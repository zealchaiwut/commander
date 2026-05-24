---
description: Verify a GitHub issue — write tests, run pytest, merge or flag for rework
---

Use the tester subagent to verify issue $ARGUMENTS

The tester agent must follow this exact workflow:
1. Check out the `feature/<N>-*` branch for this issue
2. Read the issue's Acceptance Criteria and UAT Test Steps
3. Write `tests/test_<feature>__<N>.py` with one test per AC item
4. Run `pytest` against the dev server at `http://localhost:8000`
5. Post the structured test report to the issue via `python3 scripts/post_test_report.py --issue <N> --report-file <path>`

**If all automated tests pass:**
- Run `python3 scripts/finish_feature.py --issue <N>` to merge into `develop` with `--no-ff`, push, and delete the feature branch
- Run `python3 scripts/update_ticket.py --issue <N> --status uat` to move the ticket to UAT

**If any test fails:**
- Do NOT merge — stay on the feature branch
- Run `python3 scripts/update_ticket.py --issue <N> --status in-progress` to move the ticket back to in-progress
