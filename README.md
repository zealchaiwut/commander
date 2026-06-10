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
| **Settings** | Global and per-project key-value config store backed by Neon; REST API at `GET/PUT /api/settings` and `GET/PUT /api/projects/{slug}/settings` | [SCHEMA.md](SCHEMA.md) |
| **Global Settings screen** | Gear icon in the dashboard header opens a settings panel for global config (model defaults, estimation params) | — |
| **Project Settings tab** | "More" menu on project cards exposes a Settings tab for per-project overrides | — |
| **Settings sync** | Bidirectional sync between local files (`projects.json`, `sprint.yaml`) and Neon; diff preview before commit via `POST /api/settings/sync/diff` and `POST /api/settings/sync/commit` | — |
| **Editable env paths** | Project environment paths (prd/uat/coder/tester) are editable from the dashboard via `GET/PUT /api/projects/{slug}/environments`; server-side folder browser at `GET /api/fs/list` | — |
| **Deploy tab** | Per-environment deploy/restart/start/stop cards for prd/uat, scoped to the active project. `host=local` runs pull-only `git pull --ff-only` + launchd `kickstart`/scripts (self-restart routed through a detached helper); `host=render` drives the Render deploy/restart API. Cards inline-edit the run folder + port, show a live capped log tail after an action, and badge live run-state. Config under the `deploy_config` settings key; secrets never returned in cleartext. APIs at `GET/PUT /api/projects/{slug}/deploy-config`, `POST .../environments/{env}/deploy`, `.../restart`, `.../start`, `.../stop`, `.../deploy-config/validate`, `GET .../deploy-status`, `.../run-state`, `GET /api/deploy/overview` | [SCHEMA.md](SCHEMA.md) |
| **Env-var editor** | Render-style masked `.env` editor per environment with per-row reveal and edit/add/delete/save; writes preserve line order and inline comments. APIs at `GET/PUT /api/projects/{slug}/environments/{env}/env-vars` | [SCHEMA.md](SCHEMA.md) |
| **Project events log** | Structured audit log of project-level actions (settings changes, env path updates) recorded in the `project_events` SQLite table | [SCHEMA.md](SCHEMA.md) |
| **Neon DB** | Optional Postgres export target for sprint and project state with Alembic migrations; populated on demand via `scripts/export_to_neon.py` (not a live dependency) | [SCHEMA.md](SCHEMA.md) |
| **Structured Logging** | JSON-lines log module (`services/logging.py`) writing to `.commander/logs/structured-YYYY-MM-DD.log`; respects `COMMANDER_LOG_LEVEL` | — |
| **Analytics page** | Per-project analytics at `/project/{slug}/analytics` with Status, Trends, and Calibration sub-tabs; metrics sourced from local sprint/estimate files (no Neon dependency) | [SCHEMA.md](SCHEMA.md) |
| **Global Notes** | Always-available notes pane in the dashboard left sidebar, debounced autosave to `.commander/notes.json` via `GET/PUT /api/notes` | — |
| **Live Browser UAT** | agent-browser drives browser UAT steps automatically instead of MANUAL; BA tags testable steps `[agent-test]` and step screenshots attach to the UAT test report | — |
| **Impeccable design wiring** | BA and coder agents receive impeccable design contracts; visual targets tracked against an `impeccable detect` baseline | — |
| **Activity log linking** | Activity-log agent rows render `<role> <action> #<issue>` with clickable GitHub issue links; label transitions and sprint lifecycle (started/finished/rerun) emit scoped activity events | — |
| **API** | REST API consumed by the dashboard and agent hooks | [docs/features/api.md](docs/features/api.md) |

---

## How the estimator works

The Sprint Estimator is **skipped by default** — pass `--no-skip-estimator` to `sprint_manager.py` to enable it. When enabled it runs at the start of every sprint (after the sprint branch is created, before any coder agents are dispatched), using a single Claude Code subprocess to estimate effort, files impacted, and risks for all backlog tickets in one pass.

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
dashboard runs as a macOS LaunchAgent. This starts it automatically on user login and
restarts it if the process crashes.

> **launchd is the authoritative runner for unattended operation.** Use it whenever
> Commander runs without your direct attention — remote travel, overnight, or background.
>
> **tmux** is for attended local development only (interactive sessions where you are
> watching the terminal). Do not use tmux for unattended or remote operation.

### Install

Run from inside the PRD clone (`~/dev/commander/prd` or your equivalent PRD directory):

