# Test suite rehabilitation — hang, 64 failures, timeout guard

**Date:** 2026-06-11
**Sprint label:** NEW
**Default labels:** bug
**Status:** drafted

Found while auditing: the suite was dead at collection (fixed on
hotfix/2026-06-11-manual), and underneath that, a full run **hangs** at
test_697 (documentor once-per-sprint) and carries **~64 failures** by the 38%
mark — invisible rot since the repo restructure, because testers only run
per-feature tests and nothing ever runs the whole suite. Known failure
classes: schema drift (e.g. `sprint_tickets has no column named
estimated_size` — fixtures create tables that lag the models), refactor-stale
source asserts, moved-code wiring guards.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add pytest-timeout and fix the hanging documentor test. Install pytest-timeout (add to requirements), set a default per-test timeout of 60 seconds in pytest configuration, and fix the test that currently hangs a full suite run forever: test_697__run_documentor_once_per_sprint, TestAC2DocumentorCalledOnceAfterLoop::test_three_passing_tickets_calls_documentor (likely waiting on a subprocess or network call that needs mocking). Acceptance: a full `pytest tests/` run terminates on its own, and no single test can block the suite for more than a minute.
---
Fix the schema-drift test failures: fixtures must create the CURRENT schema. Several test fixtures build sprint_tickets and related tables from outdated definitions, so inserts fail with errors like "table sprint_tickets has no column named estimated_size" (test_580__ticket_metrics_persist and others). Point fixtures at the real table-creation/migration code instead of hand-rolled CREATE TABLE copies, so the schema can never drift again. Acceptance: test_580 passes, and grepping tests for hand-rolled sprint_tickets DDL finds none.
---
Burn down the remaining full-suite failures to green. With collection fixed, the hang fixed, and schema drift fixed, triage every remaining failure in a full pytest run: fix tests that assert outdated implementation shapes (rewrite against current behavior or delete with justification), fix any real bugs they reveal, and document each skip with a reason. Acceptance: `pytest tests/` exits green (0 failures; skips allowed with reasons) and the run is repeatable.
---
Add a suite-health gate so the test suite cannot rot invisibly again. After each sprint's last ticket merges (or as a post-sprint step), run the FULL test suite in the tester worktree and record the result (pass/fail counts, duration) in the sprint summary Stats table and the structured log. A failing or hanging full suite marks the sprint summary with a visible warning. Acceptance: every sprint summary reports full-suite health, and a broken suite is impossible to miss.
```

## Notes

- Collection fix + 7 failure repairs already landed on
  `hotfix/2026-06-11-manual` (PR #845): stale path in
  test_open_tickets_metric__15 (killed ALL collection), test_764 subset
  assert + routers-aware wiring guard, test_689 trio (state filename +
  token totals).
- Failure-class evidence preserved in /tmp/suite-verbose.log on the laptop
  (64 FAILED by 38%, last line = the hang point).

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
