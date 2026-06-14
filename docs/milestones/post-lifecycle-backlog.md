# Milestone — Post-Lifecycle Backlog

> **Status:** open. Created 2026-06-14 from a review of open issues on
> `develop` after the sprint-lifecycle redesign closed
> ([`sprint-lifecycle-redesign.md`](sprint-lifecycle-redesign.md)).
> Groups the pending work into topics so it can be sprint-planned.
>
> Label legend below reflects each issue's GitHub state at review time
> (`UAT` = awaiting human sign-off, `SIT` = in test, `in-progress`,
> bare = backlog). Re-check labels before planning — some may have moved.

**Decisions for the operator are flagged `⟶ DECISION` inline.** These are the
points I am not deciding for you.

---

## 1. Code-review debt — backend

Follow-up cleanups opened by the reviewer agent. Mostly low-risk tech debt;
several touch `sprint_manager.py`.

| Issue | What |
|-------|------|
| #1016 | Unrelated lint-suppression churn in `sprint_manager.py` |
| #1014 / #1004 | Extract duplicated Cline→claude-code escalation block into a helper |
| #1005 | Remove dead noqa-suppressed assignments in `sprint_manager` |
| #1003 | Activity backend chip mislabels escalated coder runs |
| #969 | Prevent feature-branch contamination with foreign test files |
| #968 | Record sprint-summary diff range after all branches merged |
| #967 | Remove unused `_seed_agent_runs` helper from test_893 |
| #966 | Strengthen `test_ac_utcnow_iso_uses_utc_timezone` to verify `timezone.utc` |
| #892 | Derive failure-sidecar base path from project-root, not hardcoded `~/dev` (`in-progress`) |
| #874 | Reuse cached per-project summary in home daily artifact |
| #873 | Home recap Regenerate should not invalidate per-project caches |
| #872 | Validate non-empty text on project-todo create/PATCH |
| #868 | `doctor.py` claude check: probe auth, not just `--version` |
| #851 | Split packed dict keys in `get_sprint_management_issues` |
| #850 | Fix inaccurate tiebreak comment in `run_stats_service._largest_remainder` |
| #849 | Add timeout to `gh` subprocess calls in stale-branch scan/cleanup |

⟶ DECISION: #1014 and #1004 look like duplicates of the same Cline-escalation
extraction — close one as dup before sprinting.

## 2. Code-review debt — frontend

| Issue | What |
|-------|------|
| #1013 | Wire #919 Cline opt-in UI (run-sprint checkbox + Running-view badge) |
| #1000 | Keep background pill count live during batch reestimate |
| #999 | Clear/refresh background pill and unlock board on batch completion (`UAT`) |
| #998 | Per-request timeout to batch reestimate loop |
| #997 | Collapse duplicate pass branches in conflict step |
| #996 | Reconcile preflight stepper to shared progress component |
| #995 | Guard `_pfStepperAnimate` body with try/catch |
| #994 | Timeout/AbortController on preflight-fix SSE fetch |
| #985 | Remove no-op eslint-disable around `/* global */` in `board-render.js` |
| #984 | Remove hardcoded ~2.3s setTimeout delays gating Run Sprint button |
| #983 | Surface `_pfRunAutoFix` per-ticket errors in stepper notes |
| #982 | Guard `_pfStepperAnimate` so Run Sprint button can't get stuck disabled |
| #876 | Format recent-activity timestamp to short local time on home brief |
| #867 | Icon/color pickers: Arrow-key keyboard navigation |
| #838 | Dedup log-colorize between `logpanel.js` module and legacy `static/log-colorize.js` |
| #837 | Commit `package-lock.json` and use `npm ci` in frontend CI |

⟶ DECISION: #982/#984/#995 all harden the Run-Sprint stepper against getting
stuck — bundle into one ticket rather than four.

## 3. Shared progress component rollout

Extract one reusable progress/activity component, then migrate each long-running
op onto it. Several already shipped to `UAT`/`SIT`.

| Issue | What | Label |
|-------|------|-------|
| #928 | Extract reusable progress/activity component | `SIT` |
| #929 | Use it while finishing a sprint | `SIT` |
| #930 | Batch reestimate progress bar | `UAT` |
| #931 | Bulk Create uses shared component (Tier 1) | `UAT` |
| #933 | Pre-flight checks as live stepper checklist | `UAT` |

