---
description: Plan a feature with the BA agent — clarifying questions, acceptance criteria, UAT steps, GitHub issue
---

Use the BA subagent to plan this feature: $ARGUMENTS

The BA agent must:
1. Read the relevant source files to understand the current codebase before writing anything
2. Ask clarifying questions if anything is ambiguous
3. Write numbered, verifiable acceptance criteria
4. Write numbered UAT test steps with explicit Expected outcomes
5. Present the full proposed ticket (title + body) and wait for the user to type "Approve"
6. Only after approval: create the GitHub issue with `gh issue create`
