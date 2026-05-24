---
description: Sprint Status — show all open issues grouped by status. Usage: /sprint-status <repo URL>
---
Show the current sprint status by reading GitHub issues for the given repository.

Repository: $ARGUMENTS

Extract the owner/repo from the URL (e.g. from https://github.com/zealchaiwut/commander/ extract zealchaiwut/commander).

Run this bash command (substitute <owner/repo> with the extracted value):

    gh issue list --repo <owner/repo> --state open --json number,title,labels --limit 50

Then organize the output into a table with these columns:
- Stage (backlog, in-progress, SIT, UAT, blocked)
- Issues in that stage with their numbers and titles

Group by sprint label (sprint-1, sprint-2, sprint-3, etc.).

Also show any unlabeled / no-stage issues separately at the bottom under "Unlabeled / no-stage issues".

Total open issue count at the top.

If no repository URL provided, show usage and exit.
