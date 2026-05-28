---
description: Tester — run automated tests for a ticket's AC, post a report, and merge to develop on pass. Usage: /tester <issue-url>
---

Parse the following GitHub issue URL and delegate to the **tester** subagent.

Issue URL: $ARGUMENTS

Steps to follow before delegating:
1. Extract the issue number from the URL (e.g. `https://github.com/zealchaiwut/commander/issues/4` → issue `4`, owner `zealchaiwut`, repo `commander`)
2. Pass to the **tester** subagent with the instruction: `verify issue <N>` where `<N>` is the extracted issue number

The tester subagent will:
- Check out the `feature/<N>-<slug>` branch from origin (fails fast if no branch exists)
- Fetch the ticket and parse its Acceptance Criteria and UAT Test Steps
- Write `tests/test_<slug>__<N>.py` with one test per AC criterion
- Run the tests against the live server at `http://localhost:8000`
- Evaluate UAT steps (HTTP steps executed, browser/visual steps marked ⚠️ MANUAL)
- Post a structured report via `scripts/post_test_report.py`
- **If READY_FOR_UAT:** run `scripts/finish_feature.py --issue <N>` (merges to `develop`, deletes branch, labels UAT) — **only when pytest exit code is 0**
- **If NEEDS_FIXES:** move label to `blocked`, do not merge, leave branch intact for the coder

Repo context: owner `zealchaiwut`, repo `commander`

Note: the dev server must be running at `http://localhost:8000` for HTTP tests to execute. If it is not running, all HTTP tests will fail — the subagent will report this clearly rather than marking individual tests as failures.
