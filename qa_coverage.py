#!/usr/bin/env python3
"""
QA Coverage Checker — Test Plan → Epic → BE/FE PRs → Coverage Report
----------------------------------------------------------------------

Flow:
  1. User provides a YouTrack test plan ticket URL or ID
  2. Tool finds its parent epic
  3. Fetches ALL sub-issues of the epic
  4. Classifies them: Test Scenarios (from test plan) vs BE tickets vs FE tickets
  5. Searches GitHub PRs for every BE/FE ticket
  6. Maps which test scenarios are covered by BE and/or FE based on PR status

Usage:
  qacov https://realbrokerage.youtrack.cloud/issue/RV2-12345
  qacov RV2-12345
  qacov RV2-12345 --epic RV2-10000   (if epic lookup fails, provide directly)

Output: Two tables
  [1] Test Scenarios  → Ticket | Scenario | BE tickets+PR | FE tickets+PR | Status
  [2] Implementation  → Ticket | Summary  | Type | PR | State
"""

import argparse
import os
import re
import sys
from urllib.parse import urlparse

import requests
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.rule import Rule

# ── Config ────────────────────────────────────────────────────────────────────
YOUTRACK_BASE  = "https://realbrokerage.youtrack.cloud"
GITHUB_API     = "https://api.github.com"
GITHUB_REPOS   = ["Realtyka/bolt", "manepranal/bolt-rest-assured"]

