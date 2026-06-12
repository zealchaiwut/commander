# 6. Failure & recovery semantics

*The unhappy-path side of the lifecycle defined in [section 3](3_sprint-flow.md).*

[← Contents](0_content.md) · [← Prev: Concurrency & locking](5_concurrency-and-locking.md) · [Next: Git / branch strategy →](7_git-branch-strategy.md)

## 6.1 Ticket-level

Tester-rejected, needs-rework, retry.

See [section 4.6](4_agents.md#46-coder-dispatch-reliability-4a4f) for the locked decisions:

- **4a** — `record_failure()` sidecar from all failure exits (not just tester gate)
- **4b** — bounded fix-loop (`COMMANDER_MAX_FIX_ROUNDS`, default 3); early-abort on duplicate failure signature; `RETRY_EXHAUSTED` after K rounds

Logic failures (test/design/merge-boundary) accumulate context and retry. Infrastructure failures (crash, hang, rate-limit) stay on existing paths without consuming fix rounds.

## 6.2 Sprint-level

Stop, cancel, partial completion.

_TODO_

## 6.3 Process death

Server restart mid-sprint, PID resume.

Pairs with [section 1.3](1_state-and-source-of-truth.md) reconciliation and [4c nudge-before-kill](4_agents.md#46-coder-dispatch-reliability-4a4f). Buffered stdout without flush discipline loses final agent lines on kill — addressed by structured logger Phase 1 ([2.2b](2_app-dashboard-architecture.md#22b-backend-logging--structured-logger-disk-first-phase-1-neon-later-phase-2)).

## 6.4 Graceful degradation

Estimator timeout, missing CLI, stale assets.

Generalize into doctor preflight ([4e](4_agents.md#46-coder-dispatch-reliability-4a4f)): CLI presence, auth, worktree health checked before dispatch. `dispatch-blocked` and `design_docs_missing` already short-circuit with `record_failure()`.

## 6.5 Bulk-create failure

Oversized POST, edit-and-retry.

_TODO_
