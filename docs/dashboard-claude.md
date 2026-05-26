# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

```
commander/
├── dashboard/       # FastAPI web application (Python 3.12) — PRD environment
├── dashboard-uat/   # Separate clone for UAT environment (created by setup_uat_env.sh)
├── projects/        # Project data / configs
└── venv/            # Root-level Python 3.14 venv (separate from dashboard)
```

## PRD / UAT environment split

| | PRD | UAT |
|---|---|---|
| Directory | `~/commander/dashboard/` | `~/commander/dashboard-uat/` |
| Branch | `master` | `develop` |
| Port | **8000** | **8001** |
| Database | `~/commander/dashboard/commander.db` | `~/commander/dashboard-uat/commander.db` |
| Environment var | `ENVIRONMENT=prd` | `ENVIRONMENT=uat` |

### Management scripts (in `dashboard/scripts/`)

| Script | Purpose |
|---|---|
| `setup_uat_env.sh` | One-time setup: clones repo into `dashboard-uat/`, checks out develop, creates venv, writes `.env`, initialises DB |
| `start_prd.sh` | Start PRD server on port 8000 (background, writes `prd.pid`) |
| `start_uat.sh` | Start UAT server on port 8001 (background, writes `uat.pid`) |
| `stop_all.sh` | Kill any processes on ports 8000 and 8001 |
| `status.sh` | Show running status, PID, and git branch for each port |
| `sync_uat.sh` | `git pull origin develop` inside `dashboard-uat/` |

### CRITICAL: Agents must never touch live environment folders

**Agents (Claude Code) MUST NOT run code inside, modify files in, or execute scripts from:**
- `~/commander/dashboard/`        (live PRD environment)
- `~/commander/dashboard-uat/`    (live UAT environment)

All development work happens in **worktrees** (e.g. `~/commander/work-coder/`, `~/commander/work-tester/`).

### Hook behaviour

All three hook scripts (`hooks/tool_used.py`, `hooks/agent_finished.py`,
`hooks/post_tool_used.py`) read the target URL from the `HOOK_POST_TARGET`
environment variable, defaulting to `http://localhost:8000/api/agent-event`.

- **PRD agents** (worktrees for `master`-targeting work): no override needed —
  the default routes to port 8000.
- **UAT agents** (worktrees for `develop`-targeting work): set
  `HOOK_POST_TARGET=http://localhost:8001/api/agent-event` in the project's
  `.claude/settings.json` `env` block. `setup_uat_env.sh` does this
  automatically when setting up `dashboard-uat/`.

Do NOT hard-code port numbers inside the hook scripts themselves.

## Dashboard (FastAPI)

The `dashboard/` service uses its own venv at `dashboard/venv/` with Python 3.12.

**Activate the dashboard venv:**
```bash
source dashboard/venv/bin/activate
```

**Run the dev server (from repo root):**
```bash
cd dashboard && uvicorn server:app --reload --port 8000
```

**Key installed packages:** FastAPI 0.136, Pydantic v2, uvicorn + uvloop, websockets, python-dotenv, PyYAML.

## Root venv

The root `venv/` uses Python 3.14. Activate with:
```bash
source venv/bin/activate
```
