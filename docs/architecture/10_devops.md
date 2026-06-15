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

### Environments at a glance

| Stage | Type | Branch | Port | Clone | Host |
|---|---|---|---|---|---|
| SIT | ticket label (tester gate) | `feature/*` | — | `tester/` | local M4 |
| UAT | label + local server | `develop` | 8001 | `uat/` | local M4 |
| PRD | live server | `master` | 8000 | `prd/` | remote mac mini |

**SIT is a label/stage, not an environment.** The coder pushes a feature branch
and flips the ticket to `SIT`; the tester then runs pytest against that branch
**in the `tester/` clone**, merges to `develop` on green, and flips the label to
`UAT`. There is no `sit/` clone — the four clones (`prd`, `uat`, `coder`,
`tester`) are the whole topology.

A server's stage follows its **branch**, not its name: a dashboard running
`develop` is UAT by definition; **PRD ≡ `master`**.

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

**Decision (2026-06-15):** re-affirmed — **the local M4 is UAT only** (`uat/`,
develop, :8001); the **mac mini is the sole PRD** (`prd/`, master, :8000). One
authoritative production, no split-brain. Do not run `start_prd.sh` on the M4.

> **Known drift (follow-up, not yet fixed):** the local launchd service
> `com.commander.dashboard` currently runs the **`uat/` clone's venv** but sets
> `ENVIRONMENT=prd` on **port 8000** — a local instance mislabeled as PRD, which
> contradicts "PRD ≡ master, remote-only." Reconcile later: either point launchd
> at a true `prd/`+master on the mini, or relabel the local service to
> UAT/:8001. (Also: the local `prd/` clone is currently on a `docs/*` branch, not
> `master` — should return to a clean master mirror.)

### Never mutate git in the serving clone

A sprint mutates git **only in the `coder/` and `tester/` worktrees** — verified:
every `git checkout`/branch/commit in `sprint_manager.py` is pinned to
`cfg.worktree_coder` / `cfg.worktree_tester`, and the dashboard's
attachments-branch step clones into a `tmpdir`. The sprint does **not** touch the
dashboard's own clone.

The collision that does happen is the **serving clone doing double duty** — it is
both the dashboard's checkout and (if you dev there) the operator's working clone.
Two rules keep them from stepping on each other:

1. **STATUS.md is write-only.** `scripts/sync_status_md.py` rewrites the file on
   disk but no longer `git commit`s it. The dashboard serves it from disk; the
   old ~30s auto-commit moved the serving clone's branch (the recurring
   `chore: auto-sync STATUS.md` commits), diverging it from origin — which broke
   `Deploy` (ff-only pull) and collided with operator branch state.
2. **Don't branch-switch the serving clone for unrelated dev.** Do operator dev
   on `hotfix/*` / `feature/*` branches in the dev clone, and let `develop` there
   stay a clean mirror of origin so `Deploy` always fast-forwards. Avoid editor
   source-control checkouts in the clone a dashboard/sprint is actively using.

## 10.2 Promotion path

develop → master, deploy-to-production.

_TODO_

## 10.3 Controlled projects' devops

How Commander ships their code.

_TODO_

## 10.4 Health monitoring

`/api/health`, alerts.

_TODO_
