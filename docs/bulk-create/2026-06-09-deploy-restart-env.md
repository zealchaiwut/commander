# Per-project Deploy & Restart + Render-style ENV editor

**Date:** 2026-06-09
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

Give Commander a **Deploy** tab so each project's environments can be deployed
and restarted from the dashboard. Scoped to **two projects** for now: commander
(self, Mac mini) and perf-coach (PRD on Render, UAT on Mac mini). Plus a
Render-style **environment-variable editor**.

## Decisions (locked with the user)

- **Deploy PRD does NO git merge/PR.** The human merges develop→master and
  closes the PR manually. "Deploy" only does `git pull` of the **already-merged**
  branch tip + restart. (PRD pulls `master`, UAT pulls `develop`.)
- **Render** deploy/restart goes through the **Render API** (API key + service id).
- **Mac-mini restart**: implementer's choice — use `launchctl kickstart -k <label>`
  for launchd-managed services, with a detached self-restart helper for the
  dashboard restarting itself.
- **Trigger UI**: a dedicated **Deploy tab** in the project view.
- **ENV editor**: Render-style key/value table; values shown as masked fields
  with a reveal (eye) toggle. Single-user, so plaintext storage is fine for now
  — skip encryption/secret-vault.

## Deploy target matrix

| Target | Host | Deploy | Restart |
|--------|------|--------|---------|
| Commander PRD | Mac mini (self) | `git pull master` in prd clone → restart | `launchctl kickstart -k com.commander.dashboard` |
| Perf-coach PRD | Render | Render API trigger deploy | Render API restart |
| Perf-coach UAT | Mac mini | `git pull develop` in uat clone → restart | `launchctl kickstart -k <perf-coach uat label>` |

## Existing building blocks

- Per-project **environments** (prd/uat/coder/tester paths) already in settings.
- `start_prd.sh` / `start_uat.sh` / `stop_all.sh`, launchd plist
  `com.commander.dashboard` (KeepAlive + RunAtLoad), `install_launchd.sh`.
- `/api/health` for post-restart readiness polling.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add a per-environment deploy configuration to project settings. For each project environment (prd, uat) store a deploy target: host = "local" or "render". For host=local: { working_dir (the git clone path, default to the existing environment path), branch (prd→master, uat→develop), launchd_label (optional; e.g. com.commander.dashboard), restart_script (optional fallback if no launchd label) }. For host=render: { render_service_id, render_api_key (masked secret) }. Persist via the existing settings store (settings_repo with the JSON fallback so it works without Neon). Add GET/PUT /api/projects/{slug}/deploy-config returning/saving the per-env config; mask the render_api_key in GET responses (return a boolean "render_api_key_set" plus a masked value, accept a new value on PUT). Scope: seed sensible defaults for commander (prd=local launchd com.commander.dashboard, uat=local) and perf-coach (prd=render, uat=local). Acceptance: deploy config for each env can be read and saved; the Render API key is never returned in cleartext.
---
Implement local deploy + restart actions for Mac-mini-hosted environments. Add POST /api/projects/{slug}/environments/{env}/deploy and POST /api/projects/{slug}/environments/{env}/restart. DEPLOY (local): run `git pull --ff-only <branch>` in the configured working_dir, then trigger the restart. Do NOT merge, push, or open PRs — pull only (the human handles master merges manually). RESTART (local): if a launchd_label is configured, run `launchctl kickstart -k <label>` (clean restart; KeepAlive respawns on crash); otherwise run the configured stop+start scripts. SELF-RESTART SAFETY: when the env being restarted is the dashboard itself (commander prd / com.commander.dashboard), the request cannot restart in-process — spawn a DETACHED helper that sleeps ~1s and then kickstarts, and return 202 immediately so the response flushes before the process dies. Stream/return the git pull output and the new HEAD sha. Reject paths/labels not present in the saved deploy config. Acceptance: clicking Deploy on a local env pulls the latest commit and restarts it; the dashboard can restart itself without hanging the request; a crash-restart still works via launchd KeepAlive.
---
Add a launchd service for perf-coach UAT on the Mac mini so it can be restarted like the dashboard. Generalize install_launchd.sh into a parametrized installer that writes a plist for an arbitrary project env: label (e.g. com.perfcoach.uat), working dir, venv uvicorn path, port, and ENVIRONMENT value, with RunAtLoad + KeepAlive + log paths under ~/Library/Logs/. Install the perf-coach UAT service (its own port, distinct from commander's 8000/8001). Wire its launchd_label into perf-coach's uat deploy config (previous ticket) so Restart uses `launchctl kickstart -k com.perfcoach.uat`. Acceptance: perf-coach UAT runs under launchd on the mini, survives reboot/crash, and Restart from the dashboard relaunches it.
---
Integrate the Render API for perf-coach PRD deploy and restart. Using the stored render_service_id + render_api_key, implement: DEPLOY = POST a new deploy to the Render API for the service (Bearer auth); RESTART = trigger a restart via the Render API (use the deploy/restart endpoint Render exposes for the service type). Add status polling: GET the latest deploy's status (queued/building/live/failed) so the UI can show progress. All calls server-side; never expose the API key to the frontend. Handle 401 (bad key) and 404 (bad service id) with clear errors. Wire these into the same POST /api/projects/{slug}/environments/{env}/deploy and /restart endpoints, dispatching to the Render path when host=render. Acceptance: clicking Deploy on perf-coach PRD triggers a Render deploy and the UI reflects build→live status; Restart restarts the Render service; the API key stays server-side.
---
Build the Deploy tab in the project view. Add a "Deploy" tab (top-level project nav) that lists each configured environment (prd, uat) as a card showing: host badge (local / Render), branch, current commit sha (from /api/health git_sha for local, or Render's live deploy for render), last-deploy time, and a status pill. Each card has Deploy and Restart buttons that call the env endpoints, with a confirm step. While an action runs, show progress (git pull output / Render build status) and a log tail. For a self-restart of the dashboard, show a "Restarting… reconnecting" overlay that polls /api/health until the server is back, then refreshes. Acceptance: the Deploy tab shows both commander and perf-coach environments; Deploy and Restart work per env with visible progress; restarting the dashboard shows the reconnect overlay and recovers automatically.
---
Add a Render-style environment-variable editor to project settings. For a selected project environment, render the variables as a Render-like key/value table (see the Render Environment screenshot as the UX reference): a read view with each value masked (••••) and a per-row reveal (eye) toggle, an Edit mode that turns rows into editable inputs with add-row / delete-row, and a Save that writes back. Source/target = the env's .env file at its working_dir (e.g. <prd>/apps/dashboard/.env); GET /api/projects/{slug}/environments/{env}/env-vars parses it to key/value pairs, PUT writes it back preserving order and comments where practical. Single-user dashboard, so store/return values in plaintext (no encryption) — masking is display-only via the reveal toggle. Acceptance: I can view, reveal, edit, add, and remove env vars for a project environment from the dashboard, and the changes persist to that environment's .env file.
```

## Notes for the implementer

- **Self-restart** is the sharp edge: the dashboard process can't restart itself
  synchronously. Detached helper + immediate 202 + health-poll reconnect overlay.
- **launchd kickstart** is preferred over kill+start: `kickstart -k <label>`
  restarts cleanly and `KeepAlive` covers crashes.
- **Render**: confirm the current Render API endpoints for deploy + restart +
  deploy-status at implementation time (they evolve); Bearer auth with the
  stored key; everything server-side.
- **ENV editor** intentionally skips encryption (single-user). Mask in the UI
  only. Don't log values.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
