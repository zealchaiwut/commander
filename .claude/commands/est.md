---
description: Run estimator agent on a sprint to size tickets and identify impacted files
argument-hint: <sprint-label> [owner/repo]
---

Use the **estimator** agent to estimate every backlog ticket in the sprint.

## Arguments

The user invoked: `/est $ARGUMENTS`

Parse `$ARGUMENTS` as:
- First word: SPRINT_LABEL (required, e.g. `sprint-9`)
- Second word (optional): REPO in `owner/repo` form
- If REPO not given, detect with: `gh repo view --json nameWithOwner -q .nameWithOwner`

## Steps before invoking the estimator

1. Confirm SPRINT_LABEL is present; if not, ask the user to provide it and stop.
2. Resolve REPO (from arg or `gh repo view`).
3. Fetch the open backlog issues for the sprint:

       gh issue list --repo <REPO> --label <SPRINT_LABEL> --state open --json number,title,url --jq 'map({number, title, url})'

4. If the list is empty, tell the user "No open issues in <SPRINT_LABEL>" and stop.

## Invoke the estimator agent

Use the Task tool to delegate to the `estimator` sub-agent. Pass these inputs in the prompt:

- SPRINT_LABEL = the parsed label
- REPO = the resolved owner/repo
- REPO_PATH = the current working directory
- ISSUES_JSON = the JSON array from step 3

The estimator follows its own workflow (read issues, scan code, write JSON output, comment on each issue, apply `estimated` label, exit).

## After the estimator finishes

Report back to the user:

- Total tickets estimated
- Total estimated minutes
- Path to the output JSON file (`.commander/sprints/<SPRINT_LABEL>-estimate.json`)
- Any tickets that were skipped and why