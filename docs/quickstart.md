# Quick Start

Get Commander running locally in about five minutes. For the full walkthrough —
multi-clone setup, agent roles, remote access — see [tutorial.md](tutorial.md).

## Prerequisites

- Python 3.12
- A GitHub account and a personal access token with `repo` scope
- The GitHub CLI (`gh`) installed and authenticated
- Claude Code CLI (`claude`) installed and logged in

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

# 4. Set up sprint manager config
./.commander/setup.sh

# 5. Install shell shortcuts and start the dashboard
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
4. **Sign off** — review the tickets that reach UAT, close the good ones, rerun
   any that need rework.

## Next steps

- [workflow.md](workflow.md) — the full Bulk Create → Run → Finish/Rerun flow
- [tutorial.md](tutorial.md) — multi-clone setup and agent roles
- [features/](features/) — per-subsystem reference
