---
description: Show current sprint status — open issues grouped by workflow stage
---

Run the following command to fetch all open issues for this repo, then display the results grouped by workflow stage:

```bash
gh issue list --state open --json number,title,labels,assignees --limit 100
```

Present the results in this grouped order, showing issue number, title, and assignee for each:

1. **blocked** — label `blocked`
2. **UAT** — label `UAT` (awaiting sign-off)
3. **SIT** — label `SIT` (in testing)
4. **in-progress** — label `in-progress`
5. **backlog** — open issues with no status label

At the end, show a one-line summary: total open, and count per stage.
