---
description: Business Analyst — plan and file a new GitHub issue from a feature description. Usage: /ba <feature description>
---
Use the BA subagent to plan and create a GitHub issue for the following feature request.

Feature description: $ARGUMENTS

Delegate this task to the **BA** subagent. Pass the feature description exactly as given above.

The BA subagent will:
1. Ask clarifying questions if needed (user, success condition, edge cases)
2. Generate a structured issue body with Acceptance Criteria and UAT Test Steps
3. Determine the active sprint number from `~/commander/dashboard/projects.json`
4. Create the issue via `scripts/create_ticket.py`
5. Report back with the issue number, URL, and AC count

Repo: zealchaiwut/commander
