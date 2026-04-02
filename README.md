# QA Coverage Agent

CLI tools that automatically check how many test cases on a YouTrack QA test plan board are covered by BE and FE implementation tickets — and clearly shows which are not.

## Tools

| Alias | Script | Purpose |
|-------|--------|---------|
| `coverage` | `coverage_check.py` | **Primary tool.** Board URL → test cases → epic → impl PRs → coverage report |
| `qaupdate` | `update_tickets.py` | Board URL → dry-run plan → confirm → post comments + move tickets to Pass/Fail |
| `qacov` | `qa_coverage.py` | Test plan ticket → parent epic → impl PRs → scenario-level report |
| `epicov` | `epic_coverage.py` | Epic → sub-issues → PRs → test plan board cross-reference |

## Quick Start

### 1. Install dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Set environment variables

Add to your `~/.zshrc`:

```bash
export YOUTRACK_TOKEN=your_token_here
export GITHUB_TOKEN=your_token_here

alias coverage="python3 ~/coverage-check-agent/coverage_check.py"
alias qaupdate="python3 ~/coverage-check-agent/update_tickets.py"
alias qacov="python3 ~/coverage-check-agent/qa_coverage.py"
alias epicov="python3 ~/coverage-check-agent/epic_coverage.py"
```

Then reload: `source ~/.zshrc`

Or run the setup script:

```bash
bash setup.sh
```

---

## `coverage` — Board Coverage Checker

The **primary tool**. Accepts a QA board URL or ID, auto-detects the epic from the board name, fetches all implementation sub-issues, checks GitHub PRs, and outputs a clean two-table report.

```bash
# Full URL
coverage https://realbrokerage.youtrack.cloud/agiles/124-416

# Board ID only
coverage 124-416

# Override epic (if board name doesn't contain the epic ID)
coverage 124-416 --epic RV2-61965
```

### Output

- **Board info panel** — board name + current sprint
- **Epic panel** — epic summary + sub-issue count
- **COVERED table** — test cases where BE/FE PRs exist
- **NOT COVERED / BLOCKED table** — test cases missing implementation
- **IMPLEMENTATION TICKETS table** — all BE/FE sub-issues with PR status
- **Coverage Summary** — counts, percentages, and list of remaining gaps

### How it classifies

**Test cases** are classified as `BE`, `FE`, or `E2E` based on keywords in the ticket summary.

**Implementation tickets** are classified by their summary:
- `BE`: keywords like `[be]`, `be:`, `backend`, `api`, `service`, `java`
- `FE`: keywords like `[fe]`, `fe:`, `bolt:`, `frontend`, `playwright`, `react`
- `BE+FE`: both sets match

A test case is **covered** if:
- Its required BE implementation has at least one PR (any state)
- Its required FE implementation has at least one PR (any state)

---

## `qaupdate` — Comment + Move Tickets to Pass/Fail

After running `coverage`, use this tool to **automatically post comments and update the Stage** of every test case ticket on the board.

```bash
# Show a dry-run plan (no changes made)
qaupdate 124-416 --dry-run

# Full URL also works
qaupdate https://realbrokerage.youtrack.cloud/agiles/124-416

# Only update covered tickets (move to Pass; skip uncovered)
qaupdate 124-416 --covered-only

# Override epic if board name does not contain the epic ID
qaupdate 124-416 --epic RV2-61965
```

### What it does

1. Fetches all test cases from the board's current sprint
2. Looks up implementation PRs for each BE/FE sub-issue of the epic
3. Determines coverage (same logic as `coverage`)
4. Shows a **dry-run plan table**: ticket, type, current stage, new stage, comment preview
5. Asks for confirmation before making any changes
6. Posts a tailored comment and moves Stage to `Pass` or `Fail`:

| Ticket type | Covered comment | Not covered comment |
|-------------|-----------------|---------------------|
| BE | PR info + "Required implementation PR exists. QA testing can proceed." | "No BE PR found" |
| FE | PR info + "Required implementation PR exists. QA testing can proceed." | "No FE PR found" |
| E2E | BE + FE PR info + "All required BE and FE implementation PRs exist." | Missing PR details |

---

## `qacov` — Test Plan Ticket Coverage

Starts from a specific test plan ticket, finds its parent epic, and maps each test scenario to the corresponding implementation PRs.

```bash
qacov https://realbrokerage.youtrack.cloud/issue/RV2-12345
qacov RV2-12345
qacov RV2-12345 --epic RV2-10000   # override if parent detection fails
```

---

## `epicov` — Epic Coverage Checker

Starts from an epic ID, lists all BE/FE sub-issues with PR status, and cross-references the QA test plan board to find linked test tickets.

```bash
epicov RV2-12345
epicov RV2-12345 --board 124-416
```

---

## GitHub repos searched

- `Realtyka/bolt` — FE (TypeScript / React / Playwright)
- `manepranal/bolt-rest-assured` — BE (Java / REST Assured)

PR search looks for the YouTrack ticket ID (e.g. `RV2-12345`) in PR titles, bodies, and branch names.

## YouTrack board structure assumed

```
Agile Board (124-416)
  └── Sprint issues (QA project tickets)
        └── Type = "Test" → test cases
              Stage: Open / In Progress / Blocked / Done

Epic (RV2-XXXXX)  ← extracted from board name e.g. QA_RV2-61965_Feature
  └── Sub-issues
        ├── [BE] ticket → search bolt-rest-assured PRs
        ├── [FE] / Bolt: ticket → search bolt PRs
        └── Test Plan ticket → sub-issues = test scenarios
```

## Auth tokens

| Token | Where to get it |
|-------|------------------|
| `YOUTRACK_TOKEN` | YouTrack → Profile → Hub → Authentication → Permanent tokens |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens (needs `repo` scope) |