```bash
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

### Prevent sleep on AC power

By default macOS may sleep even when plugged in. Prevent this so the service survives overnight:

```bash
sudo pmset -c sleep 0
```

Verify: `pmset -g | grep " sleep"` should show `sleep 0` in the AC power section.

### Enable auto-login

Auto-login is required for launchd user agents to start automatically after a reboot or
power outage — without auto-login the user session never opens and the agent never loads.

System Settings → General → Login Items & Extensions → enable auto-login for your service account.

Reboot to confirm: after the machine powers on, the service should be running without any
manual intervention.

### Tailscale enrollment and firewall

Tailscale provides the secure tunnel for remote access. The macOS Application Firewall
restricts port 8000 so it is reachable only over Tailscale, not the open internet.

**Enroll:**
1. Install Tailscale and log in.
2. Run `tailscale up` and complete device authorization in the Tailscale admin console.
3. Enable MagicDNS in the Tailscale admin console (DNS tab).

**Restrict to Tailscale only (Application Firewall):**
1. System Settings → Network → Firewall → turn on the firewall.
2. Click **Options…** and set incoming connections for Python / uvicorn to **Block all incoming connections**.
3. Tailscale operates below the Application Firewall layer, so Tailscale-routed traffic is
   still permitted regardless of this setting.

Verify enrollment: `tailscale status` shows the node as Connected with a `100.x.x.x` address.

### ntfy push notifications

Commander uses [ntfy.sh](https://ntfy.sh) for mobile push alerts when a sprint finishes or
an agent reports an error.

1. Install the ntfy app on your iPad or phone.
2. Choose a unique topic name (e.g. `commander-yourname`).
3. In the ntfy app, subscribe using the URL: `https://ntfy.sh/<your-topic>`
4. Set `NTFY_TOPIC_URL=https://ntfy.sh/<your-topic>` in `apps/dashboard/.env`.
5. Send a test notification to confirm delivery:
   ```bash
   curl -d "Commander test" ntfy.sh/<your-topic>
   ```
   Expected: a notification appears on your iPad or phone within a few seconds.

> Self-hosted ntfy setup is out of scope. Use the public ntfy.sh service for simplicity.

### Uninstall

```bash
bash scripts/uninstall_launchd.sh
```

Unloads the service and removes the plist from `~/Library/LaunchAgents/`.

### Verify

```bash
# Service is registered and running (exit code 0):
launchctl list | grep commander

# Port 8000 is listening (prints the PID; empty means nothing is bound):
lsof -ti tcp:8000

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
├── docs/                    # Documentation (standard layout, all projects)
│   ├── quickstart.md        # 5-minute install and first run
│   ├── tutorial.md          # Full walkthrough
│   ├── workflow.md          # Bulk Create → Run → Finish/Rerun
│   ├── milestones.md        # Auto-maintained sprint history
│   ├── features/            # Per-feature guides
│   ├── bulk-create/         # Saved bulk-create prompts and outputs
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
disk failure, or a machine wipe — since both files are gitignored via `.gitignore`.

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

## Database

Commander stores all live state in **SQLite** (`DB_PATH`, e.g. `dashboard.db`) plus
local JSON files. SQLite is the primary — and only live — store: the dashboard and
sprint manager run fully without any external database. There is no startup sync
and nothing writes to a remote database mid-flow (issue #758).

### Neon (optional export target)

Neon (Postgres) is an **optional export target** for external reporting — not a
runtime dependency. Setting it up is only needed if you want a Postgres copy of
your sprint/project data; skip this entire section otherwise.

1. **Create a Neon project** — sign up at [neon.tech](https://neon.tech) and create a new project. Copy the connection string from the project dashboard.

2. **Set `DATABASE_URL`** — copy `.env.example` to `.env` at the repo root and fill in your Neon connection string:
   ```bash
   cp .env.example .env
   # Edit .env and set DATABASE_URL=postgresql://user:password@host/dbname?sslmode=require
   ```

3. **Run migrations** — apply the schema (Alembic migrations and the SQLAlchemy
   models remain intact for the export target):
   ```bash
   alembic upgrade head
   ```

4. **Run the export** — push a snapshot of the local SQLite data + `projects.json`
   to Neon. This is the *only* code path that writes to Neon:
   ```bash
   DATABASE_URL=postgresql://... python scripts/export_to_neon.py
   # Preview without writing:
   python scripts/export_to_neon.py --dry-run
   ```
   The script exits cleanly (0) on success and exits 1 if `DATABASE_URL` is unset.
   Re-running is safe — existing rows are upserted/skipped.

Verify the connection at any time with:
```bash
python -c "from services.sprint_manager.neon_db import get_engine; print(get_engine().connect())"
```

---

## Docs

**Start here**
- [Quick start](docs/quickstart.md) — install and first run in 5 minutes
- [Tutorial](docs/tutorial.md) — full walkthrough and multi-clone setup
- [Workflow](docs/workflow.md) — Bulk Create → Run Sprint → Finish/Rerun
- [Milestones](docs/milestones.md) — what each sprint shipped

**Reference**
- [Dashboard](docs/features/dashboard.md)
- [Sprint Manager](docs/features/sprint-manager.md)
- [API reference](docs/features/api.md)
- [Bulk-create records](docs/bulk-create/)
- [UAT changelogs](docs/changelog/uat/)
- [PRD changelogs](docs/changelog/prd/)
- [Testing sandbox](docs/testing/sandbox-repo.md)
- [Sprint manager config](.commander/README.md)
- [Travel playbook](docs/TRAVEL_PLAYBOOK.md)
