---
description: Tester — verify a feature, write tests, merge to develop. Usage: /tester <issue URL>
---
Use the Tester subagent to verify the implementation of the following GitHub issue.

Issue URL: $ARGUMENTS

Delegate this task to the **Tester** subagent.

The Tester subagent will:
1. Read the issue acceptance criteria
2. Check out the feature branch
3. Write pytest tests (one per AC item) following the Test Volume Policy
4. Run pytest and capture results
5. Post a structured test report as a GitHub comment
6. If all tests pass: merge feature branch into develop, label issue UAT
7. If tests fail: label issue needs-rework, post failure details
8. Report back with the result

Repo: zealchaiwut/commander
Work in: ~/commander/work-tester
