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
pip install -r requirements.txt

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
| **Sprint Estimator** | Claude Code-driven effort estimation for all sprint tickets — runs automatically at sprint start | see below |
| **API** | REST API consumed by the dashboard and agent hooks | [docs/features/api.md](docs/features/api.md) |

---

## How the estimator works

The Sprint Estimator runs automatically at the start of every sprint (after the
sprint branch is created, before any coder agents are dispatched). It uses a single
Claude Code subprocess to estimate effort, files impacted, and risks for all backlog
tickets in one pass.

**What it does:**
1. Fetches all open backlog issues labeled with the sprint label (skips any already labeled `estimated`, `done`, `UAT-approved`, `needs-rework`, or `closed`).
2. For each ticket, reads the full issue body, scans the codebase for likely-impacted files, and estimates size (S/M/L/XL) and minutes (with 20% buffer, 30% for thin AC).
3. Comments on each issue with the estimate and applies the `estimated` label.
4. Writes `<project>/.commander/sprints/sprint-<N>-estimate.json` atomically.
5. Merges results into the sprint state so the dashboard reflects them immediately.

**Estimate badge:** Each ticket card in the Sprint Mgmt board shows `M · ~25 min` (or equivalent) when estimates are available. The sprint block header shows a total like `estimated 2h 25m across 5 tickets`.

**API:** `GET /api/sprints/{sprint_label}/estimate?project=<name>` returns the estimate JSON or 404 if not yet generated.

**Standalone usage:**

```bash
python3 scripts/sprint_estimator.py sprint-N
python3 scripts/sprint_estimator.py sprint-N --repo owner/repo
```

**Graceful degradation:** If the claude CLI is missing or the agent times out, the estimator logs a warning, writes an empty estimate file, and the sprint continues normally — estimation never blocks the sprint run.

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

## Run as a service (launchd)

For unattended operation — especially when running remote with iPad-only access — the
dashboard can be registered as a macOS LaunchAgent. This starts it automatically on
user login and restarts it if the process crashes.

> **Note:** launchd is an additional deployment option. The existing `start.sh` (tmux)
> workflow continues to work unchanged and is still recommended for interactive
> development.

### Install

```bash
# From the commander repo root:
bash scripts/install_launchd.sh
```

The script will:
1. Check for a port-8000 conflict (prints a warning and exits if one is found).
2. Substitute the repo root, venv path, and home directory into the plist template.
3. Copy `scripts/com.commander.dashboard.plist` to `~/Library/LaunchAgents/` with `644` permissions.
4. Load the service with `launchctl load`.
5. Verify with `launchctl list | grep commander` and print success or failure.

Logs are written to:
- `~/Library/Logs/commander-dashboard.out.log` (stdout / uvicorn output)
- `~/Library/Logs/commander-dashboard.err.log` (stderr / errors)

### Uninstall

```bash
bash scripts/uninstall_launchd.sh
```

Unloads the service and removes the plist from `~/Library/LaunchAgents/`.

### Verify

```bash
# Service is listed:
launchctl list | grep commander

# Tail the log:
tail -f ~/Library/Logs/commander-dashboard.out.log
```

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

## Backup & Restore

Commander automatically backs up your config files (`projects.json`, `sprint.yaml`, and `.env` if
present) to a private GitHub gist. This protects against accidental data loss from `rm -rf`,
disk failure, or a machine wipe — since both files are gitignored by design.

### What gets backed up

| File | Description |
|---|---|
| `apps/dashboard/projects.json` | Project registry (all registered repos) |
| `.commander/sprint.yaml` | Agent config (sprint manager settings) |
| `apps/dashboard/.env` | Environment variables (secrets are **redacted** before upload) |

**Secrets redaction:** any line matching `*_TOKEN=*`, `*_KEY=*`, or `*_SECRET=*` has its value
replaced with `REDACTED` before uploading. The gist description notes this.

### How it works

- On server startup, a backup runs automatically after 30 seconds.
- A backup runs every 6 hours in the background (no impact on request latency).
- A backup is triggered after any successful write to `projects.json` via the dashboard API.
- All backups update the **same private gist** — GitHub stores the full revision history,
  so you can view previous versions on `https://gist.github.com/<your-user>/<gist-id>/revisions`.
- The gist ID is stored in `.commander/backup_config.json` (gitignored).

