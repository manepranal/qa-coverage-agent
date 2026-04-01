# QA Coverage Agent

CLI tools that automatically check how many test cases on a YouTrack QA test plan board are covered by BE and FE implementation tickets — and clearly shows which are not.

## Tools

| Alias | Script | Purpose |
|-------|--------|---------|
| `coverage` | `coverage_check.py` | **Primary tool.** Board URL → test cases → epic → impl PRs → coverage report |
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
- Its `Stage` field is not `Blocked`

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
|-------|-----------------|
| `YOUTRACK_TOKEN` | YouTrack → Profile → Hub → Authentication → Permanent tokens |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens (needs `repo` scope) |
