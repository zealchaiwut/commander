# 3. Sprint flow (work flow)

[← Contents](0_content.md) · [← Prev: App / Dashboard architecture](2_app-dashboard-architecture.md) · [Next: Agents →](4_agents.md)

> Lifecycle home: this section owns the sprint and ticket lifecycle definitions (happy path). Failure paths live in [section 6](6_failure-and-recovery.md); where lifecycle state is stored lives in [section 1](1_state-and-source-of-truth.md). Lifecycle redesign **landed** (P0–P4, sprints 73.x): [sprint-lifecycle.md](sprint-lifecycle.md) documents the shipped behavior; `docs/milestones/sprint-lifecycle-redesign.md` is the closed tracker. The `_TODO_` stubs below are folded against that doc in a later prose pass.

## 3.1 The core loop

```
BA writes ticket → Coder implements → Tester verifies → UAT sign-off (human) → develop → master
```

| Stage | Agent / actor | Outcome |
|-------|---------------|---------|
| **Backlog** | Human or BA | GitHub issue with acceptance criteria + UAT steps |
| **Sprint queue** | Dashboard / human | Issue gets `sprint-N` + `backlog` labels |
| **In progress** | Coder (Claude Code) | Feature branch, push. In managed runs the **orchestrator** applies `in-progress` at dispatch and `SIT` after the coder finishes — the agent does not move labels |
| **SIT** | Tester (Claude Code) | Pytest per AC. In managed runs the **orchestrator** runs gates, merges via `finish_feature.py`, and applies `UAT` itself; the tester does not merge. (Manual `/tester` sessions do merge + label) |
| **UAT** | Human | Merge Sprint sign-off from dashboard; issues closed **en masse** at Merge Sprint / bulk-complete, not per ticket |
| **Done** | Human | `develop` → `master` merge (manual) |

Progress UI counts **`done + uat`** as completed work; UAT is surfaced separately as awaiting sign-off.

## 3.2 Sprint lifecycle

Unified enum — **implemented**; stored in SQLite `sprints`, read via
`sprint_state.current()`, written only by `db.transition_sprint_state()`
(see [sprint-lifecycle.md](sprint-lifecycle.md) for the full edge table,
settlement paths, and deviations):

| State | Trigger |
|-------|---------|
| `draft` | Sprint column created — implicit; no DB row exists until first Run |
| `planned` | **Deprecated (#1686)** — legacy-read only, canonicalizes to `draft`. The plan.json `signoff` preflight gate is parked (default-disabled); every sprint runs without approval |
| `running` | `sprint_manager.py` process alive (server writes it at dispatch, manager re-writes after PID lock) |
| `ready_to_merge` | Clean exit (`end_reason=natural`), or reconcile promotion |
| `needs_rework` | Ticket failure, crash, user stop, kill, or reconcile demotion |
| `partial_finished` | Derived at read time: child sprint exists but not yet `completed` — never stored |
| `completed` | Merge Sprint / bulk-complete / complete-step / reconcile-B2 (superseded ancestor) |
| `deleted` | Removed via dashboard; row deleted, snapshot kept in `sprint_history` |

**Re-run:** always creates a **child sprint** (e.g. `sprint-68.1`); same-label re-dispatch is blocked at the HTTP layer (`_reject_terminal_label_redispatch`). Confirmation modal (`rr-modal` in UI) lists tickets before create. `auto_run=false` queues the child as plan.json only (no DB row) until dispatched.

**Pipeline mode** (opt-in): coder works ahead of the tester with up to `max_coder_slots` concurrent coders (worktree pool, issues #1411/#1412); level barrier between DAG levels.

## 3.3 Ticket lifecycle & states

Enforced by `services/sprint_manager/state_machine.py` — **only** `transition()` may add/remove status labels:

| State | GitHub label(s) |
|-------|-----------------|
| `QUEUED` | `backlog` |
| `IN_PROGRESS` | `in-progress` |
| `SIT` | `SIT` |
| `UAT` | `UAT` |
| `NEEDS_REWORK` | `needs-rework` |
| `BLOCKED` | `blocked` |

During an active run, only status labels may change — sprint labels and all others are frozen (`RUN_MUTABLE_LABELS`). **Sentinel caveat:** the orchestrator sets `COMMANDER_SPRINT_RUNNING` to the *sprint label*, while `state_machine.assert_run_mutable` checks for the literal `"1"` and `update_ticket.py` treats any truthy value as locked — so the state-machine guard is effectively inert in manager subprocesses and enforcement rests on `update_ticket.py` + the orchestrator's own `_guard_sprint_labels`. *(open question: unify the sentinel.)*

`transition()` enforces **no transition graph** — any state may jump to any non-pseudo state; correctness rests on call-site discipline (unlike the sprint-level edge table). Pseudo-states `BACKLOG` (no label) and `DONE` exist in the enum without labels.

## 3.4 Estimation step

| When | What |
|------|------|
| After BA creates ticket | Per-issue estimator (Haiku) → `.commander/estimates/issue-<N>.json` (canonical path — every writer uses it) |
| At sprint start | Sprint estimator scans backlog (Sonnet) |
| Board preview | `GET /api/sprints/{label}/preview-dag` — dispatch levels, conflicts, capacity |
| On sprint finish | Calibration cache auto-refreshes; rebuildable via `POST /api/maintenance/calibration/rebuild?project=<slug>` or `scripts/rebuild_calibration_cache.py` |

Size scale: S=5min, M=15min, L=30min, XL=90min (full pipeline wall-clock). `sprint_budget_minutes` (default 180) drives the capacity bar.

Calibration resolves each completed ticket's size with a three-tier fallback — canonical estimate JSON → sprint-state estimate → `size-*` label — so tickets estimated only at creation still appear in history. See [features/estimation-lifecycle.md](../features/estimation-lifecycle.md).

## 3.5 Sprint planning

Dashboard Sprint tab — **Board / Running / History**:

- **Board:** multi-select backlog, what-if delta, capacity budget, execution-preview mini-rail
- **Running:** level-rail node board, live metrics, per-node log tabs
- **History:** sprint ledger, run-stats, gantt, post-sprint reconciliation checklist

Create sprint: verified sequence (label → ticket labels → plan file) with retry + rollback (#857).
