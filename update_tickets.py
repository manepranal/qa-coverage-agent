#!/usr/bin/env python3
"""
QA Ticket Updater
-----------------
After running a coverage check, this tool:
  1. Identifies covered vs not-covered test cases on the board
  2. Shows a dry-run of what comment will be posted and what state each ticket will move to
  3. Asks for confirmation before making any changes
  4. Posts comments and updates Stage to Pass (covered) or Fail (not covered)

Usage:
  qaupdate https://realbrokerage.youtrack.cloud/agiles/124-416
  qaupdate 124-416
  qaupdate 124-416 --epic RV2-61965          # override epic
  qaupdate 124-416 --covered-only            # only update covered tickets (skip Fail)
  qaupdate 124-416 --dry-run                 # show plan without making changes
"""

import argparse
import os
import re
import sys
import time

import requests
from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel

# ── Config ────────────────────────────────────────────────────────────────────
YOUTRACK_BASE = "https://realbrokerage.youtrack.cloud"
GITHUB_API    = "https://api.github.com"
GITHUB_REPOS  = ["Realtyka/bolt", "manepranal/bolt-rest-assured"]

YOUTRACK_TOKEN = os.environ.get("YOUTRACK_TOKEN", "")
GITHUB_TOKEN   = os.environ.get("GITHUB_TOKEN", "")

# YouTrack Stage field and state IDs (QA project bundle)
STAGE_FIELD_ID = "114-228"
PASS_STATE_ID  = "97-366"
FAIL_STATE_ID  = "97-367"

# Keywords to classify test cases
BE_TEST_KEYWORDS = ["domain logic", "be-", "be unit", "be service", "be integration",
                    "duplicate prevention", "api", "backend", "endpoint", "[be]"]
FE_TEST_KEYWORDS = ["[ui]", "fe-", "fe unit", "fe component", "fe integration",
                    "frontend", "component", "visual", "button", "banner", "screen"]

# Keywords to classify implementation tickets
BE_IMPL_KEYWORDS = ["[be]", "be:", "be -", "be :", "backend", "api", "rest", "service",
                    "server", "endpoint", "yenta", "sherlock", "java"]
FE_IMPL_KEYWORDS = ["[fe]", "fe:", "fe -", "fe :", "bolt:", "bolt -", "bolt :",
                    "frontend", "ui", "playwright", "e2e", "react", "web", "typescript", "component"]

console = Console(width=150)


# ── YouTrack API ──────────────────────────────────────────────────────────────

