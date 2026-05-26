# Commander Dashboard

A FastAPI-based agent-event dashboard for monitoring Claude Code activity across PRD and UAT environments.

## PRD/UAT Workflow

Commander runs two isolated environments side-by-side:

| | PRD | UAT |
|---|---|---|
| Branch | `master` | `develop` |
| Port | **8000** | **8001** |
| Database | `~/commander/apps/dashboard/commander.db` | `~/commander/uat/apps/dashboard/commander-uat.db` |
| `.env` key | `DB_PATH=./commander.db` | `DB_PATH=./commander-uat.db` |
| Use when | Releasing tested features to production | Verifying features on the develop branch |

### Shell shortcuts

Source `~/.commander.zsh` (add `source ~/.commander.zsh` to your `~/.zshrc`) to get these shortcuts:

| Shortcut | What it does |
|---|---|
| `start-prd` | Start the PRD server on port 8000 |
| `start-uat` | Start the UAT server on port 8001 |
| `stop-prd` | Stop the PRD server (port 8000) |
| `stop-uat` | Stop the UAT server (port 8001) |
| `stop-all` | Stop both PRD and UAT servers |
| `cmdr-status` | Show running status, PID, and git branch for each environment |
| `restart-prd` | Stop PRD, wait 1 s, then start PRD again |
| `restart-uat` | Stop UAT, wait 1 s, then start UAT again |

> `cmdr-status` is used instead of `status` to avoid shadowing macOS `/usr/bin/stat`.

### When to use each environment

- **PRD** (`master` branch, port 8000) — production-grade, stable code only. Agent hooks in production worktrees post events here by default.
- **UAT** (`develop` branch, port 8001) — pre-release validation. Run here to verify features before merging to master. Set `HOOK_POST_TARGET=http://localhost:8001/api/agent-event` in a UAT worktree's `.claude/settings.json` to route events to UAT.

### Database locations

| Environment | File | Notes |
|---|---|---|
| PRD | `~/commander/apps/dashboard/commander.db` | Written by the PRD server (port 8000) only |
| UAT | `~/commander/uat/apps/dashboard/commander-uat.db` | Written by the UAT server (port 8001) only |

Events posted to port 8000 appear **only** in the PRD dashboard; events posted to port 8001 appear **only** in the UAT dashboard. The two databases are fully isolated.

### One-time setup

```bash
# 1. Set up the UAT environment (first time only)
bash ~/commander/scripts/setup_uat_env.sh

# 2. Migrate existing data to the new database files
python3 ~/commander/scripts/migrate_to_separate_dbs.py

# 3. Install shell shortcuts
bash ~/commander/scripts/install_shell_shortcuts.sh
# Then add the following line to ~/.zshrc:
#   source ~/.commander.zsh

# 4. Start both environments
start-prd
start-uat

# 5. Verify
cmdr-status
```

## Repository Layout

```
commander/
├── apps/
│   └── dashboard/       # FastAPI app (Python 3.12) — PRD environment
│       └── commander.db # PRD SQLite database
├── services/
│   └── sprint_manager/  # Sprint manager service
├── scripts/             # Shared CLI tools (create_ticket.py, setup_uat_env.sh, etc.)
├── hooks/               # Claude Code hooks (tool_used, agent_finished, post_tool_used)
├── docs/                # Documentation
├── projects/            # Project data / configs
├── .claude/             # Claude Code configuration (commands, agents, hooks, settings)
├── CLAUDE.md            # Project-wide agent instructions
└── README.md            # This file
```

## Per-project Layout (created by init_project.py)

When `init_project.py` onboards a new project it creates four clones:

```
~/dev/<project>/
├── main/         # primary clone — master branch (the repo root itself)
├── uat/          # UAT clone — develop branch  ← created by init_project.py
│   └── .env      # PORT=<uat_port>, ENVIRONMENT=uat, DB_PATH=./<project>-uat.db
├── .commander/
│   └── sprint.yaml   # includes uat: section (enabled, auto_sync, db_path)
~/dev/<project>-coder/   # coder agent clone — develop branch
~/dev/<project>-tester/  # tester agent clone — develop branch
```

The `uat/` clone uses a separate database (`<project>-uat.db`) and a distinct port,
so it is fully isolated from the PRD environment.

### Adding UAT to an existing project

```bash
python3 scripts/migrate_add_uat.py <repo-name> [--uat-port 8001]
```

### Skipping UAT at init time

```bash
python3 scripts/init_project.py <repo-name> --skip-uat
```

This sets `uat.enabled: false` in `sprint.yaml` and skips the `uat/` clone.

## Hook Behaviour

Hook scripts (`hooks/tool_used.py`, `hooks/agent_finished.py`, `hooks/post_tool_used.py`) read the target URL from the `HOOK_POST_TARGET` environment variable, defaulting to `http://localhost:8000/api/agent-event`.

- **PRD agents**: no override needed — the default routes to port 8000.
- **UAT agents**: set `HOOK_POST_TARGET=http://localhost:8001/api/agent-event` in the project's `.claude/settings.json` `env` block.