⟶ DECISION (#986): the #933 stepper code was merged via the #930 branch
**untested** — verify it or revert before relying on it.

## 4. Activity / running-pane redesign

| Issue | What | Label |
|-------|------|-------|
| #901 | Redesign Activity view: tokens, durations, inline errors | `UAT` (size-XL) |
| #903 | Set Activity as default project landing view | `UAT` |
| #926 | Restructure running pane: orchestrator log, status pills, inline logs | `UAT` (size-XL) |
| #927 | Running pane: two-queue coder/tester lane view | `UAT` (size-XL) |

These are large and already in UAT — mostly an operator sign-off question, not
new build. ⟶ DECISION: sign these off or list rework.

## 5. Advisor — finish or revert

Daily advisor agent for next-build suggestions. Partially shipped; #1015 flags
it as half-done.

| Issue | What | Label |
|-------|------|-------|
| #881 | Add daily advisor agent for next-build suggestions | size-L |
| #882 | Roadmap Suggestions panel with Accept/Dismiss | `UAT` |
| #883 | Advisor maintains 2–5-sprint look-ahead on roadmap | `UAT` |
| #884 | Advisor suggestions section in morning brief | size-L |
| #1015 | **Complete or revert** half-shipped #881 bundled into sprint-71.1 | follow-up |

⟶ DECISION: resolve #1015 first — decide whether the advisor ships or gets
reverted. Everything else in this group hangs off that.

## 6. Planner & sign-off

| Issue | What | Label |
|-------|------|-------|
| #862 | Pending sign-off state with Approve/Reject on planned sprints | `UAT` (size-L) |
| #932 | Sprint-kickoff stepper for run/re-run flow | `needs-rework` (size-L) |

⟶ DECISION (#932): in `needs-rework` — re-run into a child sprint or descope.

## 7. Test-suite rehab (sprint-72.1)

Burn the suite back to green and gate it.

| Issue | What | Label |
|-------|------|-------|
| #885 | Add `pytest-timeout`; fix hanging documentor test | size-M |
| #886 | Fix schema-drift by pointing fixtures at real DDL | size-L |
| #887 | Burn remaining pytest failures to green | `in-progress` (size-L) |
| #888 | Full-suite health gate in sprint summaries | `SIT` (size-XL) |

This group is a prerequisite for trusting the gate on every other sprint —
recommend running it before the larger refactors.

## 8. Cline coder backend (optional cost split)

Tracked in [`../todo.md`](../todo.md) (Phase 1–3) and
[`../features/coder-backends.md`](../features/coder-backends.md). Open issues
that intersect: #1013 (opt-in UI), #1014/#1004 (escalation helper), #1003
(activity chip), #919 (referenced opt-in).

⟶ DECISION: commit to the Cline split or shelve it — the half-wired UI (#1013)
and escalation churn (#1003/#1004/#1014) are dead weight until that call is made.

## 9. Architecture refactor (from `architecture/` decision records)

Still-open items pulled from the architecture set, not yet filed as discrete
sprint tickets in all cases.

| Item | Source | State |
|------|--------|-------|
| Backend router/service/repo split | §2.2, #761, [boundaries.md](../architecture/boundaries.md) | ~130 endpoints still in monolith |
| Frontend module boundaries (2.3a) | §2.3a | PENDING |
| Frontend sitemap + page→API binding (2.3b) | §2.3b, [frontend-map.md](../architecture/frontend-map.md) | PENDING |
| Legacy route deletion at parity (2.4) | §2.4 | not started |
| 4c — nudge-before-kill | §4.6 | OPEN |
| 4f — per-area `AGENTS.md` context targeting | §4.6 | OPEN (polish) |
| Neon telemetry Phase 2 (`run_events`/`ticket_events`) | §2.2b, §13.3 | Pending |

⟶ DECISIONS (carried over from the architecture docs, unchanged):
- Refactor as **one sprint vs split** (frontend/backend) — decide at scoping.
- **File-tail vs true SSE live-stream** for the Live View log panel (2.3b).
- Is **Neon Phase 2** still the #1/#2 telemetry priority, or deferred further?
- §2.5 "no new feature work until refactor lands" was set pre-sprint-72;
  sprints 72–73 shipped features anyway — reaffirm or retire that rule.

---

## Suggested ordering

1. **Test-suite rehab (group 7)** — restore a trustworthy gate first.
2. **Sign-off / cleanup pass (groups 4, 3)** — clear the UAT pile so the board
   reflects reality.
3. **Advisor + Cline decisions (groups 5, 8)** — resolve the half-shipped
   features before they rot.
4. **Code-review debt (groups 1, 2)** — batch into one or two cleanup sprints.
5. **Architecture refactor (group 9)** — the big one; scope after the gate is green.
