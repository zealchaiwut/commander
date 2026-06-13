# 10. DevOps process

*Commander itself + controlled projects.*

[← Contents](0_content.md) · [← Prev: Multiple projects](9_multiple-projects.md) · [Next: Remote work →](11_remote-work.md)

## 10.1 Commander's own deployment

Commander runs as **two clones side by side**, each pinned to a branch, a port,
and its own hook target. Nothing is hardcoded to a port — every layer reads it
from that clone's config, so the two never collide.

```
~/dev/commander/
  prd/   → master,  :8000, ENVIRONMENT=prd, hooks → :8000   (the authoritative deploy)
  uat/   → develop, :8001, ENVIRONMENT=uat, hooks → :8001   (local hotfix / test)
  coder/ tester/  → develop, agent worktrees
```

**PRD is the remote mac mini, and only that.** The `prd/` clone on the remote
runs `start_prd.sh` (:8000, master) under **launchd** (the authoritative
unattended runner), reachable over **Tailscale DNS**. This is the single live
deploy; "Deploy → PRD" promotes here. Do **not** run `start_prd.sh` on the
working machine for day-to-day dev — it reuses the PRD name + port for a dev
instance, which is misleading and would clash with a real PRD on the same port.

**Local development happens in the `uat/` clone on :8001.** On the working
machine, run the dashboard from `uat/` via `start_uat.sh` (`ENVIRONMENT=uat`,
develop or a `hotfix/*` branch off develop). This is where you watch hooks fire,
exercise a hotfix, and verify the frontend before promoting. Set it up once with
`scripts/setup_uat_env.sh` (idempotent): it clones `uat/`, checks out develop,
makes the venv, writes `.env` (`PORT=8001`), and — importantly — writes the UAT
hook target into the clone's `.claude/settings.json`.

### How the port reaches each layer (so "change all hooks" is never needed)

| Layer | Reads the port from |
|---|---|
| Dashboard | `start_prd.sh` / `start_uat.sh` → `.env` `PORT` (8000 / 8001) |
| **Hooks** | `HOOK_POST_TARGET` in that clone's `.claude/settings.json` (default `http://localhost:8000/api/agent-event`; UAT clone is set to `:8001` by `setup_uat_env.sh`) |
| Agent dispatch | `cfg.api_url` (sprint.yaml `dashboard.api_url`) or `DASHBOARD_API_URL` — must match the running dashboard (UAT → :8001, PRD → :8000) |

Hook code is **never** hand-edited per environment. Each clone carries its own
`HOOK_POST_TARGET`, so the agents it dispatches post their events to that clone's
dashboard. The same goes for the agent-dispatch `api_url`: keep the UAT clone's
sprint.yaml `dashboard.api_url` on `:8001`.

**Decision (2026-06-13):** local dev = the `uat/` clone on :8001; the PRD name +
the `prd/` clone are reserved for the remote mac mini. The working machine's
`prd/` clone stays on master only for mirroring the remote, and is not the dev
dashboard.

## 10.2 Promotion path

develop → master, deploy-to-production.

_TODO_

## 10.3 Controlled projects' devops

How Commander ships their code.

_TODO_

## 10.4 Health monitoring

`/api/health`, alerts.

_TODO_
