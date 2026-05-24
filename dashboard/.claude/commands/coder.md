---
description: Implement a GitHub issue end-to-end — branch, code, push, move to SIT
---

Use the coder subagent to work on issue $ARGUMENTS

The coder agent must follow this exact workflow:
1. Read the GitHub issue to understand the acceptance criteria and scope
2. Run `python3 scripts/start_feature.py --issue <N>` to create `feature/<N>-<slug>` off `develop`, push it to origin, and label the ticket `in-progress`
3. Implement the feature, committing logical units of work as you go
4. Push the branch
5. Run `python3 scripts/update_ticket.py --issue <N> --status sit` to move the ticket to SIT

Do NOT merge the branch — the tester handles merging after tests pass.
