# 6. Failure & recovery semantics

*The unhappy-path side of the lifecycle defined in [section 3](3_sprint-flow.md).*

[← Contents](0_content.md) · [← Prev: Concurrency & locking](5_concurrency-and-locking.md) · [Next: Git / branch strategy →](7_git-branch-strategy.md)

## 6.1 Ticket-level

Tester-rejected, needs-rework, retry.

See [section 4.6](4_agents.md#46-coder-dispatch-reliability-4a4f) for the locked decisions:

- **4a** — `record_failure()` sidecar from all failure exits (not just tester gate)
- **4b** — bounded fix-loop (`COMMANDER_MAX_FIX_ROUNDS`, default 3); early-abort on duplicate failure signature; `RETRY_EXHAUSTED` after K rounds

Logic failures (test/design/merge-boundary) accumulate context and retry. Infrastructure failures (crash, hang, rate-limit) stay on existing paths without consuming fix rounds.

**Label rule:** real ticket failure → immediate `needs-rework` via `state_machine.transition()`. Gate failures mid fix-loop stay on `SIT` until fix budget exhausted.

**Hang redispatch** (#787): first hang redispatches once; second hang escalates. Disable with `COMMANDER_HANG_REDISPATCH_DISABLE=1`.

**Pipeline reject** (#737): tester rejection pushes ticket to front of coder queue; 3-attempt cap → `needs-rework`.

## 6.2 Sprint-level

Stop, cancel, partial completion.

| Event | Sprint state | Ticket labels |
|-------|--------------|---------------|
| User stop / cancel | `needs_rework` (not `cancelled`) | Failed tickets get `needs-rework`; passed tickets keep `UAT` |
| All tickets pass | `ready_to_merge` | `UAT` on passed tickets |
| Partial pass + child re-run | `partial_finished` (derived) | Child sprint carries failed tickets |
| Orphaned running sprint (PID file present, process dead) | Settled to `needs_rework` **or `ready_to_merge`** depending on whether open rework tickets remain (`end_reason=reconcile-orphan`) | Stale `in-progress`/`SIT` flagged by post-sprint checks (system B) |

`end_reason` (user stop, process lost, coder failed, …) is stored in run log and sprint summary — not as a separate lifecycle enum value.

**Orphan settling is per-sprint-button-only in practice:** the settle logic
lives in `_github_reconcile_row` for `running` rows, but the auto-reconcile
sweep skips `running` rows entirely — only `POST .../reconcile` on that sprint
reaches it. There is no standalone PID-watchdog pass in the reconcile service.
*(open question: should the sweep settle confirmed orphans automatically?)*

## 6.3 Process death

Server restart mid-sprint, PID resume.

Pairs with [section 1.3](1_state-and-source-of-truth.md) reconciliation and [4c nudge-before-kill](4_agents.md#46-coder-dispatch-reliability-4a4f). Buffered stdout without flush discipline loses final agent lines on kill — addressed by structured logger Phase 1 ([2.2b](2_app-dashboard-architecture.md#22b-backend-logging--structured-logger-disk-first-phase-1-neon-later-phase-2)).

| Failure mode | Recovery |
|--------------|----------|
| Dashboard uvicorn dies | `start_prd.sh`; kill orphan worker on :8000 if bind fails |
| Sprint manager subprocess dies | Orphan-PID watchdog; re-run via child sprint |
| In-memory state lost (`_bulk_jobs`) | Lazy reload from `.commander/bulk-jobs/{id}.json` |
| Agent subprocess hang | Hang redispatch → escalate; sidecar log tail for diagnosis |

## 6.4 Graceful degradation

Estimator timeout, missing CLI, stale assets.

Generalize into doctor preflight ([4e](4_agents.md#46-coder-dispatch-reliability-4a4f)): CLI presence, auth, worktree health checked before dispatch. `dispatch-blocked` and `design_docs_missing` already short-circuit with `record_failure()`.

| Missing capability | Behavior |
|--------------------|----------|
| `claude` CLI / auth | Dispatch blocked; doctor reports remediation |
| `agent-browser` | Browser UAT steps → `MANUAL` (not FAIL) |
| Neon / `DATABASE_URL` | Dashboard continues; Neon writes skipped |
| `PRODUCT.md` / `DESIGN.md` | `design_docs_missing` gate blocks coder dispatch |
| Estimator timeout | Ticket proceeds without size label; flagged in sprint log |

Install-time doctor (`scripts/doctor.py`, `GET /api/doctor`) vs dispatch-time auth probe (#789): "is machine set up?" vs "is auth live right now?".

## 6.5 Bulk-create failure

Oversized POST, edit-and-retry.

| Failure | Recovery |
|---------|----------|
| Server restart mid-post | Lazy job reload; retry pre-commit when `image_url_map` empty |
| Non-image attachment | Plain `[link]()` not `![img]()` — avoids broken GitHub icons |
| Double `## Attachments` injection | Idempotent guard in `_build_body_with_images` |
| Draft stuck | Re-fetch job from disk; UI polls job status endpoint |

Bulk jobs persist to `uat/.commander/bulk-jobs/{id}.json` for cross-restart recovery.
