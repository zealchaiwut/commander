---
description: Run reviewer agent on a sprint to post code-review comment and open follow-up tickets
argument-hint: <sprint-label> [owner/repo] [summary-issue-num]
---

Use the **reviewer** agent to read the sprint diff against each ticket's spec, post a structured review comment on the sprint summary issue, and auto-open follow-up tickets for non-blocker findings. Advisory only.

## Arguments

The user invoked: `/rev $ARGUMENTS`

Parse `$ARGUMENTS` as:
- First word: SPRINT_LABEL (required, e.g. `sprint-9`)
- Second word (optional): REPO in `owner/repo` form
- Third word (optional): SUMMARY_ISSUE_NUM

## Steps before invoking the reviewer

1. Confirm SPRINT_LABEL is present; if not, ask the user and stop.

2. Resolve REPO (from arg or `gh repo view --json nameWithOwner -q .nameWithOwner`).

3. If SUMMARY_ISSUE_NUM was not provided, auto-detect it:

       gh issue list --repo <REPO> --label <SPRINT_LABEL> --search "Executive Summary in:title" --state all --json number --jq '.[0].number'

   If empty/null, tell the user "Could not find sprint summary issue for <SPRINT_LABEL>. Pass it as third arg." and stop.

4. Determine SHA range:

       git fetch origin
       SPRINT_BRANCH=sprint/<SPRINT_LABEL>
       BASE_SHA=$(git merge-base origin/develop $SPRINT_BRANCH)
       HEAD_SHA=$(git rev-parse $SPRINT_BRANCH)

5. If `BASE_SHA == HEAD_SHA`, no commits to review — tell the user "Sprint <LABEL> has no merged work to review" and stop.

## Invoke the reviewer agent

Use the Task tool to delegate to the `reviewer` sub-agent. Pass these inputs in the prompt:

- SPRINT_LABEL
- SPRINT_BRANCH
- BASE_SHA
- HEAD_SHA
- SUMMARY_ISSUE_NUM
- REPO

The reviewer follows its own workflow (read diff + specs, classify findings, post one comment on summary issue, open follow-up tickets for non-blockers, output JSON, exit).

**Important reminders to surface if the reviewer seems uncertain:**
- Reviewer is read-only — no code edits, no commits.
- Advisory only — never applies `needs-rework` or closes tickets.
- BLOCKER findings stay in the comment; SUGGESTION/NIT findings get auto-tickets.
- Follow-up tickets must NOT carry the sprint-N label — they go to backlog.

## After the reviewer finishes

Parse the JSON output line. Report to the user:

- Comment URL (link to the review on the sprint summary issue)
- Counts: B blockers, S suggestions, I nits
- List of follow-up ticket numbers created
- Recommendation:
  - "✅ Ready for human UAT" if blockers == 0
  - "⚠️ Blockers present — review the comment before deploying" if blockers > 0