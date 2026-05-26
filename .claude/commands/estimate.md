---
description: Estimate a GitHub issue — runs the Issue Estimator agent and saves sizing metadata. Usage: /estimate <issue-url>
---

Parse the following GitHub issue URL and estimate it.

Issue: $ARGUMENTS

Steps:
1. Extract the issue number and repo from the URL.
   Example: `https://github.com/zealchaiwut/commander/issues/7` → issue `7`, repo `zealchaiwut/commander`
2. Run the estimator script:
   ```bash
   python3 $(git rev-parse --show-toplevel)/services/sprint_manager/estimate_issue.py \
     --issue <N> --repo <owner/repo> --save-comment --save-label
   ```
3. Report back: issue number, size (S/M/L/XL), estimated hours, confidence, risk flags, and the path to the saved JSON file.