YOUTRACK_TOKEN = os.environ.get("YOUTRACK_TOKEN", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")

# Keywords to classify sub-issues
BE_KEYWORDS   = ["[be]", "be -", "be:", "backend", "api", "rest", "service",
                  "server", "endpoint", "microservice", "java"]
FE_KEYWORDS   = ["[fe]", "fe -", "fe:", "frontend", "ui", "playwright", "e2e",
                  "react", "web", "typescript", "component", "screen", "page"]
TEST_KEYWORDS = ["test plan", "qa plan", "test case", "test scenario", "qc",
                 "test suite", "testing", "acceptance criteria", "scenario"]

console = Console()


# ── YouTrack API ──────────────────────────────────────────────────────────────

def yt_get(path, params=None):
    headers = {
        "Authorization": f"Bearer {YOUTRACK_TOKEN}",
        "Accept": "application/json",
    }
    r = requests.get(
        f"{YOUTRACK_BASE}/api{path}",
        headers=headers, params=params, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fetch_issue_with_parent(ticket_id):
    """Fetch a ticket including its parent epic reference."""
    return yt_get(
        f"/issues/{ticket_id}",
        {
            "fields": (
                "id,idReadable,summary,state(name),type(name),"
                "parent(id,idReadable,summary,state(name)),"
                "links(direction,issues(id,idReadable,summary,state(name)),"
                "      linkType(name,targetToSource,sourceToTarget))"
            )
        },
    )


def fetch_epic_with_subtasks(epic_id):
    """Fetch epic and all its sub-issues with their own links."""
    return yt_get(
        f"/issues/{epic_id}",
        {
            "fields": (
                "id,idReadable,summary,state(name),type(name),"
                "subtasks("
                "  id,idReadable,summary,state(name),type(name),"
                "  links(direction,issues(id,idReadable,summary,state(name)),"
                "        linkType(name,targetToSource,sourceToTarget))"
                "),"
                "links(direction,issues(id,idReadable,summary,state(name)),"
                "      linkType(name,targetToSource,sourceToTarget))"
            )
        },
    )


def fetch_subtasks_of(ticket_id):
    """Fetch sub-issues of a specific ticket (e.g., the test plan ticket)."""
    data = yt_get(
        f"/issues/{ticket_id}",
        {
            "fields": (
                "id,idReadable,summary,state(name),"
                "subtasks(id,idReadable,summary,state(name),"
                "  links(direction,issues(id,idReadable,summary),"
                "        linkType(name)))"
            )
        },
    )
    return data.get("subtasks", [])


def get_all_linked_ids(issue):
    """Collect all linked ticket IDs (from links array)."""
    ids = {}
    for link in (issue.get("links") or []):
        ltype = (link.get("linkType") or {}).get("name", "").lower()
        for sub in (link.get("issues") or []):
            tid = sub.get("idReadable") or sub.get("id")
            if tid:
                ids[tid] = ltype
    return ids


# ── GitHub API ────────────────────────────────────────────────────────────────

def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def search_prs(ticket_id):
    """Search GitHub for PRs referencing this ticket ID in both repos."""
    found = []
    for repo in GITHUB_REPOS:
        try:
            r = requests.get(
                f"{GITHUB_API}/search/issues",
                headers=gh_headers(),
                params={"q": f"{ticket_id} type:pr repo:{repo}", "per_page": 5},
                timeout=20,
            )
            r.raise_for_status()
            for item in r.json().get("items", []):
                pr_r = requests.get(
                    f"{GITHUB_API}/repos/{repo}/pulls/{item['number']}",
                    headers=gh_headers(), timeout=15,
                )
                merged = False
                draft  = False
                state  = item.get("state", "?")
                if pr_r.ok:
                    pr    = pr_r.json()
                    merged = bool(pr.get("merged_at"))
                    draft  = pr.get("draft", False)
                    state  = pr.get("state", state)
                found.append({
                    "repo":   repo.split("/")[-1],
                    "number": item["number"],
                    "url":    item["html_url"],
                    "state":  state,
                    "merged": merged,
                    "draft":  draft,
                })
        except requests.RequestException as e:
            console.print(f"  [dim yellow]GitHub warn ({ticket_id}@{repo}): {e}[/dim yellow]")
    return found


def pr_label(prs):
    """Short rich-formatted label for a list of PRs."""
    if not prs:
        return "[dim]—[/dim]", False
    best = sorted(prs, key=lambda p: (p["merged"], p["state"] == "open"), reverse=True)[0]
    n = best["number"]
    repo = best["repo"]
    if best["merged"]:
        return f"[green]#{n} ✓ ({repo})[/green]", True
    if best["draft"]:
        return f"[dim]#{n} Draft ({repo})[/dim]", False
    if best["state"] == "open":
        return f"[yellow]#{n} Open ({repo})[/yellow]", False
    return f"[red]#{n} Closed ({repo})[/red]", False


# ── Classification ────────────────────────────────────────────────────────────

def classify(issue):
    """Return 'BE', 'FE', 'TEST', 'BE+FE', or '?'"""
    s = (issue.get("summary") or "").lower()
    t = ((issue.get("type") or {}).get("name") or "").lower()

    is_test = any(kw in s for kw in TEST_KEYWORDS) or "test" in t
    is_be   = any(kw in s for kw in BE_KEYWORDS)
    is_fe   = any(kw in s for kw in FE_KEYWORDS)

    if is_test and not (is_be or is_fe):
        return "TEST"
    if is_be and is_fe:
        return "BE+FE"
    if is_be:
        return "BE"
    if is_fe:
        return "FE"
    return "?"


# ── URL / ID parsing ──────────────────────────────────────────────────────────

def extract_ticket_id(arg):
    """Accept full URL or bare ticket ID."""
    arg = arg.strip()
    # Full URL: https://realbrokerage.youtrack.cloud/issue/RV2-12345
    m = re.search(r'/issue/([A-Z]+-\d+)', arg, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    # Bare ID: RV2-12345
    if re.match(r'^[A-Z]+-\d+$', arg, re.IGNORECASE):
        return arg.upper()
    return None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="QA Coverage: test plan link → epic → BE/FE PRs → coverage report"
    )
    parser.add_argument(
        "test_plan",
        help="YouTrack test plan ticket URL or ID  (e.g. https://.../issue/RV2-123 or RV2-123)"
    )
    parser.add_argument(
        "--epic",
        help="Override: provide the epic ID directly if auto-detection fails"
    )
    args = parser.parse_args()

    # ── Auth check ────────────────────────────────────────────────────────────
    for name, val in [("YOUTRACK_TOKEN", YOUTRACK_TOKEN), ("GITHUB_TOKEN", GITHUB_TOKEN)]:
        if not val:
            console.print(f"[bold red]Error:[/bold red] {name} env var not set.")
            sys.exit(1)

    # ── Step 1: Parse test plan ticket ────────────────────────────────────────
    tp_id = extract_ticket_id(args.test_plan)
    if not tp_id:
        console.print(f"[red]Could not parse a YouTrack ticket ID from: {args.test_plan}[/red]")
        sys.exit(1)

    console.print(f"\n[bold cyan]Fetching test plan ticket {tp_id}...[/bold cyan]")
    try:
        tp_ticket = fetch_issue_with_parent(tp_id)
    except requests.HTTPError as e:
        console.print(f"[red]Failed to fetch {tp_id}: {e}[/red]")
        sys.exit(1)

    tp_summary = tp_ticket.get("summary", "")
    console.print(Panel(
        f"[bold]{tp_id}[/bold] — {tp_summary}",
        title="Test Plan Ticket",
        border_style="cyan",
    ))

    # ── Step 2: Find parent epic ───────────────────────────────────────────────
    if args.epic:
        epic_id = args.epic.upper()
    else:
        parent = tp_ticket.get("parent")
        if parent:
            epic_id = parent.get("idReadable") or parent.get("id")
        else:
            # Try links — look for "parent" or "subtask of" type link
            epic_id = None
            for link in (tp_ticket.get("links") or []):
                ltype = (link.get("linkType") or {}).get("name", "").lower()
                target = (link.get("linkType") or {}).get("targetToSource", "").lower()
                source = (link.get("linkType") or {}).get("sourceToTarget", "").lower()
                is_parent = any(kw in ltype + target + source
                                for kw in ["parent", "subtask of", "child of", "part of"])
                if is_parent:
                    issues = link.get("issues") or []
                    if issues:
                        epic_id = issues[0].get("idReadable") or issues[0].get("id")
                        break

        if not epic_id:
            console.print(
                "[red]Could not auto-detect parent epic.\n"
                "Use --epic RV2-XXXX to provide it directly.[/red]"
            )
            sys.exit(1)

    console.print(f"\n[bold cyan]Fetching epic {epic_id}...[/bold cyan]")
    try:
        epic = fetch_epic_with_subtasks(epic_id)
    except requests.HTTPError as e:
        console.print(f"[red]Failed to fetch epic {epic_id}: {e}[/red]")
        sys.exit(1)

    epic_summary = epic.get("summary", "")
    console.print(Panel(
        f"[bold]{epic_id}[/bold] — {epic_summary}\n"
        f"[dim]State: {(epic.get('state') or {}).get('name', '?')}[/dim]",
        title="Parent Epic",
        border_style="magenta",
    ))

    # ── Step 3: Classify all epic sub-issues ──────────────────────────────────
    all_subs = epic.get("subtasks") or []

    be_tickets   = []
    fe_tickets   = []
    test_tickets = []
    other        = []

    for sub in all_subs:
        c = classify(sub)
        if c in ("BE", "BE+FE"):
            be_tickets.append(sub)
            if c == "BE+FE":
                fe_tickets.append(sub)
        elif c == "FE":
            fe_tickets.append(sub)
        elif c == "TEST":
            test_tickets.append(sub)
        else:
            # Unclassified — keep for context
            other.append(sub)

    # The test plan ticket's sub-issues = the actual test scenarios
    console.print(f"\n[dim]Fetching test scenarios from {tp_id}...[/dim]")
    scenarios = fetch_subtasks_of(tp_id)

    # If no sub-issues on test plan ticket, fall back to test_tickets from epic
    if not scenarios and test_tickets:
        scenarios = test_tickets
        console.print(f"[dim](No sub-issues on test plan ticket — using test-classified sub-issues from epic)[/dim]")

    console.print(
        f"[dim]Found: {len(scenarios)} test scenario(s), "
        f"{len(be_tickets)} BE ticket(s), {len(fe_tickets)} FE ticket(s)[/dim]"
    )

    # ── Step 4: Fetch PRs for all BE + FE tickets ─────────────────────────────
    console.print(f"\n[dim]Searching GitHub PRs...[/dim]")

    impl_tickets = list({t["idReadable"]: t for t in (be_tickets + fe_tickets)}.values())
    pr_map = {}  # ticket_id → list of PR dicts

    for ticket in impl_tickets:
        tid = ticket.get("idReadable") or ticket.get("id", "?")
        console.print(f"  [cyan]{tid}[/cyan] [{classify(ticket)}] — searching...", end="")
        prs = search_prs(tid)
        pr_map[tid] = prs
        console.print(f" {len(prs)} PR(s)")

    # ── Step 5: Build scenario ↔ impl ticket mapping ──────────────────────────
    # For each scenario, find linked BE/FE tickets (via YouTrack links)
    be_ids = {t.get("idReadable") or t.get("id") for t in be_tickets}
    fe_ids = {t.get("idReadable") or t.get("id") for t in fe_tickets}

    # ── Step 6: Print TEST SCENARIOS table ───────────────────────────────────
    console.print()
    console.print(Rule("[bold white]TEST SCENARIOS[/bold white]", style="cyan"))

    scen_table = Table(
        box=box.ROUNDED, show_header=True, header_style="bold magenta", expand=True,
        title=f"[bold]{tp_id}[/bold] — {tp_summary[:60]}",
    )
    scen_table.add_column("Ticket",   style="cyan bold", no_wrap=True, min_width=10)
    scen_table.add_column("Scenario", max_width=45)
    scen_table.add_column("State",    justify="center", min_width=12)
    scen_table.add_column("BE Tickets & PR",  min_width=22)
    scen_table.add_column("FE Tickets & PR",  min_width=22)
    scen_table.add_column("Coverage",  justify="center", min_width=10)

    scen_stats = {"full": 0, "partial": 0, "missing": 0, "no_link": 0}

    for scen in scenarios:
        sid      = scen.get("idReadable") or scen.get("id", "?")
        s_sum    = (scen.get("summary") or "")[:45]
        s_state  = (scen.get("state") or {}).get("name", "?")
        linked   = get_all_linked_ids(scen)

        # Linked BE/FE tickets for this specific scenario
        linked_be = [t for t in be_tickets
                     if (t.get("idReadable") or t.get("id")) in linked]
        linked_fe = [t for t in fe_tickets
                     if (t.get("idReadable") or t.get("id")) in linked]

        # If scenario has no direct links, fall back to all epic BE/FE tickets
        # (they all collectively "cover" every scenario)
        be_source = linked_be if linked_be else be_tickets
        fe_source = linked_fe if linked_fe else fe_tickets
        is_inferred = not linked_be and not linked_fe

        def build_cell(tickets):
            if not tickets:
                return "[dim]—[/dim]", False
            lines = []
            any_merged = False
            for t in tickets:
                tid = t.get("idReadable") or t.get("id", "?")
                label, merged = pr_label(pr_map.get(tid, []))
                lines.append(f"{tid}: {label}")
                if merged:
                    any_merged = True
            return "\n".join(lines), any_merged

        be_cell, be_ok = build_cell(be_source)
        fe_cell, fe_ok = build_cell(fe_source)

        if is_inferred:
            # Prefix a note that this is epic-level, not scenario-specific
            be_cell = "[dim](epic-level)[/dim]\n" + be_cell
            fe_cell = "[dim](epic-level)[/dim]\n" + fe_cell

        if be_ok and fe_ok:
            cov = "[green]Full ✓[/green]"
            scen_stats["full"] += 1
        elif be_ok or fe_ok:
            cov = "[yellow]Partial[/yellow]"
            scen_stats["partial"] += 1
        elif not be_source and not fe_source:
            cov = "[dim]No impl[/dim]"
            scen_stats["no_link"] += 1
        else:
            cov = "[red]Missing[/red]"
            scen_stats["missing"] += 1

        scen_table.add_row(sid, s_sum, s_state, be_cell, fe_cell, cov)

    if scenarios:
        console.print(scen_table)
    else:
        console.print("[yellow]No test scenarios found on the test plan ticket.[/yellow]")
        console.print("[dim]Tip: Add sub-issues to the test plan ticket for scenario-level tracking.[/dim]")

    # ── Step 7: Print IMPLEMENTATION table ───────────────────────────────────
    console.print()
    console.print(Rule("[bold white]IMPLEMENTATION STATUS[/bold white]", style="magenta"))

    impl_table = Table(
        box=box.ROUNDED, show_header=True, header_style="bold magenta", expand=True,
        title=f"[bold]{epic_id}[/bold] — {epic_summary[:60]} — BE/FE Tickets",
    )
    impl_table.add_column("Ticket",  style="cyan bold", no_wrap=True, min_width=10)
    impl_table.add_column("Summary", max_width=50)
    impl_table.add_column("Type",    justify="center", min_width=6)
    impl_table.add_column("State",   justify="center", min_width=12)
    impl_table.add_column("GitHub PR", min_width=28)
    impl_table.add_column("Status",  justify="center", min_width=11)

    impl_stats = {"merged": 0, "open": 0, "no_pr": 0}

    for ticket in impl_tickets:
        tid    = ticket.get("idReadable") or ticket.get("id", "?")
        t_sum  = (ticket.get("summary") or "")[:50]
        t_state = (ticket.get("state") or {}).get("name", "?")
        c      = classify(ticket)
        prs    = pr_map.get(tid, [])

        type_cell = (
            "[blue]BE[/blue]"       if c == "BE"    else
            "[magenta]FE[/magenta]" if c == "FE"    else
            "[cyan]BE+FE[/cyan]"    if c == "BE+FE" else
            "[dim]?[/dim]"
        )

        if not prs:
            pr_cell  = "[dim]No PR found[/dim]"
            stat_cell = "[red]No PR[/red]"
            impl_stats["no_pr"] += 1
        else:
            pr_lines = []
            any_merged = False
            for pr in prs:
                n = pr["number"]
                repo = pr["repo"]
                url  = pr["url"]
                if pr["merged"]:
                    pr_lines.append(f"[green]#{n} Merged ({repo})[/green]")
                    any_merged = True
                elif pr["draft"]:
                    pr_lines.append(f"[dim]#{n} Draft ({repo})[/dim]")
                elif pr["state"] == "open":
                    pr_lines.append(f"[yellow]#{n} Open ({repo})[/yellow]")
                else:
                    pr_lines.append(f"[red]#{n} Closed ({repo})[/red]")
            pr_cell = "\n".join(pr_lines)
            if any_merged:
                stat_cell = "[green]Done ✓[/green]"
                impl_stats["merged"] += 1
            else:
                stat_cell = "[yellow]In Progress[/yellow]"
                impl_stats["open"] += 1

        impl_table.add_row(tid, t_sum, type_cell, t_state, pr_cell, stat_cell)

    if impl_tickets:
        console.print(impl_table)
    else:
        console.print(
            "[yellow]No BE or FE tickets found as sub-issues of the epic.[/yellow]\n"
            "[dim]Tip: Name sub-issues with [BE] / [FE] prefix or backend/frontend keywords.[/dim]"
        )

    # ── Step 8: Summary panel ─────────────────────────────────────────────────
    total_scen = len(scenarios)
    total_impl = len(impl_tickets)

    summary_lines = [
        f"[bold]Test Scenarios ({total_scen}):[/bold]",
        f"  [green]✓ Full coverage:[/green]   {scen_stats['full']}",
        f"  [yellow]~ Partial:[/yellow]         {scen_stats['partial']}",
        f"  [red]✗ Missing:[/red]         {scen_stats['missing']}",
        "",
        f"[bold]Implementation ({total_impl} tickets):[/bold]",
        f"  [green]✓ PRs merged:[/green]      {impl_stats['merged']}",
        f"  [yellow]~ PRs open:[/yellow]        {impl_stats['open']}",
        f"  [red]✗ No PR yet:[/red]       {impl_stats['no_pr']}",
    ]

    if total_impl > 0:
        pct = int((impl_stats["merged"] / total_impl) * 100)
        all_merged = impl_stats["merged"] == total_impl
        color = "green" if all_merged else ("yellow" if impl_stats["merged"] > 0 else "red")
        summary_lines += [
            "",
            f"[{color}][bold]Implementation complete: {pct}%[/bold][/{color}]",
        ]
        if all_merged:
            summary_lines.append("[green bold]→ All PRs merged. QA testing can begin![/green bold]")
        else:
            pending = impl_stats["open"] + impl_stats["no_pr"]
            summary_lines.append(f"[yellow]→ {pending} ticket(s) still pending.[/yellow]")

    console.print()
    console.print(Panel(
        "\n".join(summary_lines),
        title=f"[bold white]Coverage Summary — {epic_id}[/bold white]",
        border_style="cyan",
    ))
    console.print()


if __name__ == "__main__":
    main()
