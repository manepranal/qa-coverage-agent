# QA Coverage Agent

CLI tools that automatically check how many test cases on a YouTrack QA test plan board are covered by BE and FE implementation tickets — and clearly shows which are not.

---

## What's Included

| File | Alias | Purpose |
|------|-------|---------|
| `coverage_check.py` | `coverage` | **Primary tool.** Board URL → test cases → epic → impl PRs → coverage report |
| `epic_coverage.py` | `epicov` | Epic → all sub-issues → PRs → test plan board cross-reference |
| `qa_coverage.py` | `qacov` | Test plan ticket → parent epic → impl PRs → scenario-level report |
| `requirements.txt` | — | Python dependencies (`requests`, `rich`) |
| `setup.sh` | — | One-time setup script |
| `.env.example` | — | Template for environment variables |

---

## Setup (One Time)

### 1. Clone the repo

```bash
git clone https://github.com/manepranal/qa-coverage-agent.git ~/coverage-check-agent
```

### 2. Install dependencies

```bash
pip3 install -r ~/coverage-check-agent/requirements.txt
```

### 3. Set tokens + aliases

Add to your `~/.zshrc` (or `~/.bashrc`):

```bash
export YOUTRACK_TOKEN=your_youtrack_permanent_token
export GITHUB_TOKEN=your_github_personal_access_token

alias coverage="python3 ~/coverage-check-agent/coverage_check.py"
alias qacov="python3 ~/coverage-check-agent/qa_coverage.py"
alias epicov="python3 ~/coverage-check-agent/epic_coverage.py"
```

Then reload: `source ~/.zshrc`

Or just run the setup script:

```bash
bash ~/coverage-check-agent/setup.sh
```

### Where to get tokens

| Token | Where |
|-------|-------|
| `YOUTRACK_TOKEN` | YouTrack → Profile → Hub → Authentication → Permanent tokens |
| `GITHUB_TOKEN` | GitHub → Settings → Developer settings → Personal access tokens (`repo` scope) |

---

## Tool 1: `coverage` — Board Coverage Checker

The **primary tool**. Give it a QA board URL or ID — it auto-detects the epic, fetches all BE/FE implementation tickets, checks GitHub PRs, and prints a full coverage report.

```bash
# Full board URL
coverage https://realbrokerage.youtrack.cloud/agiles/124-416

# Board ID only
coverage 124-416

# Override epic if board name doesn't contain the epic ID
coverage 124-416 --epic RV2-61965
```

### What it outputs

- **Board info panel** — board name + current sprint
- **Epic panel** — epic summary + sub-issue count
- **COVERED table** — test cases that have BE/FE implementation PRs
- **NOT COVERED table** — test cases missing implementation PRs
- **IMPLEMENTATION TICKETS table** — all BE/FE sub-issues with PR status (Merged / Open / No PR)
- **Coverage Summary** — counts, percentages, and list of remaining gaps

### How coverage is determined

**Test cases** on the board are classified as `BE`, `FE`, or `E2E` based on keywords in the title.

**Implementation tickets** from the epic are classified as:
- `BE` — keywords: `[be]`, `be:`, `backend`, `api`, `service`, `java`, `yenta`
- `FE` — keywords: `[fe]`, `fe:`, `bolt:`, `frontend`, `playwright`, `react`, `typescript`
- `BE+FE` — both match

**A test case is covered if** its required BE and/or FE implementation ticket has at least one PR (any state — open, draft, or merged). The YouTrack Stage field does not affect coverage.

---

## Tool 2: `epicov` — Epic Coverage Checker

Given an epic ID, lists all sub-issues with their GitHub PR status and cross-references the QA test plan board.

```bash
epicov RV2-61965
epicov RV2-61965 --board 124-416
```

### What it outputs

- All sub-issues with type (BE / FE), state, and GitHub PRs
- PR status: Merged / Open / Draft / No PR
- Summary by type: BE X/Y merged, FE X/Y merged
- Linked QA test plan tickets (if any board tickets reference these sub-issues)

---

## Tool 3: `qacov` — Test Plan Ticket Coverage

Starts from a specific test plan ticket, finds its parent epic, and maps each test scenario to its implementation PRs.

```bash
qacov RV2-12345
qacov https://realbrokerage.youtrack.cloud/issue/RV2-12345
qacov RV2-12345 --epic RV2-10000   # override parent if auto-detection fails
```

---

## GitHub Repos Searched

| Repo | Stack |
|------|-------|
| `Realtyka/bolt` | FE — TypeScript / React / Playwright |
| `manepranal/bolt-rest-assured` | BE — Java / REST Assured |

PR search also reads the **Pull Request** custom field on YouTrack tickets (catches PRs in repos like `Realtyka/yenta` that are linked directly in YouTrack).

---

## YouTrack Board Structure Expected

```
Agile Board (e.g. 124-416)
  └── Sprint issues (QA project tickets, Type = "Test")
        └── Stage: Ready / In Progress / Blocked / Pass / Fail

Board name format: QA_RV2-XXXXX_FeatureName
  └── Epic ID (RV2-XXXXX) is auto-extracted from the board name

Epic (RV2-XXXXX)
  └── Sub-issues
        ├── [BE] tickets  → searched in bolt-rest-assured + yenta
        ├── [FE] / Bolt:  → searched in bolt
        └── Test Plan tickets
```

---

## Time Saved

Without this tool a QA engineer has to:
1. Open every test case on the board
2. Find the linked epic
3. Check each sub-issue in the epic
4. Open GitHub and search for PRs per ticket
5. Manually track what's covered vs not

With this tool: **one command, full coverage report in ~30 seconds.**
