#!/usr/bin/env python3
"""
QA Coverage Checker (AI Agent)
-------------------------------
Given a YouTrack QA test plan board URL, this agent:
  1. Fetches all test cases from the board
  2. Extracts the linked epic from the board name
  3. Gets all BE and FE implementation tickets from the epic
  4. Checks GitHub PRs for every BE/FE ticket
  5. Reports which test cases are covered by BE, covered by FE, or not covered

Usage:
  coverage https://realbrokerage.youtrack.cloud/agiles/124-416
  coverage 124-416

Output:
  Table 1 — Test case coverage: Ticket | Scenario | Category | BE covered | FE covered | Status
  Table 2 — Implementation status: all BE/FE tickets with their PR status
  Summary panel — counts and list of uncovered test cases
"""

import argparse
import os
import re
import sys

import requests
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel
from rich.rule import Rule

# ── Config ────────────────────────────────────────────────────────────────────
YOUTRACK_BASE = "https://realbrokerage.youtrack.cloud"
GITHUB_API    = "https://api.github.com"
GITHUB_REPOS  = ["Realtyka/bolt", "manepranal/bolt-rest-assured"]

YOUTRACK_TOKEN = os.environ.get("YOUTRACK_TOKEN", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")

# Keywords to classify test cases
BE_TEST_KEYWORDS  = ["domain logic", "be-", "be unit", "be service", "be integration",
                     "duplicate prevention", "api", "backend", "endpoint", "[be]"]
FE_TEST_KEYWORDS  = ["[ui]", "fe-", "fe unit", "fe component", "fe integration",
                     "frontend", "component", "visual", "button", "banner", "screen"]

# Keywords to classify implementation tickets
BE_IMPL_KEYWORDS  = ["[be]", "be:", "be -", "be :", "backend", "api", "rest", "service",
                     "server", "endpoint", "yenta", "sherlock", "java"]
FE_IMPL_KEYWORDS  = ["[fe]", "fe:", "fe -", "fe :", "bolt:", "bolt -", "bolt :",
                     "frontend", "ui", "playwright", "e2e", "react", "web", "typescript", "component"]

console = Console(width=150)


# ── YouTrack API ──────────────────────────────────────────────────────────────

def yt_get(path, params=None):
    headers = {"Authorization": f"Bearer {YOUTRACK_TOKEN}", "Accept": "application/json"}
    r = requests.get(f"{YOUTRACK_BASE}/api{path}", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def get_board(agile_id):
    return yt_get(f"/agiles/{agile_id}",
                  {"fields": "id,name,currentSprint(id,name),sprints(id,name)"})


def get_sprint_issues(agile_id, sprint_id):
    """Fetch all issues in a sprint with their custom fields."""
    data = yt_get(f"/agiles/{agile_id}/sprints/{sprint_id}",
                  {"fields": "id,name,issues(id,idReadable,summary,"
                             "customFields(name,value(name,text)))"})
    return data.get("issues", [])


def get_epic_subtasks(epic_id):
    """Fetch epic details, then find all sub-issues via search."""
    epic = yt_get(f"/issues/{epic_id}",
                  {"fields": "id,idReadable,summary,state(name)"})
    # Use search to get all sub-issues (subtasks field is often limited)
    subs = yt_get("/issues", {
        "query": f"subtask of: {epic_id}",
        "fields": "id,idReadable,summary,state(name),customFields(name,value(name,text))",
        "$top": 200,
    })
    epic["subtasks"] = subs if isinstance(subs, list) else []
    return epic


def get_custom_field(issue, field_name):
    for f in (issue.get("customFields") or []):
        if f.get("name", "").lower() == field_name.lower():
            val = f.get("value")
            if isinstance(val, dict):
                return val.get("name") or val.get("text", "")
            return str(val) if val is not None else None
    return None


# ── GitHub API ────────────────────────────────────────────────────────────────

def gh_headers():
    return {"Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


def search_prs(ticket_id):
    """Search GitHub PRs in all configured repos for this ticket ID."""
    found = []
    for repo in GITHUB_REPOS:
        try:
            r = requests.get(f"{GITHUB_API}/search/issues",
                             headers=gh_headers(),
                             params={"q": f"{ticket_id} type:pr repo:{repo}", "per_page": 5},
                             timeout=20)
            r.raise_for_status()
            for item in r.json().get("items", []):
                pr_r = requests.get(f"{GITHUB_API}/repos/{repo}/pulls/{item['number']}",
                                    headers=gh_headers(), timeout=15)
                merged = draft = False
                state  = item.get("state", "?")
                if pr_r.ok:
                    pr     = pr_r.json()
                    merged = bool(pr.get("merged_at"))
                    draft  = pr.get("draft", False)
                    state  = pr.get("state", state)
                found.append({"repo": repo.split("/")[-1], "number": item["number"],
                              "url": item["html_url"], "state": state,
                              "merged": merged, "draft": draft})
        except requests.RequestException:
            pass
    return found


def pr_badge(prs):
    """Return (rich_text, is_covered) for a list of PRs."""
    if not prs:
        return "[red]✗ No PR[/red]", False
    best = sorted(prs, key=lambda p: (p["merged"], p["state"] == "open"), reverse=True)[0]
    n, repo = best["number"], best["repo"]
    if best["merged"]:
        return f"[green]✓ #{n} merged ({repo})[/green]", True
    if best["draft"]:
        return f"[dim]#{n} draft ({repo})[/dim]", False
    if best["state"] == "open":
        return f"[yellow]#{n} open ({repo})[/yellow]", False
    return f"[dim]#{n} closed ({repo})[/dim]", False


# ── Classification ────────────────────────────────────────────────────────────

def classify_test(summary):
    """Classify a board test case as BE, FE, or E2E."""
    s = summary.lower()
    is_be = any(k in s for k in BE_TEST_KEYWORDS)
    is_fe = any(k in s for k in FE_TEST_KEYWORDS)
    if is_be and not is_fe:
        return "BE"
    if is_fe and not is_be:
        return "FE"
    return "E2E"  # covers both or unclassified → treat as full-stack


def classify_impl(summary):
    """Classify an implementation ticket as BE, FE, or BE+FE."""
    s = summary.lower()
    is_be = any(k in s for k in BE_IMPL_KEYWORDS)
    is_fe = any(k in s for k in FE_IMPL_KEYWORDS)
    if is_be and is_fe:
        return "BE+FE"
    if is_be:
        return "BE"
    if is_fe:
        return "FE"
    return "?"


def extract_epic_id(board_name):
    """Extract RV2-XXXXX from board name like QA_RV2-61965_TeamAdminManagement."""
    m = re.search(r'([A-Z][A-Z0-9]+-\d+)', board_name)
    return m.group(1) if m else None


def parse_board_id(arg):
    m = re.search(r'/agiles/([^/?]+)', arg)
    return m.group(1) if m else arg.strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="QA Coverage Checker — board test cases vs BE/FE implementation PRs"
    )
    parser.add_argument("board", help="Board ID or full board URL")
    parser.add_argument("--epic", help="Override epic ID (if auto-detection from board name fails)")
    args = parser.parse_args()

    for name, val in [("YOUTRACK_TOKEN", YOUTRACK_TOKEN), ("GITHUB_TOKEN", GITHUB_TOKEN)]:
        if not val:
            console.print(f"[bold red]Error:[/bold red] {name} not set.")
            sys.exit(1)

    agile_id = parse_board_id(args.board)

    # ── Step 1: Board + sprint ────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Fetching board {agile_id}...[/bold cyan]")
    board     = get_board(agile_id)
    board_name = board["name"]
    sprint    = board.get("currentSprint") or (board.get("sprints") or [{}])[0]
    sprint_id = sprint.get("id")
    sprint_name = sprint.get("name", "?")

    console.print(Panel(
        f"[bold]{board_name}[/bold]\nSprint: {sprint_name}",
        title="QA Test Plan Board", border_style="cyan"
    ))

    if not sprint_id:
        console.print("[red]No sprint found on this board.[/red]")
        sys.exit(1)

    # ── Step 2: Board test cases ──────────────────────────────────────────────
    console.print(f"[dim]Fetching test cases from sprint...[/dim]")
    all_issues = get_sprint_issues(agile_id, sprint_id)

    # Separate group headers from actual test cases
    test_cases = []
    for issue in all_issues:
        itype = get_custom_field(issue, "Type") or ""
        stage = get_custom_field(issue, "Stage") or "?"
        mode  = get_custom_field(issue, "Automated / Manual?") or "?"
        if itype.lower() == "test":
            test_cases.append({
                "id":      issue.get("idReadable") or issue.get("id"),
                "summary": issue.get("summary", ""),
                "stage":   stage,
                "mode":    mode,
                "category": classify_test(issue.get("summary", "")),
            })

    console.print(f"[dim]Found {len(test_cases)} test case(s) on the board.[/dim]\n")

    # ── Step 3: Epic + sub-issues ─────────────────────────────────────────────
    epic_id = args.epic or extract_epic_id(board_name)
    if not epic_id:
        console.print("[red]Could not extract epic ID from board name. Use --epic RV2-XXXXX.[/red]")
        sys.exit(1)

    console.print(f"[bold cyan]Fetching epic {epic_id}...[/bold cyan]")
    epic_data = get_epic_subtasks(epic_id)
    epic_summary = epic_data.get("summary", "")
    subtasks = epic_data.get("subtasks") or []

    console.print(Panel(
        f"[bold]{epic_id}[/bold] — {epic_summary}\n"
        f"[dim]State: {(epic_data.get('state') or {}).get('name','?')}  |  Sub-issues: {len(subtasks)}[/dim]",
        title="Epic", border_style="magenta"
    ))

    # ── Step 4: Classify impl tickets + fetch PRs ─────────────────────────────
    be_tickets = []
    fe_tickets = []

    console.print("[dim]Fetching GitHub PRs for implementation tickets...[/dim]")
    pr_map = {}

    for sub in subtasks:
        tid  = sub.get("idReadable") or sub.get("id", "?")
        summ = sub.get("summary", "")
        # Also check Pull Request custom field
        pr_field = get_custom_field(sub, "Pull Request") or ""
        c = classify_impl(summ)

        console.print(f"  [cyan]{tid}[/cyan] [{c}] — searching PRs...", end="")
        prs = search_prs(tid)

        # Also extract PR numbers from the field if present
        if pr_field and pr_field.lower() not in ("na", "n/a", "see dev notes", ""):
            urls = re.findall(r'https?://github\.com/[^/]+/([^/]+)/pull/(\d+)', pr_field)
            for repo_name, pr_num in urls:
                already = any(p["number"] == int(pr_num) for p in prs)
                if not already:
                    prs.append({"repo": repo_name, "number": int(pr_num),
                                "url": pr_field.strip(), "state": "open",
                                "merged": False, "draft": False})

        pr_map[tid] = prs
        console.print(f" {len(prs)} PR(s)")

        entry = {"id": tid, "summary": summ[:55], "prs": prs,
                 "state": (sub.get("state") or {}).get("name", "?")}
        if "BE" in c:
            be_tickets.append(entry)
        if "FE" in c or c == "?":
            fe_tickets.append(entry)

    # Overall BE/FE coverage signals
    be_has_pr  = any(t["prs"] for t in be_tickets)
    fe_has_pr  = any(t["prs"] for t in fe_tickets)
    be_merged  = any(p["merged"] for t in be_tickets for p in t["prs"])
    fe_merged  = any(p["merged"] for t in fe_tickets for p in t["prs"])

    # ── Step 5: Classify every test case ─────────────────────────────────────
    def best_pr_label(tickets, needed):
        """One-line compact label for the best PR across a list of tickets."""
        if not needed:
            return "[dim]   —   [/dim]"
        all_prs = [(t["id"], p) for t in tickets for p in t["prs"]]
        if not all_prs:
            return "[red]✗  No PR[/red]"
        # Prefer merged → open → draft
        merged = [(tid, p) for tid, p in all_prs if p["merged"]]
        open_  = [(tid, p) for tid, p in all_prs if not p["merged"] and p["state"] == "open"]
        pick   = (merged or open_ or all_prs)[0]
        tid, p = pick
        n = p["number"]
        if p["merged"]:
            return f"[green]✔  #{n} merged[/green]"
        if p["draft"]:
            return f"[dim]~  #{n} draft[/dim]"
        return f"[yellow]~  #{n} open[/yellow]"

    covered_rows = []
    blocked_rows = []

    for tc in test_cases:
        cat   = tc["category"]
        stage = tc["stage"]
        be_needed = (cat != "FE")
        fe_needed = (cat != "BE")
        be_ok     = (not be_needed) or be_has_pr
        fe_ok     = (not fe_needed) or fe_has_pr
        is_covered = (stage != "Blocked") and be_ok and fe_ok
        row = {**tc, "be_needed": be_needed, "fe_needed": fe_needed, "covered": is_covered}
        (covered_rows if is_covered else blocked_rows).append(row)

    # ── Step 6: Build one clean single-line-per-row table ────────────────────
    def make_table(rows, heading_color):
        t = Table(
            box=box.SIMPLE_HEAD,
            header_style=f"bold {heading_color}",
            show_header=True,
            show_edge=True,
            pad_edge=True,
            padding=(0, 1),
        )
        t.add_column("No.",          no_wrap=True, justify="right",  style="dim",           width=3)
        t.add_column("Ticket",       no_wrap=True, justify="left",   style="bold cyan",     width=8)
        t.add_column("Type",         no_wrap=True, justify="center",                        width=5)
        t.add_column("Test Scenario",no_wrap=True, justify="left",   overflow="ellipsis",   width=64)
        t.add_column("BE",           no_wrap=True, justify="left",                          width=18)
        t.add_column("FE",           no_wrap=True, justify="left",                          width=18)
        return t

    def fill_table(t, rows):
        for i, row in enumerate(rows, 1):
            cat = row["category"]
            cat_cell = (
                "[blue] BE [/blue]"       if cat == "BE"  else
                "[magenta] FE [/magenta]" if cat == "FE"  else
                "[cyan]E2E[/cyan]"
            )
            t.add_row(
                str(i),
                row["id"],
                cat_cell,
                row["summary"],
                best_pr_label(be_tickets, row["be_needed"]),
                best_pr_label(fe_tickets, row["fe_needed"]),
            )

    # ── Step 7: COVERED table ─────────────────────────────────────────────────
    n_cov = len(covered_rows)
    n_tot = len(test_cases)
    console.print()
    console.print(Panel(
        f"[bold green] ✔  COVERED — {n_cov} of {n_tot} test cases [/bold green]",
        style="green", padding=(0, 2)
    ))
    if covered_rows:
        tbl = make_table(covered_rows, "green")
        fill_table(tbl, covered_rows)
        console.print(tbl)
    else:
        console.print("  [yellow]No test cases are currently covered.[/yellow]\n")

    # ── Step 8: NOT COVERED table ─────────────────────────────────────────────
    n_blk = len(blocked_rows)
    console.print()
    console.print(Panel(
        f"[bold red] ✘  NOT COVERED / BLOCKED — {n_blk} of {n_tot} test cases [/bold red]",
        style="red", padding=(0, 2)
    ))
    if blocked_rows:
        tbl = make_table(blocked_rows, "red")
        fill_table(tbl, blocked_rows)
        console.print(tbl)
    else:
        console.print("  [green]All test cases are covered![/green]\n")

    # ── Step 9: Implementation tickets (BE then FE) ───────────────────────────
    console.print()
    console.print(Panel(
        f"[bold white] IMPLEMENTATION TICKETS — {epic_id} [/bold white]",
        style="white", padding=(0, 2)
    ))

    impl_table = Table(
        box=box.SIMPLE_HEAD,
        header_style="bold white",
        show_header=True,
        show_edge=True,
        pad_edge=True,
        padding=(0, 1),
    )
    impl_table.add_column("Ticket",  no_wrap=True, style="bold cyan",   width=10)
    impl_table.add_column("Type",    no_wrap=True, justify="center",    width=6)
    impl_table.add_column("Summary", no_wrap=True, overflow="ellipsis", width=55)
    impl_table.add_column("PR",      no_wrap=True,                      width=24)
    impl_table.add_column("Status",  no_wrap=True, justify="center",    width=13)

    all_impl = list({t["id"]: t for t in (be_tickets + fe_tickets)}.values())
    all_impl.sort(key=lambda t: (0 if "BE" in classify_impl(t["summary"])
                                  else 1 if "FE" in classify_impl(t["summary"])
                                  else 2))

    for t in all_impl:
        c = classify_impl(t["summary"])
        type_cell = (
            "[blue] BE [/blue]"       if c == "BE"    else
            "[magenta] FE [/magenta]" if c == "FE"    else
            "[cyan]BE+FE[/cyan]"      if c == "BE+FE" else
            "[dim]  ?  [/dim]"
        )
        badge, is_merged = pr_badge(t["prs"])
        stat = (
            "[green]Merged ✓[/green]"    if is_merged else
            "[yellow]Open / WIP[/yellow]" if t["prs"]  else
            "[red]No PR ✗[/red]"
        )
        summ = t["summary"]
        if len(summ) > 51:
            summ = summ[:48] + "..."
        impl_table.add_row(t["id"], type_cell, summ, badge, stat)

    console.print(impl_table)

    # ── Step 10: Summary panel ────────────────────────────────────────────────
    total   = len(test_cases)
    n_cov   = len(covered_rows)
    n_not   = len(blocked_rows)
    pct     = int((n_cov / total) * 100) if total else 0
    color   = "green" if pct == 100 else ("yellow" if pct >= 50 else "red")

    be_covered_count = sum(1 for r in covered_rows if r["category"] in ("BE", "E2E"))
    fe_covered_count = sum(1 for r in covered_rows if r["category"] in ("FE", "E2E"))
    be_blocked_count = sum(1 for r in blocked_rows if r["category"] in ("BE", "E2E"))
    fe_blocked_count = sum(1 for r in blocked_rows if r["category"] in ("FE", "E2E"))

    summary_lines = [
        f"  {'Metric':<32} {'Count':>6}",
        f"  {'─'*40}",
        f"  {'Total test cases':<32} {total:>6}",
        f"  [green]{'✓  Covered':<32} {n_cov:>6}[/green]",
        f"  [green]{'   · BE covered (BE + E2E)':<32} {be_covered_count:>6}[/green]",
        f"  [green]{'   · FE covered (FE + E2E)':<32} {fe_covered_count:>6}[/green]",
        f"  [red]{'✗  Not Covered / Blocked':<32} {n_not:>6}[/red]",
        f"  [red]{'   · BE not covered':<32} {be_blocked_count:>6}[/red]",
        f"  [red]{'   · FE not covered':<32} {fe_blocked_count:>6}[/red]",
        f"  {'─'*40}",
        f"  [{color}][bold]{'Coverage Rate':<32} {pct:>5}%[/bold][/{color}]",
    ]

    if n_not > 0:
        summary_lines += [
            "",
            "  [bold red]Remaining (not covered):[/bold red]",
        ]
        for r in blocked_rows:
            cat = r["category"]
            tag = "[blue]BE[/blue]" if cat=="BE" else "[magenta]FE[/magenta]" if cat=="FE" else "[cyan]E2E[/cyan]"
            summary_lines.append(
                f"  {tag}  [cyan]{r['id']}[/cyan]  [dim]{r['summary'][:55]}[/dim]  "
                f"[yellow]({r['stage']})[/yellow]"
            )

    if n_not == 0:
        summary_lines.append("\n  [green bold]✓ All test cases covered — QA testing can begin![/green bold]")

    console.print()
    console.print(Panel(
        "\n".join(summary_lines),
        title=f"[bold white]Coverage Summary — {epic_id}  {epic_summary[:40]}[/bold white]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()


if __name__ == "__main__":
    main()
