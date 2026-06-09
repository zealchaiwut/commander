# Impeccable Detect — Gate Pass-Rate Baseline

Tracking doc for issue #713 (wire impeccable design skills into the BA and coder
agents). It records the `impeccable detect` design-gate baseline so the pass-rate
change from threading design contracts into the agents is measurable, and so we
can confirm **no regression** on tickets that already passed.

## What the gate is

The design gate runs `npx impeccable detect <static_dir>` against the coder's
worktree during quality gates (`_run_design_gate` in
`services/sprint_manager/sprint_manager.py`). Exit code `0` = no UI
anti-patterns; non-zero bounces the ticket back to SIT. The gate skips
gracefully when there is no frontend to scan or `npx` is unavailable.

## How the pass rate is tracked

Every dispatch records a `GateResult(gate="design", passed=..., skipped=...)`
and emits a `gate_failed` structured-log event on failure. To compute the
baseline and ongoing pass rate, count `design` gate results over a window of
frontend tickets:

- **Numerator:** design gate results with `passed=True` and `skipped=False`.
- **Denominator:** design gate results with `skipped=False`.

Pull from the structured logs / gate-result history per sprint.

## Baseline (pre-#713)

Before #713, frontend tickets carried no explicit design contract: the BA wrote
vague AC ("follows design system") and the coder guessed at tokens, so the
`impeccable detect` gate failed repeatedly on first dispatch and tickets churned
through the fix-loop. This is the baseline the #713 wiring (design-token
extraction in the BA, `context.mjs` injection into coder/tester dispatch) is
expected to improve.

## Expectation after #713

- Frontend-ticket `impeccable detect` pass rate **increases** versus the
  pre-#713 baseline.
- **No regression:** tickets that already passed the detect gate continue to
  pass — the change only adds design context, it does not tighten the detector
  or its rules (those are out of scope per the issue).
