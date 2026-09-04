# REQ-FINISH — Finish, UAT sign-off, complete-after-dispatch

## Capabilities

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-FINISH-01 | Finish merges sprint→develop (sprint-branch model) and closes sprint work issues with soft rework guard | [ADR 2026-07-02-2](../decisions/2026-07-02-2-merge-sprint-rework-soft-guard.md), sprint-finish |
| REQ-FINISH-02 | Per-sprint UAT sign-off closes UAT + Executive Summary issues for that sprint label | #2305 |
| REQ-FINISH-03 | `POST …/complete-after-dispatch` merges the PR recorded by a green dispatch (`sprint_pr_number`); preview/dry_run mutates nothing | #2357 |
| REQ-FINISH-04 | `uat_signoff: true` runs Finish path after PR is known; missing sprint PR → 409 | #2357 |
| REQ-FINISH-05 | Closing without UAT is the sanctioned waive mechanism when explicitly chosen | [ADR 2026-07-02-10](../decisions/2026-07-02-10-close-without-uat-is-waive.md) |
| REQ-FINISH-06 | Human remains sole merger of develop → master | PRODUCT.md non-goals |

## API contract

See [`docs/api/overnight.yaml`](../api/overnight.yaml) tags `finish` and `overnight`.
