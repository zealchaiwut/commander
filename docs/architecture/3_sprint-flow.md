# 3. Sprint flow (work flow)

[← Contents](0_content.md) · [← Prev: App / Dashboard architecture](2_app-dashboard-architecture.md) · [Next: Agents →](4_agents.md)

> Lifecycle home: this section owns the sprint and ticket lifecycle definitions (happy path). Failure paths live in [section 6](6_failure-and-recovery.md); where lifecycle state is stored lives in [section 1](1_state-and-source-of-truth.md). Active redesign: [sprint-lifecycle.md](sprint-lifecycle.md) (agreed design) and `docs/milestones/sprint-lifecycle-redesign.md` (milestone tracker).

## 3.1 The core loop

```
BA writes ticket → Coder implements → Tester verifies → UAT sign-off (human) → develop → master
```

| Stage | Agent / actor | Outcome |
|-------|---------------|---------|
| **Backlog** | Human or BA | GitHub issue with acceptance criteria + UAT steps |
| **Sprint queue** | Dashboard / human | Issue gets `sprint-N` + `backlog` labels |
| **In progress** | Coder (Claude Code) | Feature branch, push, label → `SIT` |
| **SIT** | Tester (Claude Code) | Pytest per AC, merge to develop/sprint branch, label → `UAT` |
| **UAT** | Human | Sign-off from dashboard; issue closed |
| **Done** | Human | `develop` → `master` merge (manual) |

Progress UI counts **`done + uat`** as completed work; UAT is surfaced separately as awaiting sign-off.

## 3.2 Sprint lifecycle

Unified enum (target — see [sprint-lifecycle.md](sprint-lifecycle.md)):

| State | Trigger |
|-------|---------|
| `draft` | Sprint column created; tickets being arranged |
| `planned` | Preflight confirmed; not yet dispatched |
| `running` | `sprint_manager.py` process alive |
| `ready_to_merge` | All tickets passed; awaiting Merge Sprint |
| `needs_rework` | Failure, crash, user stop, or partial completion |
| `partial_finished` | Derived: child sprint exists but not yet `completed` |
| `completed` | Merge Sprint done; PRs/issues closed |
| `deleted` | Removed via dashboard |

**Re-run:** always creates a **child sprint** (e.g. `sprint-68.1`); same-label re-dispatch is abolished. Confirmation modal (`rr-modal` in UI) lists tickets before create.

**Pipeline mode** (opt-in): coder works ticket N+1 while tester validates N — one coder + one tester concurrent; level barrier between DAG levels.

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

During an active run (`COMMANDER_SPRINT_RUNNING=1`), only status labels may change — sprint labels and all others are frozen (`RUN_MUTABLE_LABELS`).

## 3.4 Estimation step

| When | What |
|------|------|
| After BA creates ticket | Per-issue estimator (Haiku) → `.commander/estimates/issue-<N>.json` |
| At sprint start | Sprint estimator scans backlog (Sonnet) |
| Board preview | `GET /api/sprints/{label}/preview-dag` — dispatch levels, conflicts, capacity |

Size scale: S=5min, M=15min, L=30min, XL=90min (full pipeline wall-clock). `sprint_budget_minutes` (default 180) drives the capacity bar.

## 3.5 Sprint planning

Dashboard Sprint tab — **Board / Running / History**:

- **Board:** multi-select backlog, what-if delta, capacity budget, execution-preview mini-rail
- **Running:** level-rail node board, live metrics, per-node log tabs
- **History:** sprint ledger, run-stats, gantt, post-sprint reconciliation checklist

Create sprint: verified sequence (label → ticket labels → plan file) with retry + rollback (#857).
