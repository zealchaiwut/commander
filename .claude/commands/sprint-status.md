---
description: Show open GitHub issues grouped by label (backlog → in-progress → sit → uat) for the current sprint.
---

Run the following commands to show the current sprint status for the Commander repo (zealchaiwut/commander). Display results grouped by label in workflow order.

```bash
REPO="zealchaiwut/commander"

echo "=== SPRINT STATUS: $REPO ==="
echo ""

for LABEL in backlog in-progress sit uat blocked; do
  echo "--- $LABEL ---"
  gh issue list --repo "$REPO" --label "$LABEL" --state open \
    --json number,title,labels,assignees,updatedAt \
    --template '{{range .}}  #{{.number}} {{.title}}{{"\n"}}{{end}}'
  echo ""
done

echo "--- Total open issues ---"
gh issue list --repo "$REPO" --state open --json number --template '{{len .}} open issues{{"\n"}}'
```

Run these commands now and display the output in a clean, readable format. If any label has no issues, still show the header so the pipeline state is visible at a glance.
