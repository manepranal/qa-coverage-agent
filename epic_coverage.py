#!/usr/bin/env python3
"""
Epic Coverage Checker
---------------------
Given a YouTrack epic, fetches all BE/FE sub-issues, finds their GitHub PRs,
and cross-references against the QA test plan board (124-416).

Flow:
  Epic → sub-issues (BE / FE)
       → GitHub PRs for each sub-issue (searched in bolt + bolt-rest-assured)
       → PR status (Merged / Open / Draft / Closed / None)
       → Cross-reference with test plan board 124-416

Usage:
  epicov <EPIC-ID> [--board 124-416]

Examples:
  epicov RV2-12345
  epicov RV2-12345 --board 124-416
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
from rich.text import Text

# ── Config ────────────────────────────────────────────────────────────────────
YOUTRACK_BASE  = "https://realbrokerage.youtrack.cloud"
GITHUB_API     = "https://api.github.com"
DEFAULT_BOARD  = "124-416"

# Repos to search for PRs (ticket ID in PR title/body/branch)
GITHUB_REPOS   = ["Realtyka/bolt", "manepranal/bolt-rest-assured"]

YOUTRACK_TOKEN = os.environ.get("YOUTRACK_TOKEN", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")

# Keywords to classify sub-issues as BE or FE
BE_KEYWORDS = ["[be]", "be -", "be:", "be :", "backend", "api", "rest", "service", "server", "endpoint", "yenta", "sherlock", "java"]
FE_KEYWORDS = ["[fe]", "fe -", "fe:", "fe :", "bolt:", "bolt -", "bolt :", "frontend", "ui", "playwright", "e2e", "react", "web", "typescript", "component"]

console = Console()


# ── YouTrack helpers ──────────────────────────────────────────────────────────

def yt_get(path, params=None):
    headers = {
        "Authorization": f"Bearer {YOUTRACK_TOKEN}",
        "Accept": "application/json",
    }
    r = requests.get(f"{YOUTRACK_BASE}/api{path}", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_epic(epic_id):
    """Fetch the epic and all its sub-issues via search query."""
    epic = yt_get(
        f"/issues/{epic_id}",
        {"fields": "id,idReadable,summary,state(name)"},
    )
    subs = yt_get("/issues", {
        "query": f"subtask of: {epic_id}",
        "fields": "id,idReadable,summary,state(name),customFields(name,value(name,text))",
        "$top": 200,
    })
    epic["subtasks"] = subs if isinstance(subs, list) else []
    return epic


def fetch_board_issues(agile_id, board_name):
    """Fetch all issues from the test plan board (current sprint)."""
    board = yt_get(
        f"/agiles/{agile_id}",
        {"fields": "id,name,currentSprint(id,name),sprints(id,name)"},
    )
    sprint = board.get("currentSprint")
    if not sprint:
        return [], None
    sprint_name = sprint["name"]
    issues = yt_get(
        "/issues",
        {
            "query": f'Board "{board_name}": "{sprint_name}"',
            "fields": "id,idReadable,summary,links(direction,issues(id,idReadable),linkType(name))",
            "$top": 200,
        },
    )
    return issues, sprint_name


def get_linked_ticket_ids(issue):
    """Return all YouTrack ticket IDs linked to an issue."""
    ids = set()
    for link in issue.get("links", []):
        for sub in link.get("issues", []):
            tid = sub.get("idReadable") or sub.get("id")
            if tid:
                ids.add(tid)
    return ids


def get_custom_field(issue, field_name):
    for f in (issue.get("customFields") or []):
        if f.get("name", "").lower() == field_name.lower():
            val = f.get("value")
            if isinstance(val, dict):
                return val.get("name") or val.get("text", "")
            return str(val) if val is not None else None
    return None


# ── GitHub helpers ────────────────────────────────────────────────────────────

def gh_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def search_prs_for_ticket(ticket_id):
    """
    Search GitHub PRs across bolt + bolt-rest-assured for the given ticket ID.
    Returns list of dicts with: repo, number, title, url, state, merged, draft.
    """
    found = []
    for repo in GITHUB_REPOS:
        query = f"{ticket_id} type:pr repo:{repo}"
        try:
            r = requests.get(
                f"{GITHUB_API}/search/issues",
                headers=gh_headers(),
                params={"q": query, "per_page": 5},
                timeout=20,
            )
            r.raise_for_status()
            for item in r.json().get("items", []):
                # Fetch PR details to get merged/draft status
                pr_r = requests.get(
                    f"{GITHUB_API}/repos/{repo}/pulls/{item['number']}",
                    headers=gh_headers(),
                    timeout=15,
                )
                if pr_r.ok:
                    pr = pr_r.json()
                    found.append({
                        "repo":   repo,
                        "number": item["number"],
                        "title":  item["title"][:60],
                        "url":    item["html_url"],
                        "state":  pr.get("state", item["state"]),
                        "merged": bool(pr.get("merged_at")),
                        "draft":  pr.get("draft", False),
                    })
                else:
                    found.append({
                        "repo":   repo,
                        "number": item["number"],
                        "title":  item["title"][:60],
                        "url":    item["html_url"],
                        "state":  item["state"],
                        "merged": "pull_request" in item and item.get("pull_request", {}).get("merged_at") is not None,
                        "draft":  False,
                    })
        except requests.RequestException as e:
            console.print(f"[yellow]  GitHub search warning for {ticket_id} in {repo}: {e}[/yellow]")
    return found


def pr_status_cell(prs):
    """Return a rich-formatted status string for a list of PRs."""
    if not prs:
        return "[dim]No PR[/dim]", False
    parts = []
    any_merged = False
    for pr in prs:
        repo_short = pr["repo"].split("/")[-1]
        num = pr["number"]
        if pr["merged"]:
            parts.append(f"[green]#{num} Merged ({repo_short})[/green]")
            any_merged = True
        elif pr["draft"]:
            parts.append(f"[dim]#{num} Draft ({repo_short})[/dim]")
        elif pr["state"] == "open":
            parts.append(f"[yellow]#{num} Open ({repo_short})[/yellow]")
        else:
            parts.append(f"[red]#{num} Closed ({repo_short})[/red]")
    return "\n".join(parts), any_merged


# ── Classification ────────────────────────────────────────────────────────────

def classify(summary):
    s = summary.lower()
    is_be = any(kw in s for kw in BE_KEYWORDS)
    is_fe = any(kw in s for kw in FE_KEYWORDS)
    if is_be and not is_fe:
        return "BE"
    if is_fe and not is_be:
        return "FE"
    if is_be and is_fe:
        return "BE+FE"
    return "?"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Epic Coverage Checker — BE/FE sub-issues → GitHub PRs → test plan status"
    )
    parser.add_argument("epic_id", help="YouTrack epic ID, e.g. RV2-12345")
    parser.add_argument(
        "--board", default=DEFAULT_BOARD,
        help=f"Agile board ID to cross-reference test plan (default: {DEFAULT_BOARD})"
    )
    args = parser.parse_args()

    if not YOUTRACK_TOKEN:
        console.print("[bold red]Error:[/bold red] YOUTRACK_TOKEN not set.")
        sys.exit(1)
    if not GITHUB_TOKEN:
        console.print("[bold red]Error:[/bold red] GITHUB_TOKEN not set.")
        sys.exit(1)

    epic_id = args.epic_id.upper()

    # ── 1. Fetch epic ─────────────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Fetching epic {epic_id}...[/bold cyan]")
    try:
        epic = fetch_epic(epic_id)
    except requests.HTTPError as e:
        console.print(f"[red]Failed to fetch epic {epic_id}: {e}[/red]")
        sys.exit(1)

    epic_summary = epic.get("summary", "")
    epic_state   = (epic.get("state") or {}).get("name", "?")
    console.print(Panel(
        f"[bold]{epic_id}[/bold] — {epic_summary}\n[dim]State: {epic_state}[/dim]",
        title="Epic",
        border_style="cyan",
    ))

    # ── 2. Get sub-issues ─────────────────────────────────────────────────────
    subtasks = epic.get("subtasks", [])
    if not subtasks:
        console.print("[yellow]No sub-issues found on this epic.[/yellow]")
        sys.exit(0)

    console.print(f"[dim]Found {len(subtasks)} sub-issue(s). Fetching GitHub PRs...[/dim]\n")

    # ── 3. Build sub-issue → PR data ──────────────────────────────────────────
    rows = []
    for sub in subtasks:
        tid     = sub.get("idReadable") or sub.get("id", "?")
        summary = sub.get("summary", "")
        state   = (sub.get("state") or {}).get("name", "?")
        stype   = classify(summary)
        pr_field = get_custom_field(sub, "Pull Request") or ""

        console.print(f"  [cyan]{tid}[/cyan] [{stype}] — searching PRs...", end="")
        prs = search_prs_for_ticket(tid)

        # Also extract PRs from the YouTrack Pull Request custom field
        if pr_field and pr_field.lower() not in ("na", "n/a", "see dev notes", ""):
            urls = re.findall(r'https?://github\.com/[^/]+/([^/]+)/pull/(\d+)', pr_field)
            for repo_name, pr_num in urls:
                if not any(p["number"] == int(pr_num) for p in prs):
                    prs.append({"repo": repo_name, "number": int(pr_num),
                                "url": pr_field.strip(), "state": "open",
                                "merged": False, "draft": False})

        console.print(f" {len(prs)} PR(s) found")

        rows.append({
            "tid":     tid,
            "summary": summary[:55],
            "state":   state,
            "type":    stype,
            "prs":     prs,
        })

    # ── 4. Fetch test plan board ──────────────────────────────────────────────
    console.print(f"\n[dim]Fetching test plan board {args.board}...[/dim]")
    try:
        board_data = yt_get(f"/agiles/{args.board}", {"fields": "id,name,currentSprint(id,name)"})
        board_name = board_data["name"]
        board_issues, sprint_name = fetch_board_issues(args.board, board_name)
    except Exception as e:
        board_issues, sprint_name, board_name = [], None, args.board
        console.print(f"[yellow]Could not fetch board: {e}[/yellow]")

    # Map test plan tickets that link back to any sub-issue of the epic
    all_sub_ids = {r["tid"] for r in rows} | {epic_id}
    linked_plan_tickets = []
    for bi in (board_issues or []):
        linked = get_linked_ticket_ids(bi)
        if linked & all_sub_ids:
            linked_plan_tickets.append(bi.get("idReadable") or bi.get("id"))

    # ── 5. Build output table ─────────────────────────────────────────────────
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title=f"[bold white]{epic_id} — Sub-issue Coverage[/bold white]",
        expand=True,
    )
    table.add_column("Ticket", style="cyan bold", no_wrap=True, min_width=10)
    table.add_column("Summary", max_width=50)
    table.add_column("Type", justify="center", min_width=6)
    table.add_column("State", justify="center", min_width=10)
    table.add_column("GitHub PR", min_width=28)
    table.add_column("Status", justify="center", min_width=10)

    stats = {"merged": 0, "open": 0, "no_pr": 0}

    be_merged = fe_merged = be_total = fe_total = 0

    for row in rows:
        pr_cell, any_merged = pr_status_cell(row["prs"])

        # Determine row status
        if not row["prs"]:
            status = "[red]No PR[/red]"
            stats["no_pr"] += 1
        elif any_merged:
            status = "[green]Merged ✓[/green]"
            stats["merged"] += 1
        else:
            status = "[yellow]In Progress[/yellow]"
            stats["open"] += 1

        # Type color
        t = row["type"]
        type_cell = (
            "[blue]BE[/blue]"     if t == "BE"    else
            "[magenta]FE[/magenta]" if t == "FE"  else
            "[cyan]BE+FE[/cyan]"  if t == "BE+FE" else
            "[dim]?[/dim]"
        )

        # Tally for summary
        if "BE" in t:
            be_total += 1
            if any_merged:
                be_merged += 1
        if "FE" in t:
            fe_total += 1
            if any_merged:
                fe_merged += 1

        table.add_row(
            row["tid"],
            row["summary"],
            type_cell,
            row["state"],
            pr_cell,
            status,
        )

    console.print()
    console.print(table)

    # ── 6. Summary ────────────────────────────────────────────────────────────
    total = len(rows)
    console.print(f"\n[bold]PR Status Summary:[/bold]")
    console.print(f"  [green]✓ Merged:[/green]      {stats['merged']:>3} / {total}")
    console.print(f"  [yellow]~ In Progress:[/yellow] {stats['open']:>3} / {total}")
    console.print(f"  [red]✗ No PR:[/red]       {stats['no_pr']:>3} / {total}")

    console.print(f"\n[bold]By Type:[/bold]")
    if be_total:
        be_pct = int((be_merged / be_total) * 100)
        console.print(f"  [blue]BE[/blue] — {be_merged}/{be_total} merged ({be_pct}%)")
    if fe_total:
        fe_pct = int((fe_merged / fe_total) * 100)
        console.print(f"  [magenta]FE[/magenta] — {fe_merged}/{fe_total} merged ({fe_pct}%)")

    # ── 7. Test plan cross-reference ──────────────────────────────────────────
    console.print(f"\n[bold]Test Plan Board ({board_name}):[/bold]")
    if sprint_name:
        console.print(f"  Sprint: {sprint_name}")
    if linked_plan_tickets:
        console.print(f"  [green]Linked test plan tickets:[/green] {', '.join(linked_plan_tickets)}")
    else:
        console.print(f"  [dim]No test plan board tickets found linking to this epic or its sub-issues.[/dim]")
        console.print(f"  [dim]Tip: link the epic or sub-issues to the relevant board tickets in YouTrack.[/dim]")

    # Overall test readiness
    console.print()
    all_merged = stats["merged"] == total
    if all_merged:
        console.print(Panel(
            "[green bold]All sub-issues have merged PRs — implementation complete. QA testing can begin![/green bold]",
            border_style="green",
        ))
    elif stats["merged"] > 0:
        console.print(Panel(
            f"[yellow bold]Partial implementation: {stats['merged']}/{total} sub-issues merged.\n"
            f"QA can test merged areas; {stats['open'] + stats['no_pr']} sub-issue(s) still pending.[/yellow bold]",
            border_style="yellow",
        ))
    else:
        console.print(Panel(
            "[red bold]No sub-issues merged yet. Implementation in progress — testing not ready.[/red bold]",
            border_style="red",
        ))

    console.print()


if __name__ == "__main__":
    main()
