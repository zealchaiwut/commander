# Commander Dashboard

Live status dashboard for Claude Code agents, with a GitHub Issues sprint board and a quality gates workflow (BA → Coder → Tester → UAT sign-off).

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [First-time setup](#first-time-setup)
3. [Configuration reference](#configuration-reference)
4. [PRD / UAT workflow](#prd--uat-workflow)
5. [Running the dashboard](#running-the-dashboard)
6. [Using the dashboard](#using-the-dashboard)
7. [Agent hooks](#agent-hooks)
8. [GitHub sprint board](#github-sprint-board)
9. [Quality gates flow](#quality-gates-flow)
10. [Agent helper scripts](#agent-helper-scripts)
11. [API reference](#api-reference)
12. [Migrating to another machine](#migrating-to-another-machine)

---

## Prerequisites

Install these before anything else.

| Tool | Min version | Install |
|------|-------------|---------|
| Python | 3.12 | `brew install python@3.12` |
| `gh` CLI | 2.x | `brew install gh` |
| Git | any | pre-installed on macOS |
| Claude Code CLI | latest | `npm install -g @anthropic/claude-code` |
| Tailscale *(optional)* | any | [tailscale.com/download](https://tailscale.com/download) — for phone access |

Authenticate `gh` once:

```bash
gh auth login
```

---

## First-time setup

```bash
# 1. Clone the repo
git clone https://github.com/zealchaiwut/commander.git ~/commander
cd ~/commander/dashboard

# 2. Create the Python virtualenv
python3.12 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — at minimum set TRACKED_REPOS (see Configuration reference below)

# 5. Register Claude Code hooks globally (one-time, per machine)
chmod +x hooks/*.py scripts/*.py scripts/*.sh
#   If ~/.claude/settings.json does not exist yet:
cp .claude/settings.json ~/.claude/settings.json
#   If it already exists, open both files and merge the "hooks" block manually.

# 6. (Optional) Create GitHub labels for the sprint board
#    Run this once per GitHub repo you want to track:
gh label create sprint-1  --color 0075ca --repo zealchaiwut/commander
gh label create in-progress --color e4e669 --repo zealchaiwut/commander
gh label create SIT         --color d93f0b --repo zealchaiwut/commander
gh label create UAT         --color 0052cc --repo zealchaiwut/commander
gh label create UAT-approved --color 0e8a16 --repo zealchaiwut/commander
gh label create needs-rework --color ee0701 --repo zealchaiwut/commander
gh label create blocked      --color b60205 --repo zealchaiwut/commander
```

---

## Configuration reference

All settings live in `dashboard/.env`. Copy `.env.example` as a starting point.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DB_PATH` | **Yes** | *(none — server exits if absent)* | Path to the SQLite database file. PRD: `./commander.db`. UAT: `./commander-uat.db`. |
| `TRACKED_REPOS` | No | auto-detected from `git remote` | Comma-separated `owner/repo` list shown on the Projects tab. Example: `zealchaiwut/commander,zealchaiwut/other-project` |
| `GITHUB_REPO` | No | auto-detected from `git remote` | Override for a single repo. Rarely needed if `TRACKED_REPOS` is set. |
| `GITHUB_DEFAULT_BRANCH` | No | `main` | Used when linking to GitHub for new PRs. |
| `SPRINT_DURATION_DAYS` | No | `14` | Length of a sprint in days, used to compute ETA. |

`GITHUB_REPO` and `TRACKED_REPOS` are auto-detected from the `origin` remote of the `dashboard/` directory. You only need to set them if auto-detection fails or you want to override.

---

## PRD / UAT workflow

Two isolated server environments run side-by-side so you can develop on `develop`
(UAT) while `master` (PRD) stays stable.

| | PRD | UAT |
|---|---|---|
| Directory | `~/commander/dashboard/` | `~/commander/dashboard-uat/dashboard/` |
| Branch | `master` | `develop` |
| Port | **8000** | **8001** |
| Database | `commander.db` | `commander-uat.db` |
| `ENVIRONMENT` | `prd` | `uat` |
| `DB_PATH` | `./commander.db` | `./commander-uat.db` |

### Shell shortcuts

Install the `~/.commander.zsh` functions once, then add `source ~/.commander.zsh` to your
`~/.zshrc`. These functions delegate to the existing scripts in `dashboard/scripts/`:

| Function | What it does |
|---|---|
| `start-prd` | Start PRD server on port 8000 |
| `start-uat` | Start UAT server on port 8001 |
| `stop-prd` | Stop the PRD server |
| `stop-uat` | Stop the UAT server |
| `stop-all` | Stop both servers |
| `cmdr-status` | Show running status + branch for each port |
| `restart-prd` | Stop PRD, wait 1 s, start it again |
| `restart-uat` | Stop UAT, wait 1 s, start it again |

> `cmdr-status` uses the `cmdr-` prefix to avoid shadowing the macOS `/usr/bin/stat` binary.

### Which environment should I use?

- **PRD** — `master` branch, port 8000. Use this to monitor live agent activity.
- **UAT** — `develop` branch, port 8001. Use this to test new features before merging.

### Setting up UAT for the first time

```bash
bash ~/commander/dashboard/scripts/setup_uat_env.sh
```

This clones the repo into `~/commander/dashboard-uat/`, checks out `develop`, creates a venv,
writes `.env` with `DB_PATH=./commander-uat.db`, and initialises the database.

### First-time database migration (PRD)

If you have live data in `dashboard.db`, migrate it to `commander.db` before updating
`dashboard/.env`:

```bash
python3 ~/commander/dashboard/scripts/migrate_to_separate_dbs.py
```

Then ensure `dashboard/.env` contains `DB_PATH=./commander.db`.

---

## Running the dashboard

```bash
cd ~/commander/dashboard
source venv/bin/activate
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

- Local access: `http://localhost:8000`
- Phone / other devices via Tailscale: `http://<your-tailscale-ip>:8000`

The `--reload` flag is optional but useful during development — it restarts the server on file changes. Drop it for a stable background process.

**Run in the background:**

```bash
nohup uvicorn server:app --host 0.0.0.0 --port 8000 > /tmp/commander.log 2>&1 &
echo $! > /tmp/commander.pid
# To stop: kill $(cat /tmp/commander.pid)
```

---

## Using the dashboard

### Projects tab (default)

The main view. One row per tracked repo.

- **Metrics row** — active sprints, open tickets, UAT queue, and working agents counts.
- **Filter bar** — `All`, `Active` (has an open sprint), `Needs review` (has UAT tickets).
- **Project rows** — click any row to expand it.
  - Progress bar shows closed/total tickets for the current sprint.
  - ETA is computed from the closure rate over the last 7 days.
  - Agent pills show which agents are active on that project.
- **Expanded panel** — shows the top 5 open tickets and active agents for that project.
  - **UAT tickets** display the Tester's test report inline: ✅/❌ per criterion, ✅/⚠️/❌ per UAT step.
  - **⚠️ MANUAL** steps show a checkbox — tick each one as you walk through it.
  - The **Approve** button turns green once all manual steps are ticked (or if there are none).
  - **Reject** opens an inline text field — write the reason and it is posted as a GitHub comment and the ticket moves back to `in-progress`.

### Agents tab

Live view of all non-archived Claude Code sessions.

- **working** (green left border) — session is actively using a tool.
- **waiting** (amber) — session is idle, waiting for input or a blocker.
- **done** (dimmed) — session ended; auto-archived after 1 hour.

Each card shows the role badge, repo + branch, last tool used, and time since last activity.

### Activity log tab

Chronological log of the last 50 tool-use events across all sessions. Useful for seeing what all agents have been doing at a glance.

### Theme toggle

The moon/sun button in the top-right corner switches between light and dark theme. The preference is saved in `localStorage`.

### Live indicator

The green dot in the header shows whether the SSE stream is connected. If it goes grey, the dashboard is not receiving live updates — check that the server is still running.

---

## Agent hooks

Hooks fire automatically on every Claude Code session once registered globally. They report tool activity and session end to the dashboard.

### What they send

- **PreToolUse** (`hooks/tool_used.py`) — fires before every tool call, sends `status: working` plus the tool name.
- **Stop** (`hooks/agent_finished.py`) — fires when a session ends, sends `status: done`.

### Agent identification

Each hook auto-detects:
- **Git repo name** from `git remote get-url origin`
- **Git branch** from `git rev-parse --abbrev-ref HEAD`
- **Folder name** as fallback when git is unavailable

Set `CLAUDE_AGENT_ROLE` before launching Claude Code to tag what kind of work the session is doing:

```bash
CLAUDE_AGENT_ROLE=coder  claude   # blue badge
CLAUDE_AGENT_ROLE=tester claude   # purple badge
CLAUDE_AGENT_ROLE=ba     claude   # green badge
claude                            # grey "agent" badge (default)
```

The composed name format is: `{role}·{repo}·{branch}·#{session[:6]}`
Example: `coder·commander·feature/auth·#a3f7c2`

### Registering / updating hooks

The hook registration lives in `.claude/settings.json`. After any change, re-copy it:

```bash
cp ~/commander/dashboard/.claude/settings.json ~/.claude/settings.json
```

If `~/.claude/settings.json` already has content (other hooks, MCP servers), merge the `"hooks"` block manually rather than overwriting.

---

## GitHub sprint board

### Label conventions

| Label | Meaning |
|-------|---------|
| `sprint-N` | Ticket belongs to sprint N |
| `in-progress` | Being worked on |
| `SIT` | In system/integration testing |
| `UAT` | Ready for your sign-off |
| `UAT-approved` | Approved; ticket is closed |
| `needs-rework` | Rejected from UAT, sent back |
| `blocked` | Blocked on a dependency |

A ticket can carry multiple labels. The dashboard picks the highest-priority status label (`blocked > UAT > SIT > in-progress > backlog`).

### Adding a new sprint

1. Create the label in GitHub: `gh label create sprint-2 --color 0075ca --repo owner/repo`
2. Add it to `projects.json` under `active_sprints`:
   ```json
   "active_sprints": {
     "2": { "started_at": "2026-06-05", "theme": "Sprint theme here" }
   }
   ```
3. Attach the new label to tickets for that sprint.

---

## Quality gates flow

Every feature follows four gates before you sign off:

```
/ba  →  CLAUDE_AGENT_ROLE=coder  →  /tester  →  UAT (you on the dashboard)
```

### 1. BA — write the ticket

```
/ba <feature description>
```

The BA agent asks clarifying questions, generates acceptance criteria and UAT test steps using the `.github/ISSUE_TEMPLATE/feature.md` template, then creates the GitHub issue via `scripts/create_ticket.py`.

### 2. Coder — implement

```bash
CLAUDE_AGENT_ROLE=coder claude
```

The coder picks up the ticket, moves it to `in-progress`, implements the feature, then moves it to `SIT`:

```bash
python3 scripts/update_ticket.py --issue 42 --status in-progress
# ... implement ...
python3 scripts/update_ticket.py --issue 42 --status sit
```

### 3. Tester — automated verification

```
/tester verify issue 42
```

Or from the terminal:

```bash
./scripts/run_tester.sh 42
```

The Tester agent:
1. Reads the ticket's Acceptance Criteria and UAT Test Steps.
2. Writes `tests/test_<feature>__42.py` with one test per AC item.
3. Runs `pytest` against the running dev server at `http://localhost:8000`.
4. Attempts HTTP-verifiable UAT steps; marks visual/mobile steps ⚠️ MANUAL.
5. Posts a structured test report as a GitHub comment.
6. Moves the ticket to `UAT` only if all automated checks pass.

### 4. UAT — your sign-off

Expand the project on the dashboard. The UAT ticket shows the test report inline. Tick off each ⚠️ MANUAL step as you walk through it, then hit **Approve**.

### Test report format

The Tester posts comments using these exact H2 headers (parsed by the dashboard):

```markdown
## Acceptance Criteria Results
- [x] User can log in with valid credentials — ✅ PASS
- [ ] Invalid password shows error — ❌ FAIL (AssertionError: expected 401, got 200)

## UAT Step Results
1. Navigate to /login — ✅ PASS (HTTP 200)
2. Enter credentials and submit — ⚠️ MANUAL (browser interaction required)

## Summary
Status: READY_FOR_UAT
Passed: 1 / Failed: 0 / Manual: 1
```

### Running tests manually

```bash
cd ~/commander/dashboard
source venv/bin/activate

# All tests
pytest tests/ -v

# Tests for a specific issue
pytest tests/test_login_timeout__42.py -v --tb=short
```

Test files accumulate in `tests/` as permanent regression artifacts.

---

## Agent helper scripts

All scripts load `.env` automatically and auto-detect the GitHub repo from `git remote`.

```bash
source venv/bin/activate   # ensure venv is active

# Create a new issue in sprint 2
python3 scripts/create_ticket.py \
  --title "Add token spend widget" \
  --body  "Description…" \
  --sprint 2 \
  --labels "feature"

# Move an issue through the workflow
python3 scripts/update_ticket.py --issue 42 --status in-progress
python3 scripts/update_ticket.py --issue 42 --status sit
python3 scripts/update_ticket.py --issue 42 --status uat
python3 scripts/update_ticket.py --issue 42 --status blocked

# Post a plain comment
python3 scripts/comment_ticket.py \
  --issue 42 \
  --body "Picked this up at $(date '+%H:%M')"

# Post a structured test report (used by the Tester agent)
python3 scripts/post_test_report.py \
  --issue 42 \
  --report-file /tmp/report.md
```

All scripts accept an optional `--repo owner/repo` argument to override the auto-detected repo.

---

## UI routing reference

The dashboard has two UI surfaces and a legacy redirect layer.

| Path pattern | Served asset | Notes |
|---|---|---|
| `/project/{slug}/{tab}` | `static/project.html` | Current UI — use this for all new links |
| `/project/{slug}` | — | Redirects to `/project/{slug}/sprint-mgmt` (302) |
| `/legacy/{slug}/{tab}` | `static/index.html` | Legacy UI (deprecated) — emits a server warning on every hit |
| `/projects/{slug}/{tab}` | — | Redirects to `/project/{slug}/{tab}` (301) — for old bookmarks only |
| `/projects/{slug}` | — | Redirects to `/project/{slug}/sprint-mgmt` (301) |
| `/projects/` | — | Redirects to `/` (301) |

> Do not use `/projects/` in new code or links. Use `/project/` for the current UI or `/legacy/` to reach the deprecated index.html interface.

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Server health — uptime + DB reachability |
| GET | `/` | Dashboard UI |
| GET | `/api/projects` | All tracked projects with metrics and ETA |
| GET | `/api/project-details?repo=owner/repo` | Tickets + agents for one project |
| GET | `/api/agents` | Active (non-archived) agents |
| GET | `/api/events` | Recent 50 tool-use events |
| POST | `/api/agent-event` | Hook ingestion endpoint |
| GET | `/events` | SSE live stream |
| GET | `/api/sprints` | Available sprints + current default |
| GET | `/api/issues?sprint=N` | Issues for sprint N |
| POST | `/api/issues/{id}/approve` | Approve UAT — adds `UAT-approved`, closes ticket |
| POST | `/api/issues/{id}/reject` | Reject UAT — body: `{"reason":"..."}` |
| GET | `/api/issues/{id}/test-report` | Parsed test report + UAT steps for a ticket |
| GET | `/api/repo/config` | Detected repo + default branch |

All write endpoints accept an optional `?repo=owner/repo` query parameter.

---

## Migrating to another machine

These steps get the dashboard running identically on a new Mac.

### Step 1 — Install prerequisites

```bash
brew install python@3.12 gh
npm install -g @anthropic/claude-code
gh auth login
```

Install and connect Tailscale if you want phone access from outside the local network.

### Step 2 — Clone and set up

```bash
git clone https://github.com/zealchaiwut/commander.git ~/commander
cd ~/commander/dashboard
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 3 — Configure

```bash
cp .env.example .env
```

Edit `.env`. The only thing you usually need to set is `TRACKED_REPOS`:

```
TRACKED_REPOS=zealchaiwut/commander
GITHUB_DEFAULT_BRANCH=master
SPRINT_DURATION_DAYS=14
```

`GITHUB_REPO` is auto-detected from the `origin` remote, so you can leave it commented out.

### Step 4 — Register hooks

```bash
chmod +x hooks/*.py scripts/*.py scripts/*.sh

# Check whether ~/.claude/settings.json already exists
ls ~/.claude/settings.json
```

**If the file does not exist:**
```bash
cp .claude/settings.json ~/.claude/settings.json
```

**If the file already exists**, open both files and copy the `"hooks"` array from `.claude/settings.json` into `~/.claude/settings.json`. Do not overwrite the whole file — you may have MCP servers or other settings there.

### Step 5 — Verify and start

```bash
# Quick check — should print "OK"
python3 -c "import server, db, github_client, projects; print('OK')"

# Start the server
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` in your browser.

### Step 6 — (Optional) Migrate agent history

The dashboard stores agent events in `dashboard.db` (SQLite). This is ephemeral data — agent history, not source code. You can either:

- **Start fresh** (recommended): leave `dashboard.db` out of the migration. It will be recreated automatically on first run.
- **Copy it**: `scp old-machine:~/commander/dashboard/dashboard.db ~/commander/dashboard/dashboard.db` — gives you historical activity log entries on the new machine.

### Step 7 — (Optional) Migrate manual UAT checkbox state

Ticked UAT step checkboxes are stored in the browser's `localStorage`. They do not transfer automatically.

To export from the old browser and import on the new one:

```javascript
// Run in browser console on old machine — copies all uat-manual-* keys
const keys = Object.keys(localStorage).filter(k => k.startsWith('uat-manual-'));
console.log(JSON.stringify(Object.fromEntries(keys.map(k => [k, localStorage.getItem(k)]))));
```

Paste the output, then on the new machine:

```javascript
// Run in browser console on new machine — paste your JSON object as `data`
const data = { /* paste here */ };
Object.entries(data).forEach(([k, v]) => localStorage.setItem(k, v));
```

### What does and does not transfer

| Item | Transfers via git | Notes |
|------|-------------------|-------|
| Source code + config | ✅ | Everything in the repo |
| `.env` secrets | ❌ | Re-create from `.env.example` |
| `dashboard.db` | ❌ | Optional — copy manually if you want history |
| `projects.json` | ✅ | Committed to the repo |
| `tests/` files | ✅ | Committed to the repo |
| `~/.claude/settings.json` hooks | ❌ | Re-register on new machine (Step 4) |
| Browser localStorage (UAT checkboxes) | ❌ | Export/import manually if needed |
| `gh` auth token | ❌ | Run `gh auth login` on new machine |
| Tailscale connection | ❌ | Install and connect Tailscale separately |
