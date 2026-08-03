---
description: Estimate a single GitHub issue via the Issue Estimator. Usage: /estimate <issue-url>
argument-hint: <issue-url|issue-number> [--save-comment] [--save-label] [--force]
---

Estimate a GitHub issue using the Issue Estimator (Haiku 4.5).

## Arguments

The user invoked: `/estimate $ARGUMENTS`

Parse `$ARGUMENTS` as:

- **First token: ISSUE_REF** (required) — either a GitHub issue URL or a bare issue number.
  - URL form: `https://github.com/<owner>/<repo>/issues/<N>` → extract ISSUE_NUM=`<N>` and REPO=`<owner>/<repo>`
  - Bare integer form (e.g. `/estimate 42`) → ISSUE_NUM=`42`; auto-detect REPO via:
    ```
    gh repo view --json nameWithOwner -q .nameWithOwner
    ```
- **Remaining tokens** are optional flags passed through verbatim:
  - `--save-comment` — post the structured estimate as a GitHub issue comment
  - `--save-label` — apply a `size-S/M/L/XL` label to the issue
  - `--force` — re-run even if a cached estimate already exists

If ISSUE_REF is absent, tell the user "Usage: /estimate <issue-url>" and stop.

## Run

From the repository root, call:

```bash
python3 services/sprint_manager/estimate_issue.py \
  --issue <ISSUE_NUM> \
  --repo <REPO> \
  [--save-comment] [--save-label] [--force]
```

The script:
- Fetches the issue from the local mirror (zero GitHub quota cost) or falls back to `gh api`
- Invokes the Haiku 4.5 estimator agent via `claude -p`
- Saves the result to `.commander/estimates/issue-<N>.json`
- Returns cached output if the file already exists (override with `--force`)

## After running

Report back:
- Issue number and title
- Size (`S` / `M` / `L` / `XL`), estimated minutes, confidence
- Path to the saved estimate JSON (`.commander/estimates/issue-<N>.json`)
- Whether a comment was posted (`--save-comment`) or a label applied (`--save-label`)