def yt_get(path, params=None):
    headers = {"Authorization": f"Bearer {YOUTRACK_TOKEN}", "Accept": "application/json"}
    r = requests.get(f"{YOUTRACK_BASE}/api{path}", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def yt_post(path, payload):
    headers = {"Authorization": f"Bearer {YOUTRACK_TOKEN}",
               "Accept": "application/json", "Content-Type": "application/json"}
    r = requests.post(f"{YOUTRACK_BASE}/api{path}", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r


def get_board(agile_id):
    return yt_get(f"/agiles/{agile_id}",
                  {"fields": "id,name,currentSprint(id,name),sprints(id,name)"})


def get_sprint_issues(agile_id, sprint_id):
    data = yt_get(f"/agiles/{agile_id}/sprints/{sprint_id}",
                  {"fields": "id,name,issues(id,idReadable,summary,"
                             "customFields(name,value(name,text)))"})
    return data.get("issues", [])


def get_epic_subtasks(epic_id):
    epic = yt_get(f"/issues/{epic_id}", {"fields": "id,idReadable,summary,state(name)"})
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
                state = item.get("state", "?")
                if pr_r.ok:
                    pr = pr_r.json()
                    merged = bool(pr.get("merged_at"))
                    draft  = pr.get("draft", False)
                    state  = pr.get("state", state)
                found.append({"repo": repo.split("/")[-1], "number": item["number"],
                              "state": state, "merged": merged, "draft": draft})
        except requests.RequestException:
            pass
    return found


# ── Classification ────────────────────────────────────────────────────────────

def classify_test(summary):
    s = summary.lower()
    is_be = any(k in s for k in BE_TEST_KEYWORDS)
    is_fe = any(k in s for k in FE_TEST_KEYWORDS)
    if is_be and not is_fe:
        return "BE"
    if is_fe and not is_be:
        return "FE"
    return "E2E"


def classify_impl(summary):
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
    m = re.search(r'([A-Z][A-Z0-9]+-\d+)', board_name)
    return m.group(1) if m else None


def parse_board_id(arg):
    m = re.search(r'/agiles/([^/?]+)', arg)
    return m.group(1) if m else arg.strip()


def best_pr_summary(prs):
    """Return a short human-readable PR summary string."""
    if not prs:
        return "No PR"
    best = sorted(prs, key=lambda p: (p["merged"], p["state"] == "open"), reverse=True)[0]
    status = "merged" if best["merged"] else ("draft" if best["draft"] else best["state"])
    return f"#{best['number']} {status} ({best['repo']})"


# ── Comment builder ───────────────────────────────────────────────────────────

def build_comment(category, be_prs, fe_prs, covered):
    if covered:
        lines = ["\u2705 Implementation coverage verified \u2014 moving to Pass."]
        if category in ("BE", "E2E") and be_prs:
            pr = best_pr_summary(be_prs)
            lines.append(f"- BE: PR {pr}")
        if category in ("FE", "E2E") and fe_prs:
            pr = best_pr_summary(fe_prs)
            lines.append(f"- FE: PR {pr}")
        if category == "E2E":
            lines.append("All required BE and FE implementation PRs exist.")
        elif category in ("BE", "FE"):
            lines.append("Required implementation PR exists. QA testing can proceed.")
    else:
        lines = ["\u274c Implementation not yet covered \u2014 moving to Fail."]
        if category in ("BE", "E2E") and not be_prs:
            lines.append("- BE: No PR found")
        if category in ("FE", "E2E") and not fe_prs:
            lines.append("- FE: No PR found")
        lines.append("QA testing cannot proceed until implementation PRs are created.")
    return "\n".join(lines)


# ── YouTrack updater ──────────────────────────────────────────────────────────

def post_comment(ticket_id, text):
    return yt_post(f"/issues/{ticket_id}/comments", {"text": text})


def set_stage(ticket_id, state_id):
    return yt_post(f"/issues/{ticket_id}/fields/{STAGE_FIELD_ID}",
                   {"value": {"id": state_id, "$type": "StateBundleElement"}})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="QA Ticket Updater \u2014 comment + move tickets to Pass/Fail based on coverage"
    )
    parser.add_argument("board", help="Board ID or full board URL")
    parser.add_argument("--epic", help="Override epic ID")
    parser.add_argument("--covered-only", action="store_true",
                        help="Only update covered tickets (skip moving uncovered to Fail)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan only, do not make any changes")
    args = parser.parse_args()

    for name, val in [("YOUTRACK_TOKEN", YOUTRACK_TOKEN), ("GITHUB_TOKEN", GITHUB_TOKEN)]:
        if not val:
            console.print(f"[bold red]Error:[/bold red] {name} not set.")
            sys.exit(1)

    agile_id = parse_board_id(args.board)

    # ── Step 1: Board + sprint ────────────────────────────────────────────────
    console.print(f"\n[bold cyan]Fetching board {agile_id}...[/bold cyan]")
    board      = get_board(agile_id)
    board_name = board["name"]
    sprint     = board.get("currentSprint") or (board.get("sprints") or [{}])[0]
    sprint_id  = sprint.get("id")
    sprint_name = sprint.get("name", "?")

    console.print(Panel(f"[bold]{board_name}[/bold]\nSprint: {sprint_name}",
                        title="QA Test Plan Board", border_style="cyan"))

    if not sprint_id:
        console.print("[red]No sprint found on this board.[/red]")
        sys.exit(1)

    # ── Step 2: Test cases ────────────────────────────────────────────────────
    console.print("[dim]Fetching test cases...[/dim]")
    all_issues = get_sprint_issues(agile_id, sprint_id)
    test_cases = []
    for issue in all_issues:
        if (get_custom_field(issue, "Type") or "").lower() == "test":
            test_cases.append({
                "id":       issue.get("idReadable") or issue.get("id"),
                "summary":  issue.get("summary", ""),
                "stage":    get_custom_field(issue, "Stage") or "?",
                "category": classify_test(issue.get("summary", "")),
            })
    console.print(f"[dim]Found {len(test_cases)} test case(s).[/dim]\n")

    # ── Step 3: Epic + sub-issues ─────────────────────────────────────────────
    epic_id = args.epic or extract_epic_id(board_name)
    if not epic_id:
        console.print("[red]Could not extract epic ID. Use --epic RV2-XXXXX.[/red]")
        sys.exit(1)

    console.print(f"[bold cyan]Fetching epic {epic_id}...[/bold cyan]")
    epic_data = get_epic_subtasks(epic_id)
    subtasks  = epic_data.get("subtasks") or []

    # ── Step 4: PR lookup ─────────────────────────────────────────────────────
    console.print("[dim]Fetching GitHub PRs for implementation tickets...[/dim]")
    be_tickets, fe_tickets = [], []

    for sub in subtasks:
        tid  = sub.get("idReadable") or sub.get("id", "?")
        summ = sub.get("summary", "")
        pr_field = get_custom_field(sub, "Pull Request") or ""
        c = classify_impl(summ)

        console.print(f"  [cyan]{tid}[/cyan] [{c}] \u2014 searching PRs...", end="")
        prs = search_prs(tid)

        if pr_field and pr_field.lower() not in ("na", "n/a", "see dev notes", ""):
            urls = re.findall(r'https?://github\.com/[^/]+/([^/]+)/pull/(\d+)', pr_field)
            for repo_name, pr_num in urls:
                if not any(p["number"] == int(pr_num) for p in prs):
                    prs.append({"repo": repo_name, "number": int(pr_num),
                                "state": "open", "merged": False, "draft": False})

        console.print(f" {len(prs)} PR(s)")
        entry = {"id": tid, "summary": summ[:55], "prs": prs}
        if "BE" in c:
            be_tickets.append(entry)
        if "FE" in c or c == "?":
            fe_tickets.append(entry)

    be_has_pr = any(t["prs"] for t in be_tickets)
    fe_has_pr = any(t["prs"] for t in fe_tickets)

    # Collect all BE/FE PRs for comment building
    all_be_prs = [p for t in be_tickets for p in t["prs"]]
    all_fe_prs = [p for t in fe_tickets for p in t["prs"]]

    # ── Step 5: Build plan ────────────────────────────────────────────────────
    plan = []
    for tc in test_cases:
        cat = tc["category"]
        be_needed = (cat != "FE")
        fe_needed = (cat != "BE")
        be_ok = (not be_needed) or be_has_pr
        fe_ok = (not fe_needed) or fe_has_pr
        covered = be_ok and fe_ok

        if not covered and args.covered_only:
            continue

        be_prs = all_be_prs if be_needed else []
        fe_prs = all_fe_prs if fe_needed else []

        plan.append({
            "id":       tc["id"],
            "summary":  tc["summary"][:70],
            "category": cat,
            "stage":    tc["stage"],
            "covered":  covered,
            "new_state": "Pass" if covered else "Fail",
            "state_id":  PASS_STATE_ID if covered else FAIL_STATE_ID,
            "comment":   build_comment(cat, be_prs, fe_prs, covered),
        })

    # ── Step 6: Show dry-run table ────────────────────────────────────────────
    console.print()
    tbl = Table(box=box.SIMPLE_HEAD, header_style="bold white",
                show_header=True, show_edge=True, pad_edge=True, padding=(0, 1))
    tbl.add_column("Ticket",       style="bold cyan", width=8,  no_wrap=True)
    tbl.add_column("Type",         justify="center",  width=5,  no_wrap=True)
    tbl.add_column("Current Stage",justify="center",  width=13, no_wrap=True)
    tbl.add_column("New Stage",    justify="center",  width=10, no_wrap=True)
    tbl.add_column("Comment Preview",                 width=60, no_wrap=True, overflow="ellipsis")

    for row in plan:
        new_stage_cell = (
            "[green]Pass[/green]" if row["new_state"] == "Pass" else "[red]Fail[/red]"
        )
        tbl.add_row(
            row["id"],
            row["category"],
            row["stage"],
            new_stage_cell,
            row["comment"].replace("\n", " | "),
        )

    console.print(Panel(f"[bold]Planned Updates \u2014 {len(plan)} ticket(s)[/bold]",
                        border_style="cyan", padding=(0, 2)))
    console.print(tbl)

    pass_count = sum(1 for r in plan if r["new_state"] == "Pass")
    fail_count = sum(1 for r in plan if r["new_state"] == "Fail")
    console.print(f"\n  [green]\u2192 Pass: {pass_count}[/green]   [red]\u2192 Fail: {fail_count}[/red]\n")

    if args.dry_run:
        console.print("[yellow]Dry-run mode \u2014 no changes made.[/yellow]")
        return

    # ── Step 7: Confirm ───────────────────────────────────────────────────────
    confirm = input("Proceed? Post comments and update ticket stages? [y/N]: ").strip().lower()
    if confirm != "y":
        console.print("[yellow]Aborted.[/yellow]")
        return

    # ── Step 8: Execute ───────────────────────────────────────────────────────
    console.print()
    errors = []
    for row in plan:
        tid = row["id"]
        comment_ok = state_ok = False

        try:
            post_comment(tid, row["comment"])
            comment_ok = True
        except Exception as e:
            errors.append(f"{tid} comment: {e}")

        try:
            set_stage(tid, row["state_id"])
            state_ok = True
        except Exception as e:
            errors.append(f"{tid} stage: {e}")

        status = "[green]\u2713[/green]" if (comment_ok and state_ok) else "[red]\u2717[/red]"
        new_stage = f"[green]Pass[/green]" if row["new_state"] == "Pass" else "[red]Fail[/red]"
        console.print(f"  {status} {tid} \u2014 comment: {'\u2713' if comment_ok else '\u2717'}  "
                      f"stage \u2192 {new_stage}: {'\u2713' if state_ok else '\u2717'}")
        time.sleep(0.3)

    console.print()
    if errors:
        console.print("[red]Errors:[/red]")
        for e in errors:
            console.print(f"  [red]{e}[/red]")
    else:
        console.print(Panel(
            f"[green bold]\u2713 Done. {pass_count} ticket(s) moved to Pass, "
            f"{fail_count} ticket(s) moved to Fail.[/green bold]",
            border_style="green"
        ))


if __name__ == "__main__":
    main()
