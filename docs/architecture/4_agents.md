# 4. Agents

> **Decision record · status: partial** — 4a (rework context) and 4b (bounded retry loop) largely landed (#618, #787); 4c–4f open or in progress.

[← Contents](0_content.md) · [← Prev: Sprint flow](3_sprint-flow.md) · [Next: Concurrency & locking →](5_concurrency-and-locking.md)

Cross-refs: [3.1 Sprint loop](3_sprint-flow.md) · [6.1 Ticket-level failure](6_failure-and-recovery.md) · [6.3 Process death](6_failure-and-recovery.md) · [7 Git/worktree](7_git-branch-strategy.md) · [13.4 Cost](13_observability-and-cost.md)

## 4.1 Roles & responsibilities

| Role | Default model | Responsibility |
|------|---------------|----------------|
| **BA** | Sonnet 4.6 | Acceptance criteria + UAT steps; creates GitHub issues |
| **Coder** | Sonnet 4.6 (size-routed — see 4.3) | Feature branch, implement, push, label → SIT |
| **Tester** | Haiku 4.5 | Pytest against acceptance criteria, merge to develop, label → UAT |
| **Estimator** | Haiku 4.5 | Per-issue sizing metadata after ticket creation |
| **Reviewer** | — | Code review gate (when enabled) |
| **Documenter** | — | Maintains `architecture.md` / `milestones.md` AUTO regions |
| **Sprint estimator** | Sonnet 4.6 | Batch backlog scan for sprint planning |
| **Human** | — | UAT sign-off, develop → master merge |

### Sibling record: Agent capability — coder revamp + UI/testing tooling

Separate but related initiative (Claude Code CLI prompt drafted):

- TDD anchored to Acceptance criteria
- Typecheck + frontend-lint + impeccable `detect` design gate
- Gate ordering (which checks run when)
- Coder skill + per-project `DESIGN.md` / `PRODUCT.md` guards
- agent-browser E2E (non-blocking-first)
- Live-Mode how-to kept as a human-tool adjunct

Cross-refs: [3.1](3_sprint-flow.md) · [6.1](6_failure-and-recovery.md) · [2.3](2_app-dashboard-architecture.md) · [13.4](13_observability-and-cost.md)

## 4.2 Dispatch mechanism

`sprint_manager.py` orchestrates the per-ticket loop: dispatch agent subprocess → wait → run gates → transition labels.

Entry points:

- **Sprint dispatch** — `sprint_manager.py run` (sets `COMMANDER_RUN_ID`, iterates planned tickets)
- **Manual / ad-hoc** — dashboard buttons, CLI helpers (must also mint `run_id` + structured log — action item)
- **Rerun paths** — sprint re-run, per-ticket rerun, dispatch-tester-direct for SIT tickets

### Observation: sprint-dispatched coder fails; interactive `/coder` passes

Same `claude` binary, five invocation differences (verified from `_dispatch_coder` + `_build_failure_suffix`):

| # | Headless sprint | Interactive CLI |
|---|-----------------|-----------------|
| 1 | One-shot `-p` (no iteration) | Multi-turn conversation |
| 2 | Idle-silence monitor could SIGKILL mid-thought | Human keeps session alive |
| 3 | Runs in `work-coder` worktree with `-dangerously-skip-permissions` — foreign/possibly-stale tree | Clean interactive cwd |
| 4 | Prompt = `coder_prompt_template` + MERGE BOUNDARY (#311) + sprint-mode branch ceremony | Works from issue URL + live error context |
| 5 | Rework loop fragile: sidecar written only by tester gate; non-gate failures → identical generic rerun prompt | Human re-diagnoses |

## 4.3 Model selection & cost

Default models per role in CLAUDE.md. **Decision (4e):** route coder by S/M/L/XL estimate instead of hardcoded Sonnet — landed partially (#789: `_resolve_coder_model`).

Cost-relevant post-June-15 subscription changes. Token usage tracked in `token_usage` table; see [section 13](13_observability-and-cost.md).

## 4.4 Agent clones / worktrees

The `coder`, `tester`, `uat` dirs — separate git clones/worktrees per role (see [section 7.3](7_git-branch-strategy.md)).

**Decision (4d — worktree/branch hygiene):** ensure coder worktree is fresh and on the right base, not stale/foreign. Landed partially (#788: `_worktree_hygiene` — fetch, stash dirty, reset to base, validate branch). Ties to prd/uat dir debt.

## 4.5 Confirmation policy

Pause vs execute — STRICT policy in CLAUDE.md. Default is **execute**. Agents pause only for: ambiguous requirements, destructive actions, BA ticket-body confirmation gate.

## 4.6 Coder dispatch reliability (4a→4f)

Decomposition of the sprint-vs-interactive gap. **omo candidates** mapped; items marked skip require re-platforming off Claude Code.

| ID | Topic | Status | What it fixes |
|----|-------|--------|---------------|
| **4a** | Rework context | **LOCKED · largely landed** | Single `record_failure(issue, class, detail)` chokepoint from *all* failure exits (tester gate, needs-rework, hang-kill, coder crash, merge-boundary, reviewer, dispatch-blocked). Class-aware sidecar; for non-gate failures the evidence is agent-log tail + exit code/signal. Feed real error, not 10-line cap. Clear on success. |
| **4b** | Bounded retry loop | **LOCKED · landed** (#618) | Wrap coder→gates in a loop; on *logic* failure re-dispatch coder with 4a's accumulated context, gates as "done?" oracle, up to **K rounds** (`COMMANDER_MAX_FIX_ROUNDS`, default 3). Infra failures stay on existing paths. **Early-abort** if two rounds produce the same failure signature. After K → needs-rework tagged `RETRY_EXHAUSTED`. |
| **4c** | Nudge-before-kill | **OPEN** | Replace idle-silence SIGKILL with nudge/re-prompt first, kill only if truly dead; pairs with 1.3 watchdog liveness signals. *(omo Todo-Enforcer.)* `_LogActivityMonitor` removed; hang handling evolved (#787) but nudge-first not fully settled. |
| **4d** | Worktree/branch hygiene | **OPEN · partial** (#788) | Ensure coder worktree fresh and on right base. *(Fixes #3; ties §7 + prd/uat debt.)* |
| **4e** | Model routing + preflight | **OPEN · partial** (#789, doctor) | Route by estimate; generalize "claude CLI not found" into doctor preflight (CLI/auth/worktree). *(omo size-routing + doctor.)* |
| **4f** | Context targeting | **OPEN (polish)** | Per-area `AGENTS.md` vs one big `CLAUDE.md`. *(omo `/init-deep`.)* |

**omo — skip:** hash-anchored editor, multi-model orchestration, OpenCode plugin (all require re-platforming).

### Action items (section 4)

- [x] 4a — `record_failure()` chokepoint wired across failure exits
- [x] 4b — bounded fix-loop with `COMMANDER_MAX_FIX_ROUNDS` + `RETRY_EXHAUSTED`
- [ ] 4c — nudge-before-kill (settle with 1.3 watchdog)
- [ ] 4d — complete worktree hygiene + prd/uat debt cleanup
- [ ] 4e — finish estimate-based routing + doctor preflight generalization
- [ ] 4f — per-area AGENTS.md context targeting
- [ ] Manual agent entry-points: mint `run_id` + structured logger
- [ ] Agent capability revamp (TDD, lint gates, impeccable detect, E2E) — sibling record above
