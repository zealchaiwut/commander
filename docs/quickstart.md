# Quick Start

Get Commander running locally in about five minutes. For the full walkthrough —
multi-clone setup, agent roles, remote access — see [tutorial.md](tutorial.md).

## Prerequisites

- Python 3.12
- Node.js 18+ and npm — required for the esbuild frontend bundle (`npm run build`)
- A GitHub account and a personal access token with `repo` scope
- The GitHub CLI (`gh`) installed and authenticated
- Claude Code CLI (`claude`) installed and logged in (`npm install -g @anthropic-ai/claude-code`)

## Install

```bash
# 1. Clone and enter the repo
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/prd
cd ~/dev/commander/prd

# 2. Create the virtualenv and install dependencies
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Copy and edit the environment file
cp apps/dashboard/.env.example apps/dashboard/.env
# Set GITHUB_TOKEN and TRACKED_REPOS in .env

# 4. Build the frontend bundle (esbuild → static/dist/bundle.js)
npm install && npm run build

# 5. Set up sprint manager config
./.commander/setup.sh

# 6. Install shell shortcuts and start the dashboard
bash scripts/install_shell_shortcuts.sh
source ~/.commander.zsh
start-prd   # dashboard at http://localhost:8000
```

Open `http://localhost:8000` and add your first repo from the dashboard.

## First run

1. **Add a repo** — paste an `owner/repo` into the dashboard. It appears as a
   project card.
2. **Bulk Create tickets** — open the Bulk Create tab, paste a prompt describing
   the features you want, and let the BA agent draft tickets. Review, then post
   them to GitHub. See [workflow.md](workflow.md).
3. **Run a sprint** — give the tickets a `sprint-N` label, then hit Run Sprint.
   The Coder and Tester agents work each ticket in turn.
4. **Sign off** — review the tickets that reach UAT, close the good ones. Any
   sprint that needs rework re-runs into a **child sprint** (`sprint-N.1`) off
   the sprint base branch — the original label is never re-dispatched.

## Restart after machine reboot

After a machine restart the dashboard servers need to come back up before the
Deploy tab shows live run-state and commit info.

**PRD (port 8000) — two paths:**

| Setup | What to do |
|---|---|
| launchd installed (`launchctl list \| grep commander`) | Auto-restarts on reboot — nothing to do. Confirm with `curl -s http://localhost:8000/api/health \| jq .status` |
| Manual / dev (no launchd) | `cd ~/dev/commander/prd && bash scripts/start_prd.sh` |

**UAT (port 8001) — always manual:**

```bash
# Run from the PRD clone (start_uat.sh targets ../uat automatically)
cd ~/dev/commander/prd
bash scripts/start_uat.sh
```

Or, if working directly in the UAT clone:

```bash
cd ~/dev/commander/uat
bash scripts/start_uat.sh
```

**Stop either server:**

```bash
bash scripts/stop_all.sh        # both PRD + UAT
bash scripts/stop_all.sh prd    # PRD only
bash scripts/stop_all.sh uat    # UAT only
```

**After starting**, open the Deploy tab — the card for that environment will
show the running commit SHA, commit message, server-started time, and last
deploy timestamp.

> If port 8000/8001 is still held after a kill (orphaned uvicorn worker),
> find and kill it: `lsof -i :8000 -sTCP:LISTEN` → `kill -9 <pid>`, then
> restart.

## Next steps

- [machine-onboarding.md](machine-onboarding.md) — provision a brand-new machine
  from scratch (clone layout, venvs, claude/gh auth, launchd, doctor)
- [workflow.md](workflow.md) — the full Bulk Create → Run → Finish/Rerun flow
- [tutorial.md](tutorial.md) — multi-clone setup and agent roles
- [features/](features/) — per-subsystem reference
