---
description: Coder — implement a GitHub issue on a feature branch. Usage: /coder <issue URL>
---
Use the Coder subagent to implement the feature described in the following GitHub issue.

Issue URL: $ARGUMENTS

Delegate this task to the **Coder** subagent.

The Coder subagent will:
1. Read the issue to understand acceptance criteria
2. Create a feature branch from develop (named feature/N-<slug>)
3. Implement the feature, committing changes
4. Push the branch to origin
5. Update the issue label from backlog → SIT
6. Post a brief completion comment on the issue
7. Report back with the branch name and commit SHA

Repo: zealchaiwut/commander
Work in: ~/commander/work-coder
