# Commander platform — usage tutorial

Day-to-day reference for using Commander. For a five-minute install see
[`quickstart.md`](quickstart.md); for provisioning a brand-new machine see
[`machine-onboarding.md`](machine-onboarding.md). For agent rules see
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

For full sprints, the **Sprint Manager** (`sprint_manager.py`) automates the
Coder → Tester → gate → merge loop for every issue in the sprint, runs quality
gates, and generates an executive summary — replacing manual per-ticket
orchestration. See section 5.

Use this platform when a feature benefits from a written contract, automated
regression tests, and a UAT gate. For a one-line tweak you can verify in five
seconds, just edit directly.

### Flow diagram

The diagram shows every handoff in the BA→Coder→Tester→UAT loop.

> **[Open diagram — commander-workflow.excalidraw](commander-workflow.excalidraw)**
> Open the file at [excalidraw.com](https://excalidraw.com) (File → Open) for an interactive view.

---

## 2. Setup

### Prerequisites

| Tool | Min version | Install |
|------|-------------|---------|
| macOS | any | — |
| Python | 3.12 | `brew install python@3.12` |
| Node.js | 18+ | `brew install node` |
| `gh` CLI | 2.x | `brew install gh` |
| Claude Code | latest | `npm install -g @anthropic-ai/claude-code` |
| tmux | any | `brew install tmux` |
| Tailscale | any | [tailscale.com/download](https://tailscale.com/download) — optional |

Authenticate `gh`:

```bash
gh auth login
```

### Clone, install, configure

```bash
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/prd
cd ~/dev/commander/prd
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
npm install && npm run build            # esbuild frontend bundle
cp apps/dashboard/.env.example apps/dashboard/.env
```

Edit `apps/dashboard/.env` — set `TRACKED_REPOS` to your GitHub repo:

```
TRACKED_REPOS=zealchaiwut/commander
GITHUB_DEFAULT_BRANCH=master
```

### Set up agent clones

Commander runs each agent role in its own clone (see §6c for the layouts). The
sprint manager reads their paths from `.commander/sprint.yaml`. To onboard a
project with the nested layout:

```bash
cd ~/dev/commander/prd
python3 scripts/init_project.py <repo-name> --nested
```

### Set up tmux layout (attended local dev only)

```bash
bash ~/dev/commander/prd/start.sh
```

This loads `~/.commander.tmux` and opens four panes: SERVER (top-left),
CODER (top-right), TESTER (bottom-left), UTILITY/BA (bottom-right). For
unattended/remote running, **launchd** is the authoritative runner — see
[`machine-onboarding.md`](machine-onboarding.md).

### First-run verification

```bash
cd ~/dev/commander/prd && source venv/bin/activate
./.commander/setup.sh
bash scripts/start_prd.sh
```

Open `http://localhost:8000`. The dashboard should show your repo.

---

## 3. Running PRD and UAT on different ports

| Environment | Branch | Port | Directory |
|-------------|--------|------|-----------|
| PRD | `master` | **8000** | `~/dev/commander/prd/` |
| UAT | `develop` | **8001** | `~/dev/commander/uat/` |

All `scripts/*.sh` below run from the PRD clone (`~/dev/commander/prd`);
`start_uat.sh` targets `../uat` automatically.

### One-time UAT setup

```bash
bash scripts/setup_uat_env.sh
```

### Start and stop

```bash
# Start PRD (port 8000)
bash scripts/start_prd.sh

# Start UAT (port 8001)
bash scripts/start_uat.sh

# Stop both (or pass prd / uat to stop one)
bash scripts/stop_all.sh

# Show running status and branch
bash scripts/status.sh
```

### Sync UAT after a tester merge

When the Tester merges a feature to `develop`, pull and restart:

```bash
bash scripts/sync_uat.sh
bash scripts/stop_all.sh uat
bash scripts/start_uat.sh
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
cd ~/dev/commander/prd
bash scripts/sync_uat.sh
bash scripts/stop_all.sh uat
bash scripts/start_uat.sh
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
cd ~/dev/commander/prd && source venv/bin/activate
python3 scripts/approve_ticket.py --issue 42
```

### f. Release: merge develop to master, restart PRD

```bash
cd ~/dev/commander/prd
git checkout master
git merge develop --no-ff -m "release: merge develop into master"
git push origin master
bash scripts/stop_all.sh prd
bash scripts/start_prd.sh
```

The endpoint is now live at `http://localhost:8000/api/health`.

---

## 5. Sprint Manager — automated sprint runs

The Sprint Manager (`services/sprint_manager/sprint_manager.py`) replaces the
manual coder → tester → approve loop. Given a sprint label, it works through
every issue in that sprint sequentially: dispatches the Coder agent, dispatches
the Tester, runs seven quality gates (typecheck → lint → frontend-lint → design
→ pytest → monolith → merge-preview), and merges passing tickets to the sprint
base branch. When the sprint finishes it writes an executive summary and
optionally records learnings.

### Run a sprint

```bash
# label is positional; auto-discovers .commander/sprint.yaml by walking up
cd ~/dev/commander/prd
python3 services/sprint_manager/sprint_manager.py sprint-3
```

For each issue the manager:

1. Dispatches the Coder → implements the feature on a worktree branch
2. Dispatches the Tester → writes pytest tests for each AC, runs them
3. Runs the quality gates (cheap/deterministic first; see [Sprint Manager feature doc](features/sprint-manager.md#quality-gates))
4. Gate pass → merges to the sprint base branch, labels ticket `UAT`
5. Gate fail → labels ticket `needs-rework` with an `end_reason`, posts a structured failure comment, moves on

### Common flags

```bash
SM=services/sprint_manager/sprint_manager.py

# Dry run — shows what would happen, no agents dispatched
python3 $SM sprint-3 --dry-run

# Skip all gates (faster, less safe)
python3 $SM sprint-3 --skip-gates

# Skip only the pytest gate (each gate has a --no-gate-<name> flag)
python3 $SM sprint-3 --no-gate-pytest

# Resume after a crash or manual stop
python3 $SM sprint-3 --resume

# Retry tickets that previously failed
python3 $SM sprint-3 --retry-failed

# Send alerts to dashboard banner and a log file
python3 $SM sprint-3 --alert-mode dashboard-banner,file
```

### Executive summary and Sprint History tab

After the sprint loop finishes, the manager writes a Markdown summary to
`.commander/sprints/{label}-summary-YYYY-MM-DD.md` (per-label, so child sprints
don't overwrite siblings) and creates a GitHub issue for permanent record. If
run interactively (TTY) you'll see:

```
Sprint 3 done. Want to add learnings to the summary? (y/n)
```

Type `y` and your `$EDITOR` opens. Learnings are appended to the summary and
synced to the GitHub issue body.

The **Sprint History** tab on the dashboard shows all past summaries with
quick-stats (shipped · skipped · token count) and inline expand to read the
full Markdown.

### Failure categories

When a ticket fails the manager records a failure class (used by the bounded
fix-loop, 4a/4b) on the sidecar and in the comment:

| Category | Meaning |
|----------|---------|
| `CRASH` | Coder or Tester subprocess exited non-zero |
| `HANG` | No log output for the hang-kill timeout; process was killed |
| `GATE_FAIL` | a quality gate returned non-zero |
| `TESTER_REJECTED` | Tester marked the ticket as needs-rework |

Logic failures (gate / tester-rejected) re-dispatch the coder with accumulated
context up to `COMMANDER_MAX_FIX_ROUNDS` (default 3); after that the ticket is
tagged `RETRY_EXHAUSTED`. On terminal failure the manager labels the ticket
`needs-rework` and moves on to the next issue. Infra failures (crash/hang) stay
on their existing paths and don't consume fix rounds.

### Sprint Planning tab

The **Sprint Planning** tab on the dashboard provides a conversational CLI
planner and visual drag-and-drop interface for choosing which backlog issues to
include before running sprint_manager. Open it at `http://localhost:8000`, pick
issues, and use "Start sprint" to launch the manager directly from the UI.

---

## 6. Best practices

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

### c. Project layout — flat vs. nested

Commander supports two directory layouts per project:

**Nested layout** (`--nested`, recommended for new projects):

```
~/dev/<project>/
  main/              # primary working clone (master branch)
  coder/             # coder agent clone (develop branch)
  tester/            # tester agent clone (develop branch)
  uat/               # UAT clone (develop branch) — optional
  .commander/        # sprint config — at project root, outside any git clone
    sprint.yaml
    logs/
    sprints/
    alerts/
```

Benefits: all three clones live under one folder; `.commander/` can never be
accidentally committed because it sits outside any git working tree.

**Flat layout** (default, backward compatible):

```
~/dev/<project>/          # main clone (master branch)
~/dev/<project>-coder/    # coder agent clone (develop branch)
~/dev/<project>-tester/   # tester agent clone (develop branch)
~/dev/<project>/uat/      # UAT clone (develop branch) — optional
~/dev/<project>/.commander/sprint.yaml   # inside the main clone
```

**Onboard a new project with the nested layout:**

```bash
cd ~/dev/commander/prd
source venv/bin/activate
python3 scripts/init_project.py <repo-name> --nested
```

**Migrate an existing flat project to nested:**

```bash
python3 scripts/migrate_project_layout.py <project-name>

# Dry-run first to preview changes:
python3 scripts/migrate_project_layout.py <project-name> --dry-run
```

The sprint manager auto-discovers `.commander/sprint.yaml` by walking up from
the current working directory, so it works from inside any clone in both
layouts — no need to `cd` to a specific directory first.

### d. Sign off on your phone — mobile UAT keeps you honest about UX

A feature that looks fine on a 27-inch monitor can be unusable on a 6-inch
screen. Reviewing UAT via Tailscale on your phone catches layout, tap-target,
and readability regressions before they reach production. Make mobile review
a non-negotiable step.

### e. Use auto-mode for Coder and Tester, never for BA

Coder and Tester follow deterministic workflows with no ambiguity to resolve,
so auto-mode is appropriate. BA makes creative decisions — what ACs are correct?
is the scope right? — that need your review before a GitHub issue is filed. Run
BA interactively and approve the ticket body before it is created.

### f. CLAUDE.md is the source of truth — all project-wide rules go there

Rules scattered across agent prompts, hook scripts, and docs drift. `CLAUDE.md`
is the one file every agent reads at startup. When you change a convention —
new label, new script, new naming rule — update `CLAUDE.md` first so the next
agent follows current conventions, not month-old ones. See
[`CLAUDE.md`](../CLAUDE.md).
