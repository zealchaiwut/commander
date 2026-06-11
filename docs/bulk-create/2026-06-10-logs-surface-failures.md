# Surface sprint failures on the Logs page (no raw-file digging)

**Date:** 2026-06-10
**Sprint label:** NEW
**Default labels:** enhancement, bug
**Status:** drafted

From investigating a vector-search-demo run where all 9 tickets failed. The
board showed "TESTER REJECTED" but the real cause (in the structured log) was
`coder_failed: design_docs_missing`. The failure data already exists
(`.commander/logs/structured-*.log` has `event` / `category` / `message`, and
each failure writes `last-failure-N.json` with `failure_class`); the UI just
doesn't surface it.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Show the real failure class and message on the Logs page run rows. When a sprint run has failures, the Logs page (run view) should display the failure class and a one-line message per failed ticket at the first level, read from the structured log events (coder_failed / tester_failed / design_docs_missing / sprint_pr_create_failed / neon_* / rate-limit / timeout) and the last-failure-N.json sidecars. Today a failure only shows up if you open the raw log file. Show, per failed ticket, the category (e.g. design_docs_missing, gate_failed, graphql_rate_limit, server_timeout) and the message; make the row expandable to the full detail. Acceptance: after a failed run I can see, on the Logs page without opening any file, which tickets failed and the specific reason for each.
---
Fix the sprint board mislabeling coder-guard failures as "TESTER REJECTED". On the Sprint Mgmt board, a ticket whose coder step failed (for example design_docs_missing or a gate failure, so the tester never ran) is shown with the red "TESTER REJECTED" outcome, which is wrong and misleading. Derive the outcome label from the actual failure stage in the structured log / last-failure sidecar: show "CODER FAILED" (with the failure class) when the coder failed, "TESTER REJECTED" only when the tester actually ran and rejected, and the appropriate label for sprint-level failures (PR create, rate limit). Acceptance: a ticket that failed the coder guard reads as a coder failure with its class, not as a tester rejection.
---
Add a run-level failure banner with the dominant error class to the Logs page. At the top of a failed run on the Logs page, show a short banner summarizing the run outcome: counts (completed / failed / skipped) and the dominant failure class with its message (e.g. "9 failed: design_docs_missing — PRODUCT.md, DESIGN.md missing in coder worktree" or "sprint stopped: GraphQL rate limit exceeded"). Surface known transient classes (GitHub GraphQL/REST rate limit, server timeout, network) distinctly from project-config classes (design_docs_missing, DATABASE_URL not set) so I can tell at a glance whether to retry or fix config. Acceptance: opening a failed run shows a first-level banner naming the dominant failure and whether it is transient or a config problem.
---
Quiet the optional-Neon errors in the structured log when Neon is disabled or unconfigured. When DATABASE_URL is not set (Neon is optional and off for most projects), the sprint structured log fills with repeated ERROR-level neon_update_failed / neon_sprint_init_failed entries, which drown the real failures. Downgrade these to a single INFO/DEBUG note per run (or suppress when COMMANDER_DISABLE_NEON is set), so the structured log and the Logs page show the actual failures, not Neon noise. Acceptance: a run with no DATABASE_URL produces at most one Neon-disabled note, and real ERRORs are not buried.
```

## Notes

- Investigation reference: vector-search-demo `sprint-1` run `sprint1-20260610T0721-x2nV`.
  Real cause `design_docs_missing` (run predated the PRODUCT/DESIGN scaffold fix,
  PR #780). Board showed "TESTER REJECTED" — the mislabel this batch fixes.
- The bulk-create BA producing tickets with **no acceptance criteria** (pre-flight
  "8 missing AC") is a separate concern (BA prompt/behavior), not covered here.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
