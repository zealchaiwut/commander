# Commander

Personal AI agent platform for solo development with Claude Code. Runs a full
BA → Coder → Tester → UAT sign-off workflow using GitHub Issues as a sprint board,
with a live dashboard at `localhost:8000`.

---

## Quick Start

```bash
# 1. Clone and enter the repo
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/prd
cd ~/dev/commander/prd

# 2. Create the virtualenv and install dependencies
python3.12 -m venv venv && source venv/bin/activate
pip install -r apps/dashboard/requirements.txt

# 3. Copy and edit the environment file
cp apps/dashboard/.env.example apps/dashboard/.env
# Set GITHUB_TOKEN and TRACKED_REPOS in .env

# 4. Set up sprint manager config
./.commander/setup.sh

# 5. Install shell shortcuts and start both environments
bash scripts/install_shell_shortcuts.sh
source ~/.commander.zsh
start-prd   # dashboard at http://localhost:8000
start-uat   # dashboard at http://localhost:8001
```

Then open `http://localhost:8000` and add your first repo from the dashboard.

For a full walkthrough including multi-clone setup for Coder/Tester agents, see
[docs/tutorial.md](docs/tutorial.md).

---

## Features

| Feature | What it does | Docs |
|---|---|---|
| **Dashboard** | Live agent event feed, project cards, sprint progress bar, UAT sign-off UI | [docs/features/dashboard.md](docs/features/dashboard.md) |
| **Sprint Manager** | Automates the BA → Coder → Tester → UAT loop for every ticket in a sprint | [docs/features/sprint-manager.md](docs/features/sprint-manager.md) |
| **API** | REST API consumed by the dashboard and agent hooks | [docs/features/api.md](docs/features/api.md) |

---

## PRD / UAT Environments

Commander runs two isolated environments side-by-side:

| | PRD | UAT |
|---|---|---|
| Branch | `master` | `develop` |
| Port | **8000** | **8001** |
| Database | `apps/dashboard/commander.db` | `uat/apps/dashboard/commander-uat.db` |
| Use when | Stable, production-grade features | Validating features before merge to master |

### Shell shortcuts

Source `~/.commander.zsh` to get these shortcuts:

| Shortcut | What it does |
|---|---|
| `start-prd` / `start-uat` | Start PRD (8000) or UAT (8001) server |
| `stop-prd` / `stop-uat` / `stop-all` | Stop one or both servers |
| `restart-prd` / `restart-uat` | Stop then start |
| `cmdr-status` | Show PID, port, branch, and running state for each environment |

---

## Repository Layout

```
commander/
├── apps/
│   └── dashboard/          # FastAPI app — PRD server
│       ├── server.py        # API routes
│       ├── projects.py      # GitHub data layer
│       ├── sprint_manager/  # Sprint orchestration engine
│       └── static/          # Frontend (index.html + app.js)
├── scripts/                 # CLI tools (create_ticket.py, init_project.py, …)
├── hooks/                   # Claude Code hooks (agent_finished, tool_used, …)
├── services/                # Background services
├── docs/                    # Documentation
│   ├── features/            # Per-feature guides
│   ├── changelog/
│   │   ├── uat/             # Changelogs written when a sprint finishes on develop
│   │   └── prd/             # Changelogs written when develop is merged to master
│   └── testing/             # Sandbox and test setup
├── .claude/                 # Claude Code config (agents, commands, hooks)
├── .commander/              # Sprint manager config (sprint.yaml, logs, sprints)
└── CLAUDE.md                # Agent instructions (read by all agents)
```

---

## Docs

- [Setup and tutorial](docs/tutorial.md)
- [Dashboard](docs/features/dashboard.md)
- [Sprint Manager](docs/features/sprint-manager.md)
- [API reference](docs/features/api.md)
- [UAT changelogs](docs/changelog/uat/)
- [PRD changelogs](docs/changelog/prd/)
- [Testing sandbox](docs/testing/sandbox-repo.md)
- [Sprint manager config](.commander/README.md)