### Check backup status

```
GET /api/backup/status
```

Returns:
```json
{
  "last_backup_at": "2026-05-28T12:00:00+00:00",
  "gist_id": "abc123...",
  "gist_url": "https://gist.github.com/abc123...",
  "file_count": 2,
  "last_error": null
}
```

### Restore from gist

```bash
# From the repo root:
python -m services.sprint_manager.backup restore --gist-id <gist-id>

# Write to a specific directory:
python -m services.sprint_manager.backup restore --gist-id <gist-id> --target-dir /tmp/restore
```

Restored files are written with their original names. Copy them back to their expected locations
after verifying the contents.

---

## Health Check

`GET /api/health` returns a rich system health snapshot — bookmarkable and safe to
ping from UptimeRobot. No authentication required. Response is cached for 10 seconds.

### Response shape

```json
{
  "status": "ok",
  "checked_at": "2026-05-28T12:00:00Z",
  "checks": {
    "dashboard":        { "status": "ok", "uptime_sec": 12345 },
    "database":         { "status": "ok" },
    "github_auth":      { "status": "ok", "user": "zealchaiwut" },
    "claude_code_auth": { "status": "ok" },
    "disk":             { "status": "ok", "free_gb": 45.2, "total_gb": 500.0 },
    "stuck_sprints":    { "status": "ok", "count": 0, "labels": [] }
  }
}
```

### Overall status values

| Value | Meaning |
|---|---|
| `ok` | All checks are `ok` |
| `degraded` | One or more checks are `warn`, `critical`, `expired`, or `missing`; nothing is critically down |
| `down` | `database` is `down`/`timeout`, or `github_auth` is `expired`/`missing`/`timeout` |

### HTTP status codes

| Overall status | HTTP code |
|---|---|
| `ok` or `degraded` | `200` |
| `down` | `503` |

### Individual check statuses

| Check | Possible statuses |
|---|---|
| `dashboard` | `ok` (always) |
| `database` | `ok`, `down` (with `error` field), `timeout` |
| `github_auth` | `ok` (with `user` field), `expired`, `missing`, `timeout` |
| `claude_code_auth` | `ok`, `expired`, `missing`, `timeout` |
| `disk` | `ok` (≥10 GB free), `warn` (<10 GB free), `critical` (<2 GB free), `timeout` |
| `stuck_sprints` | `ok`, `warn` (with `count` and `labels` fields) |

`github_auth` result is cached for 60 seconds to avoid hammering the `gh` CLI.

---

## Going Remote?

Traveling with iPad-only access? See [docs/TRAVEL_PLAYBOOK.md](docs/TRAVEL_PLAYBOOK.md) for:

- Pre-travel checklist (sleep, launchd, Tailscale, auth, health check)
- URLs to save before you leave
- Common failure modes and step-by-step recovery
- SSH commands reference
- Fallback paths if hardware fails

---

## Database Setup

Commander's sprint manager uses a Neon (Postgres) database for persistent storage. Follow these steps to set it up:

1. **Create a Neon project** — sign up at [neon.tech](https://neon.tech) and create a new project. Copy the connection string from the project dashboard.

2. **Set `DATABASE_URL`** — copy `.env.example` to `.env` at the repo root and fill in your Neon connection string:
   ```bash
   cp .env.example .env
   # Edit .env and set DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
   ```

3. **Run migrations** — apply the schema:
   ```bash
   alembic upgrade head
   ```

After running `alembic upgrade head`, you can verify the setup with:
```bash
# Check the current migration head
alembic current

# Verify the connection
python -c "from services.sprint_manager.neon_db import get_engine; print(get_engine().connect())"
```

### Backfilling existing sprints

If you have existing sprint data in `.commander/sprints/*.json` that pre-dates the Neon integration, run this one-shot script to migrate it:

```bash
python scripts/migrate_sprints_to_neon.py
```

Sprints already present in Neon are skipped automatically, so the script is safe to re-run. Use `--dry-run` to preview what would be inserted without writing anything:

```bash
python scripts/migrate_sprints_to_neon.py --dry-run
```

To backfill only a specific project:

```bash
python scripts/migrate_sprints_to_neon.py --project zealchaiwut/commander
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
- [Travel playbook](docs/TRAVEL_PLAYBOOK.md)
