---
description: Coder — implement a GitHub issue end-to-end (branch, code, push, label SIT). Usage: /coder <issue-url>
---

Parse the following GitHub issue URL and delegate to the **coder** subagent.

Issue URL: $ARGUMENTS

Steps to follow before delegating:
1. Extract the issue number from the URL (e.g. `https://github.com/zealchaiwut/commander/issues/4` → issue `4`, owner `zealchaiwut`, repo `commander`)
2. Pass to the **coder** subagent with the instruction: `work on issue <N>` where `<N>` is the extracted issue number

The coder subagent will:
- Ensure it is working from the correct git root (not `~/commander/dashboard/`)
- Run `scripts/start_feature.py --issue <N>` to create `feature/<N>-<slug>` off `develop`
- Read the ticket's Acceptance Criteria and implement exactly what is specified
- Commit in logical chunks, push the feature branch, then label the ticket SIT
- Post a completion comment to the issue

Branching rules (from CLAUDE.md):
- Feature branch must be created off `develop`, never `master`
- Never commit directly to `develop` or `master`
- Branch naming: `feature/<N>-<slug>` (kebab-case, includes issue number)
