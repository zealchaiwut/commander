# Commander platform — usage tutorial

Day-to-day reference for using Commander. For first-time machine setup see
[`dashboard/README.md`](../dashboard/README.md). For agent rules see
[`CLAUDE.md`](../CLAUDE.md).

---

## 1. What Commander is

Commander is a personal AI agent platform that runs a full software delivery
workflow — planning, coding, testing, and sign-off — using Claude Code agents
and a GitHub Issues sprint board. The dashboard at `localhost:8000` shows live
agent status, project metrics, and a UAT sign-off UI.

The core loop: you describe a feature to the BA agent, which writes acceptance
criteria and files a GitHub issue. The Coder agent creates a feature branch and
implements it. The Tester writes pytest tests against each AC item, runs them,
and merges to `develop` if they pass. You then review the feature on the UAT
server (port 8001) and approve it from the dashboard. After approval you merge
`develop` to `master` and restart PRD.

Use this platform when a feature benefits from a written contract, automated
regression tests, and a UAT gate. For a one-line tweak you can verify in five
seconds, just edit directly.

### Flow diagram

The diagram below shows every handoff in the BA→Coder→Tester→UAT loop.

![Commander workflow diagram](commander-workflow.excalidraw)

> Source: [`docs/commander-workflow.excalidraw`](commander-workflow.excalidraw).
> Open at [excalidraw.com](https://excalidraw.com) for an interactive view.

---

## 2. Setup

### Prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| macOS | any | — |
| Python | 3.12 | `brew install python@3.12` |
| Node.js | 18+ | `brew install node` |
| `gh` CLI | 2.x | `brew install gh` |
| Claude Code | latest | `npm install -g @anthropic/claude-code` |
| tmux | any | `brew install tmux` |
| Tailscale | any | [tailscale.com/download](https://tailscale.com/download) — optional |

Authenticate `gh`:

```bash
gh auth login
```

### Clone, install, configure

```bash
git clone https://github.com/zealchaiwut/commander.git ~/commander
cd ~/commander/dashboard
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — set `TRACKED_REPOS` to your GitHub repo:

```
TRACKED_REPOS=zealchaiwut/commander
GITHUB_DEFAULT_BRANCH=master
```

### Set up worktrees

```bash
cd ~/commander
git worktree add work-coder develop
git worktree add work-tester develop
```

### Set up tmux layout

```bash
bash ~/commander/start.sh
```

This loads `~/.commander.tmux` and opens four panes: SERVER (top-left),
CODER (top-right), TESTER (bottom-left), UTILITY/BA (bottom-right).

### First-run verification

```bash
cd ~/commander/dashboard && source venv/bin/activate
python3 -c "import server, db, github_client, projects; print('OK')"
bash scripts/start_prd.sh
```

Open `http://localhost:8000`. The dashboard should show your repo.

---

## 3. Running PRD and UAT on different ports

| Environment | Branch | Port | Directory |
|-------------|--------|------|-----------|
| PRD | `master` | **8000** | `~/commander/dashboard/` |
| UAT | `develop` | **8001** | `~/commander/dashboard-uat/` |

### One-time UAT setup

```bash
bash ~/commander/dashboard/scripts/setup_uat_env.sh
```

### Start and stop

```bash
# Start PRD (port 8000)
bash ~/commander/dashboard/scripts/start_prd.sh

# Start UAT (port 8001)
bash ~/commander/dashboard/scripts/start_uat.sh

# Stop both
bash ~/commander/dashboard/scripts/stop_all.sh

# Show running status and branch
bash ~/commander/dashboard/scripts/status.sh
```

### Sync UAT after a tester merge

When the Tester merges a feature to `develop`, pull and restart:

```bash
bash ~/commander/dashboard/scripts/sync_uat.sh
bash ~/commander/dashboard/scripts/stop_all.sh
bash ~/commander/dashboard/scripts/start_uat.sh
```

### Access from phone via Tailscale

```bash
tailscale ip -4   # prints your Tailscale IP
```

Open `http://<tailscale-ip>:8000` (PRD) or `http://<tailscale-ip>:8001` (UAT)
on your phone.

Use PRD for daily monitoring while agents run. Use UAT to review a feature
after the Tester merges it, before you approve and release to `master`.

---

## 4. The workflow — end to end

This walks through adding a `/api/health` endpoint from idea to production.

### a. Describe the feature to the BA agent (utility pane)

```bash
CLAUDE_AGENT_ROLE=ba claude
```

```
/ba Add a /api/health endpoint that returns {"status": "ok", "uptime_seconds": N}
```

The BA agent asks one or two clarifying questions, then presents a draft ticket
with acceptance criteria and UAT test steps. Reply `approve` or request changes.
On approval the agent files the GitHub issue and prints the URL, e.g.:

```
https://github.com/zealchaiwut/commander/issues/42
```

### b. Coder implements the feature (coder pane)

```bash
CLAUDE_AGENT_ROLE=coder claude
```

```
/coder https://github.com/zealchaiwut/commander/issues/42
```

The Coder creates `feature/42-add-api-health-endpoint` off `develop`,
implements the feature, commits, pushes, and moves the ticket to `SIT`. Watch
the dashboard at `http://localhost:8000` for live progress.

### c. Tester verifies and merges to develop (tester pane)

```bash
CLAUDE_AGENT_ROLE=tester claude
```

```
/tester https://github.com/zealchaiwut/commander/issues/42
```

The Tester checks out the feature branch, writes `tests/test_api_health__42.py`
(one test per AC item), runs pytest, posts a structured report as a GitHub
comment, and — if all tests pass — merges to `develop` and moves the ticket to
`UAT`.

### d. Verify on UAT at port 8001

Sync and restart UAT:

```bash
bash ~/commander/dashboard/scripts/sync_uat.sh
bash ~/commander/dashboard/scripts/stop_all.sh
bash ~/commander/dashboard/scripts/start_uat.sh
```

Open the feature on your phone: `http://<tailscale-ip>:8001/api/health`.
Expected:

```json
{"status": "ok", "uptime_seconds": 42}
```

### e. Approve via the dashboard

On the dashboard expand the project row. Find the ticket in the UAT column.
Tick any `MANUAL` UAT steps shown inline, then click **Approve**.

Or approve from the terminal:

```bash
cd ~/commander/dashboard && source venv/bin/activate
python3 scripts/approve_ticket.py --issue 42
```

### f. Release: merge develop to master, restart PRD

```bash
cd ~/commander
git checkout master
git merge develop --no-ff -m "release: merge develop into master"
git push origin master
bash dashboard/scripts/stop_all.sh
bash dashboard/scripts/start_prd.sh
```

The endpoint is now live at `http://localhost:8000/api/health`.

---

## 5. Best practices

### a. Write acceptance criteria before any code — the BA ticket is the contract

When ACs exist before implementation, the Coder and Tester share the same
definition of done. Without them, "done" means something different to each
agent and rework is inevitable. Always start with `/ba`, even for small
features.

### b. Never let the Coder touch master — three-tier branching protects you

`master` is production; `develop` is the integration surface; feature branches
are disposable. The moment you allow shortcuts (direct commits to `master`,
untested merges) the safety net breaks. Use `start_feature.py` and
`finish_feature.py` through the agents — do not do manual git operations that
bypass the workflow.

### c. Sign off on your phone — mobile UAT keeps you honest about UX

A feature that looks fine on a 27-inch monitor can be unusable on a 6-inch
screen. Reviewing UAT via Tailscale on your phone catches layout, tap-target,
and readability regressions before they reach production. Make mobile review
a non-negotiable step.

### d. Use auto-mode for Coder and Tester, never for BA

Coder and Tester follow deterministic workflows with no ambiguity to resolve,
so auto-mode is appropriate. BA makes creative decisions — what ACs are correct?
is the scope right? — that need your review before a GitHub issue is filed. Run
BA interactively and approve the ticket body before it is created.

### e. CLAUDE.md is the source of truth — all project-wide rules go there

Rules scattered across agent prompts, hook scripts, and docs drift. `CLAUDE.md`
is the one file every agent reads at startup. When you change a convention —
new label, new script, new naming rule — update `CLAUDE.md` first so the next
agent follows current conventions, not month-old ones. See
[`CLAUDE.md`](../CLAUDE.md).
