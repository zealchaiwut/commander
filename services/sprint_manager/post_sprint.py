"""Post-sprint agent dispatch helpers for sprint_manager.

Contains: _create_sprint_pr, _dispatch_documenter, _dispatch_reviewer,
_dispatch_ba_for_followup, _dispatch_estimator_for_followup,
_enrich_followup_tickets — extracted from sprint_manager.py (issue #1288)
as a pure structural move with no behavioral changes.

sprint_manager.py re-imports and re-exports all symbols so all
existing call sites remain unmodified.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig
    from services.sprint_manager.state import SprintState

# Ensure repo root is on sys.path so sibling service imports work regardless
# of how this module is imported.
_REPO_ROOT = Path(__file__).parent.parent.parent
for _p in (str(_REPO_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.logging import log as structured_log  # noqa: E402
from services.sprint_manager.paths import _state_path  # noqa: E402
from services.sprint_manager.dispatch import (  # noqa: E402
    _load_agent_persona,
    _agent_identity_env,
)
from services.sprint_manager.model_routing import apply_provider_env  # noqa: E402


# ── sys.modules proxy helper ──────────────────────────────────────────────────
# Deferred lookups via sys.modules avoid a circular import (post_sprint.py is
# imported BY sprint_manager.py) while also ensuring that test monkeypatches
# applied to sprint_manager attributes are respected.
#
# Strategy mirrors summary.py's _SmMergedView: for each attribute lookup,
# check both sprint_manager module aliases.  If post_sprint.py itself defines
# the attribute, compare each alias's value to the local original — a differing
# value means it was monkeypatched, so prefer it.
# If post_sprint.py does NOT define the attribute (e.g. subprocess, github_client),
# return the first non-None value found, preferring the package-path alias so
# tests that import via that path see their patches.


class _SmMergedView:
    """Proxy for sprint_manager attributes, honoring test monkeypatches.

    Tests patch attributes on `sm` (sprint_manager); functions in post_sprint.py
    must see those patches even though they live in a different module.
    """

    def __getattr__(self, name: str):
        _self = sys.modules.get("services.sprint_manager.post_sprint")
        try:
            original = object.__getattribute__(_self, name) if _self is not None else None
        except AttributeError:
            original = None

        # Pass 1: find a patched (or any available) value.
        for _key in ("services.sprint_manager.sprint_manager", "sprint_manager"):
            _mod = sys.modules.get(_key)
            if _mod is None:
                continue
            val = getattr(_mod, name, None)
            if val is None:
                continue
            if original is None:
                return val
            if val is not original:
                return val

        # Pass 2: no patched value found; return any available value.
        for _key in ("services.sprint_manager.sprint_manager", "sprint_manager"):
            _mod = sys.modules.get(_key)
            if _mod is not None:
                val = getattr(_mod, name, None)
                if val is not None:
                    return val

        # Last resort: direct import.
        import services.sprint_manager.sprint_manager as _sm_mod  # noqa: PLC0415
        return getattr(_sm_mod, name)


_sm_ref = _SmMergedView()


def _lookup_in_sm(attr: str, local_fn):
    """Return the sprint_manager attribute if it differs from local_fn.

    Checks both "sprint_manager" and "services.sprint_manager.sprint_manager"
    keys so that monkeypatches applied via either import path are found.
    Returns None when no patch is active.
    """
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None:
            _f = getattr(_sm, attr, None)
            if _f is not None and _f is not local_fn:
                return _f
    return None


# ── Proxy functions for sprint_manager.py-only helpers ───────────────────────

def _r(*args, **kwargs):
    """Proxy to sprint_manager._r."""
    _f = _lookup_in_sm("_r", _r)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_r: sprint_manager not loaded")


def _git_verified_shipped_issues(*args, **kwargs):
    """Proxy to sprint_manager._git_verified_shipped_issues."""
    _f = _lookup_in_sm("_git_verified_shipped_issues", _git_verified_shipped_issues)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_git_verified_shipped_issues: sprint_manager not loaded")


def _reporting_not_shipped_issues(*args, **kwargs):
    """Proxy to sprint_manager._reporting_not_shipped_issues."""
    _f = _lookup_in_sm("_reporting_not_shipped_issues", _reporting_not_shipped_issues)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_reporting_not_shipped_issues: sprint_manager not loaded")


def _log_shipped_status_git_mismatch(*args, **kwargs):
    """Proxy to sprint_manager._log_shipped_status_git_mismatch."""
    _f = _lookup_in_sm("_log_shipped_status_git_mismatch", _log_shipped_status_git_mismatch)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_log_shipped_status_git_mismatch: sprint_manager not loaded")


def _fail_loud_shipped_reconciliation(*args, **kwargs):
    """Proxy to sprint_manager._fail_loud_shipped_reconciliation."""
    _f = _lookup_in_sm("_fail_loud_shipped_reconciliation", _fail_loud_shipped_reconciliation)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_fail_loud_shipped_reconciliation: sprint_manager not loaded")


def _build_crash_detail(*args, **kwargs):
    """Proxy to sprint_manager._build_crash_detail."""
    _f = _lookup_in_sm("_build_crash_detail", _build_crash_detail)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_build_crash_detail: sprint_manager not loaded")


def record_failure(*args, **kwargs):
    """Proxy to sprint_manager.record_failure."""
    _f = _lookup_in_sm("record_failure", record_failure)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("record_failure: sprint_manager not loaded")


def _db_agent_start_sm(*args, **kwargs):
    """Proxy to sprint_manager._db_agent_start_sm."""
    _f = _lookup_in_sm("_db_agent_start_sm", _db_agent_start_sm)
    if _f is not None:
        return _f(*args, **kwargs)


def _db_agent_finish_sm(*args, **kwargs):
    """Proxy to sprint_manager._db_agent_finish_sm."""
    _f = _lookup_in_sm("_db_agent_finish_sm", _db_agent_finish_sm)
    if _f is not None:
        return _f(*args, **kwargs)


class HangDetector:
    """Proxy to sprint_manager.HangDetector (alerts.py) honoring test patches."""

    def __new__(cls, *args, **kwargs):
        _real_cls = _lookup_in_sm("HangDetector", HangDetector)
        if _real_cls is not None:
            return _real_cls(*args, **kwargs)
        from services.sprint_manager.alerts import HangDetector as _Real  # noqa: PLC0415
        return _Real(*args, **kwargs)


# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = _REPO_ROOT

# ── Reviewer agent (issue #159 / #160) ───────────────────────────────────────
#
# Sprint.yaml override example (add under the `agents:` key):
#
#   agents:
#     reviewer_prompt_template: |
#       You are the code reviewer for sprint {sprint_label}.
#       Repo: {repo_name}. Diff: {base_sha}..{head_sha}.
#       Sprint summary issue: #{summary_issue_num}.
#       ... (your custom instructions here) ...
#
# All six placeholders are available in every template (custom or default):
#   {sprint_label}       e.g. "sprint-12"
#   {sprint_branch}      e.g. "sprint/sprint-12"
#   {base_sha}           base commit SHA of the sprint branch
#   {head_sha}           head commit SHA of the sprint branch
#   {summary_issue_num}  GitHub issue number of the sprint summary
#   {sprint_filter_url}  GitHub issues URL filtered by sprint label
#   {repo_name}          e.g. "zealchaiwut/commander"

DEFAULT_REVIEWER_PROMPT = """\
You are the **Code Reviewer** agent for sprint {sprint_label}.

## Context

- Repo: {repo_name}
- Sprint branch: {sprint_branch} (already merged to develop)
- Diff range: {base_sha}..{head_sha}
- Sprint summary issue: #{summary_issue_num}
- Sprint tickets: {sprint_filter_url}

## Your Mandate (Read-Only Review)

You perform a structured code review of everything merged this sprint. \
You do NOT modify code, make commits, push branches, close tickets, \
apply labels to merged tickets, run tests, or run the application. \
You post EXACTLY ONE comment on the sprint summary issue. \
You do NOT review tickets that failed or were not merged.

## Step 1 — Collect the diff

Run these two commands and read their output carefully:

```
git diff {base_sha}..{head_sha}
git diff {base_sha}..{head_sha} --stat
```

## Step 2 — Identify merged tickets

Run:

```
gh api repos/{repo_name}/issues/{summary_issue_num} --jq .body
```

Parse the sprint summary body to extract the list of merged ticket numbers \
(look for checkboxes or a "Merged" / "Done" section). \
For each merged ticket N, run:

```
gh api repos/{repo_name}/issues/N
```

Skip any ticket that:
- Has the label `needs-rework`
- Is NOT in a merged/done state (failed or skipped tickets are not reviewed)

## Step 3 — Per-Ticket Review

For each merged ticket, evaluate the following. \
Every finding MUST include a `file:line` reference from the actual diff — \
vague findings with no file reference are not allowed.

### 3a. Acceptance Criteria Coverage

Every AC checkbox in the ticket body should have corresponding code changes \
in the diff. Flag any AC item that appears unimplemented or only partially \
implemented.

### 3b. Scope Creep

Files changed in the diff that go beyond what the ticket asked for. \
Flag files that have no plausible connection to the ticket's stated goal.

### 3c. Out-of-Scope Violations

If the ticket has an "Out of Scope" section listing things NOT to do, \
check whether the diff does any of those things anyway.

### 3d. Code Quality Smells

Check for:
- Hardcoded secrets or credentials
- Raw SQL string concatenation (SQL injection risk)
- Bare `except:` with no exception type
- Missing input validation on new API endpoints
- Missing error handling on external calls (HTTP, subprocess, file I/O)
- Dead code (unreachable branches, unused imports, commented-out blocks)
- Hardcoded values that belong in config
- New unjustified dependencies added to requirements.txt or package.json

### 3e. Spec-vs-Code Drift

Does the code actually implement what the AC says, beyond what the tester \
already verified? Look for subtle divergences: wrong defaults, inverted \
conditions, missing edge-case handling the AC implied.

## Step 4 — Sprint-Level Review

After reviewing all individual tickets, check across the full diff for:

- **Migration order**: if multiple Alembic migrations are present, are their \
  down_revision chains chronologically valid?
- **Schema conflicts**: column names referenced consistently across tickets \
  (no ticket renames a column another ticket still uses under the old name)?
- **Endpoint consistency**: new routes follow the naming conventions of \
  existing routes?
- **Dependency additions**: every new entry in requirements.txt / package.json \
  is justified by at least one ticket?
- **Cross-file quiet refactoring**: large code movements or renames that no \
  individual ticket required?

## Step 5 — Classify Each Finding

Assign one of three severity labels:

| Label | Meaning |
|-------|---------|
| **BLOCKER** | Real harm if shipped — data loss, security hole, broken contract, \
crash on the happy path. Do NOT create a follow-up ticket for blockers. |
| **SUGGESTION** | Worth fixing but not blocking — better error handling, \
cleaner abstraction, missing test coverage for an edge case. |
| **NIT** | Minor polish — naming, formatting, a missing docstring. |

Be conservative with BLOCKER. When in doubt, downgrade to SUGGESTION.

## Step 6 — Post ONE Comment on Issue #{summary_issue_num}

Use this exact structure. Write the body to a temp file, then post it with:

```
gh issue comment {summary_issue_num} --body-file /tmp/reviewer-report.md
```

If a reviewer comment from a previous run already exists on this issue, \
EDIT that comment instead of posting a new one (use `gh api` PATCH). \
Do NOT post more than one reviewer comment on the sprint summary issue.

### Comment Structure

```
## Code Reviewer Report — Sprint {sprint_label}

**Diff range:** {base_sha}..{head_sha}
**Tickets reviewed:** N  |  **Findings:** B blockers · S suggestions · N nits

---

### Per-Ticket Review

#### Ticket #N — <title>

**AC coverage:** [all covered | AC item "..." not found in diff]
**Scope creep:** [none | file:line — reason]
**Out-of-scope violations:** [none | description]

BLOCKER · `file.py:42` — Raw SQL concatenation: `query = "SELECT * FROM users WHERE id=" + user_id`
SUGGESTION · `file.py:88` — Missing error handling on `requests.get(url)` call; wrap in try/except and return HTTP 502 on failure.
NIT · `file.py:15` — Unused import `os`.

---

### Sprint-Level Findings

[findings here, or "None identified."]

---

### Follow-up Tickets Opened

[List of #N — title, or "None."]

---

**Recommendation:** Ready for human UAT  ← (or "Blockers present — resolve before UAT")

_Generated by reviewer on <ISO timestamp>_
```

## Step 7 — Create Follow-up Tickets for SUGGESTION and NIT

For every SUGGESTION and NIT finding, create one follow-up ticket:

```
gh issue create \\
  --title "[follow-up] <short description>" \\
  --body "..." \\
  --label "enhancement,follow-up,code-review,<area>"
```

Where `<area>` is `backend`, `frontend`, or `database` — inferred from the \
file path of the finding.

**Body format:**

```
Context: sprint {sprint_label} review of #<original-ticket-num> — \
see #{summary_issue_num} for full report.

**Original ticket:** #<N>
**Severity:** SUGGESTION | NIT
**File:** `file.py:42`
**Issue:** <description of the problem>
**Suggested fix:** <concrete suggestion, or "N/A">
```

**Label rules:**
- Always apply: `enhancement`, `follow-up`, `code-review`
- Infer area label from file path: `backend` for .py server files, \
  `frontend` for .html/.js/.css, `database` for migrations/models
- DO NOT apply any `sprint-N` label
- DO NOT create follow-up tickets for BLOCKER findings

## Step 8 — Output ONE JSON Line to stdout

After posting the comment and creating tickets, output exactly one JSON line:

```
{{"comment_url": "https://github.com/...", "blockers": N, "suggestions": N, "nits": N, "follow_up_tickets": [123, 124]}}
```

Then exit cleanly.

## Concrete Examples of Good vs Bad Findings

### BLOCKER — Good finding

> BLOCKER · `apps/dashboard/main.py:234` — `user_id` from query string is \
> concatenated directly into SQL: `query = "SELECT * FROM orders WHERE user=" + user_id`. \
> Classic SQL injection; any unauthenticated caller can dump the database.

### BLOCKER — Bad finding (vague, no file reference)

> BLOCKER — The code might have SQL injection somewhere.

### SUGGESTION — Good finding

> SUGGESTION · `services/sprint_manager/sprint_manager.py:1802` — \
> `subprocess.run(cmd)` has no timeout. If the subprocess hangs, \
> the sprint loop blocks indefinitely. Add `timeout=300`.

### SUGGESTION — Bad finding (not tied to diff)

> SUGGESTION — The codebase should use async everywhere for better performance.

### NIT — Good finding

> NIT · `apps/dashboard/static/index.html:88` — `var` used instead of `const` \
> for `let result = fetch(...)`. Modern JS prefers `const` or `let`.

### NIT — Bad finding (invented requirement)

> NIT — All functions should have docstrings. (This was not in the AC.)

## Prohibited Actions (Do NOT Do These)

- Modify any source file
- Run `git commit`, `git push`, or any branch operation
- Apply labels to the merged sprint tickets (only to new follow-up tickets)
- Close any issue
- Run the application, run tests, or invoke any server
- Post more than one comment on the sprint summary issue
- Review tickets that failed, were skipped, or were not merged
- Invent requirements not present in the AC or issue body
- Include file:line references that do not appear in `git diff --name-only {base_sha}..{head_sha}`
"""


# ── Documenter agent (issue #165) ─────────────────────────────────────────────

DEFAULT_DOCUMENTER_PROMPT = """\
You are the **Documenter** agent for sprint {sprint_label}.

## Context

- Sprint branch: {sprint_branch}
- Diff range: {base_sha}..{head_sha}
- Sprint tickets: {sprint_filter_url}
- Sprint summary issue: #{summary_issue_num}

## Your Mandate

Read the sprint diff and update project documentation to reflect what shipped
this sprint. Commit any doc changes directly to the sprint branch.

Documentation files to consider updating (only if relevant changes shipped):
- README.md — new features, changed commands, updated usage examples
- CHANGELOG.md — one-line entry per merged ticket (format: "- #N: <title>")
- SCHEMA.md — any new or changed DB tables/columns/endpoints

## Step 1 — Review the diff

Run:
```
git diff {base_sha}..{head_sha} --stat
git diff {base_sha}..{head_sha}
```

## Step 2 — Identify what shipped

For each ticket in the sprint, read:
```
gh api repos/{repo_name}/issues/<N>
```

Only document tickets that are in a merged/done state.

## Step 3 — Update docs

For each doc file that needs updating:
1. Read the current file
2. Make targeted, accurate additions
3. Do not remove existing content unless it is factually incorrect after this sprint

## Step 4 — Commit

If you made any doc changes, stage and commit them:
```
git add <doc files changed>
git commit -m "docs: auto-update from sprint-{sprint_label} diff"
```

Then print a line in this exact format so sprint_manager can parse it:
```
Documenter complete: <comma-separated list of files touched, or 'none'>
```

If no doc changes were needed (nothing to document), print:
```
Documenter complete: none
```

## Prohibited Actions

- Do NOT modify source code (.py, .js, .html, .sql, etc.)
- Do NOT create new tickets or close existing ones
- Do NOT push to remote — sprint_manager handles git push
- Do NOT modify files outside the project root
"""


# ── Follow-up ticket enrichment (BA + estimator) ─────────────────────────────

_ESTIMATE_ISSUE_SCRIPT_SM = REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py"

_BA_REWRITE_PROMPT = """\
You are a Business Analyst. Issue #{issue_num} in repository {repo} was created by an \
automated code reviewer as a follow-up ticket. Its body may be minimal or unstructured.

Your task:
1. Read the issue with: gh api repos/{repo}/issues/{issue_num}
2. Rewrite the body to the standard Commander format with ALL of these sections:
   ## What & Why
   ## Acceptance Criteria
   (at least 2 checkbox items: - [ ] ...)
   ## UAT Test Steps
   ## Out of Scope
3. Update the issue body on GitHub: gh issue edit {issue_num} --repo {repo} --body-file <tmpfile>
   (write the new body to a temp file first, then pass via --body-file)

Do not change the title, labels, milestone, or any field other than the body.
Do not ask clarifying questions. Produce the structured body directly from the existing content.
"""

_BA_DISPATCH_TIMEOUT = int(os.environ.get("COMMANDER_BA_REWRITE_TIMEOUT", "300"))
_ESTIMATOR_DISPATCH_TIMEOUT = int(os.environ.get("COMMANDER_ESTIMATOR_TIMEOUT", "300"))


# ── Helper ────────────────────────────────────────────────────────────────────

def _extract_follow_up_issue_nums(
    log_text: str,
    eff_repo: Optional[str],
    summary_issue_num: Optional[int],
) -> list:
    """Extract follow-up issue numbers from `gh issue create` URLs in a log.

    `gh issue create` prints the new issue URL: https://github.com/<repo>/issues/N
    Comment URLs (https://github.com/<repo>/issues/N#issuecomment-M) must be
    excluded. Earlier code used a fragile position-based heuristic
    (the char after the match must be '#'), which silently broke when logs
    wrapped or reformatted URLs. This uses stricter regex anchoring: a negative
    lookahead `(?!#issuecomment-)` on the issue-number match, so ONLY comment
    URLs are skipped — issue URLs carrying any other fragment still count.

    Returns issue numbers in first-seen order, de-duplicated, with the sprint
    summary issue removed. Returns [] when eff_repo is falsy.
    """
    if not eff_repo:
        return []

    # (?<!\d) / (?!\d) keep the number whole; (?!#issuecomment-) drops comment URLs.
    issue_url_pat = re.compile(
        rf"https://github\.com/{re.escape(eff_repo)}/issues/(?<!\d)(\d+)(?!\d)(?!#issuecomment-)",
        re.IGNORECASE,
    )
    seen_nums: set = set()
    follow_up_nums: list = []
    for url_m2 in issue_url_pat.finditer(log_text):
        num = int(url_m2.group(1))
        if summary_issue_num is not None and num == summary_issue_num:
            continue
        if num not in seen_nums:
            seen_nums.add(num)
            follow_up_nums.append(num)
    return follow_up_nums


# ── Six extracted methods ─────────────────────────────────────────────────────

# ── sprint branch PR creation (AC6, AC7) ─────────────────────────────────────

def _create_sprint_pr(
    sprint_branch: str,
    sprint_label: str,
    sprint_number: Optional[int],
    state: "SprintState",
    repo_name: Optional[str] = None,
    pr_base: str = "develop",
    merge_target: Optional[str] = None,
) -> Optional[str]:
    """Create a PR from sprint_branch → pr_base at the end of a child sprint.

    Child branches promote into the base sprint branch here; develop is only
    reached at Merge Sprint (no auto-merge). Returns PR URL or None.

    When merge_target is provided, shipped tickets are git-verified; done-but-
    unverified tickets appear in the skipped/failed section.
    """
    r = _r(repo_name)
    n = sprint_number if sprint_number is not None else sprint_label

    reconciliation_mismatch: list[int] = []
    if merge_target:
        _log_shipped_status_git_mismatch(state, merge_target)
        reconciliation_mismatch = _fail_loud_shipped_reconciliation(state, merge_target, "Sprint PR")
        shipped  = _git_verified_shipped_issues(state, merge_target)
        not_shipped = _reporting_not_shipped_issues(state, merge_target)
    else:
        shipped     = [i for i in state.issues if i.status == "done"]
        not_shipped = [i for i in state.issues if i.status == "skipped"]

    ticket_lines = "\n".join(
        f"- #{i.number} {i.title}" for i in shipped
    ) or "No tickets shipped."

    skipped_lines_parts = []
    for i in not_shipped:
        if i.status == "done" and merge_target:
            reason = f"marked done but not git-verified on {merge_target}"
        else:
            reason = i.category or "unknown"
        skipped_lines_parts.append(f"- #{i.number} {i.title} ({reason})")
    skipped_lines = "\n".join(skipped_lines_parts) or "None."

    reconciliation_section = ""
    if reconciliation_mismatch:
        mismatch_refs = " ".join(f"#{n}" for n in reconciliation_mismatch)
        reconciliation_section = (
            f"### Reconciliation failed\n\n"
            f"{len(reconciliation_mismatch)} ticket(s) marked done in state but not "
            f"git-verified on {merge_target}: {mismatch_refs}\n\n"
        )

    body = (
        f"{reconciliation_section}"
        f"## Sprint {n} — auto-generated PR\n\n"
        f"This PR promotes `{sprint_branch}` into `{pr_base}` after all sprint issues "
        f"have been processed.\n\n"
        f"### Shipped ({len(shipped)} tickets)\n\n"
        f"{ticket_lines}\n\n"
        f"### Skipped / Failed ({len(not_shipped)} tickets)\n\n"
        f"{skipped_lines}\n\n"
        f"### Stats\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Total tokens in | {state.total_tokens_in} |\n"
        f"| Total tokens out | {state.total_tokens_out} |\n"
        f"| Wall clock | {state.wall_clock_secs:.0f}s |\n\n"
        f"_Merge via Merge Sprint when UAT is complete._"
    )

    # Title reflects the CHILD label and its immediate-parent target (e.g.
    # "Sprint 94.2 → 94.1 — 2 ticket(s) shipped"), not the base sprint number, so
    # a rerun child's PR is unambiguous in the lineage.
    title = f"Sprint {sprint_label} → {pr_base.split('/')[-1]} — {len(shipped)} ticket(s) shipped"

    sys.stdout.write(str(f"  Creating PR: {sprint_branch} → {pr_base} ...") + "\n")
    try:
        result = _sm_ref.subprocess.run(
            [
                "gh", "pr", "create",
                "--repo", r,
                "--base", pr_base,
                "--head", sprint_branch,
                "--title", title,
                "--body", body,
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            sys.stdout.write(str(f"  Sprint PR created: {pr_url}") + "\n")
        else:
            stderr = result.stderr.strip()
            # If a PR already exists for this branch, gh will print its URL in stderr
            if "already exists" in stderr or "already have" in stderr.lower():
                # Extract URL from stderr (gh prints "a pull request for branch ... already exists: <url>")
                m = re.search(r"https://github\.com/\S+", stderr)
                if m:
                    pr_url = m.group(0)
                    sys.stdout.write(str(f"  Sprint PR already exists: {pr_url}") + "\n")
                else:
                    structured_log.error("sprint_pr_create_failed", f"failed to create sprint PR: {stderr}", subprocess_stderr=stderr)
                    return None
            else:
                structured_log.error("sprint_pr_create_failed", f"failed to create sprint PR: {stderr}", subprocess_stderr=stderr)
                return None

        return pr_url
    except Exception as e:
        structured_log.error("sprint_pr_create_failed", f"exception creating sprint PR: {e}", exc=str(e))
        return None


def _dispatch_documenter(
    state: "SprintState",
    sprint_branch: str,
    base_sha: str,
    head_sha: str,
    cfg: Optional["SprintConfig"],
    repo_name: Optional[str],
    timeout_secs: int = 300,
    merge_target: Optional[str] = None,
) -> None:
    """Dispatch the documenter agent after write_sprint_summary() and before _create_sprint_pr().

    Updates state.documenter_status, state.documenter_files_touched, and
    state.documenter_commit_sha in-place. Raises RuntimeError on failure so the
    sprint pipeline fails loudly (AC6).

    When merge_target is provided, only git-verified shipped issues count as merged.
    """
    # Skip: nothing merged this sprint
    if merge_target:
        merged = _git_verified_shipped_issues(state, merge_target)
    else:
        merged = [i for i in state.issues if i.status == "done"]
    if not merged:
        sys.stdout.write(str("  [documenter] skipped: nothing merged this sprint") + "\n")
        state.documenter_status = "skipped"
        return

    # Determine prompt
    if cfg and cfg.documenter_prompt_template:
        prompt_template = cfg.documenter_prompt_template
    else:
        prompt_template = DEFAULT_DOCUMENTER_PROMPT

    eff_repo = repo_name or (cfg.repo_name if cfg else None)

    sprint_filter_url = (
        f"https://github.com/{eff_repo}/issues"
        f"?q=label%3A{state.sprint_label}"
        if eff_repo else ""
    )

    # Read summary_issue_num from state JSON
    summary_issue_num: Optional[int] = None
    # Resolve state path to pick up summary_issue_url
    state_path_doc = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
    if state_path_doc.exists():
        try:
            sd = json.loads(state_path_doc.read_text())
            surl = sd.get("summary_issue_url", "")
            m_issue = re.search(r"/issues/(\d+)$", surl)
            if m_issue:
                summary_issue_num = int(m_issue.group(1))
        except Exception:
            pass

    try:
        prompt = prompt_template.format(
            sprint_label      = state.sprint_label,
            sprint_branch     = sprint_branch,
            base_sha          = base_sha,
            head_sha          = head_sha,
            sprint_filter_url = sprint_filter_url,
            summary_issue_num = summary_issue_num or 0,
            repo_name         = eff_repo or "",
        )
    except KeyError as e:
        structured_log.error("documenter_template_error", f"[documenter] prompt template has unknown placeholder {e}", placeholder=str(e))
        state.documenter_status = "failed"
        raise RuntimeError(
            f"[documenter] prompt template has unknown placeholder {e}; "
            "check documenter_prompt_template in sprint.yaml or DEFAULT_DOCUMENTER_PROMPT"
        ) from e

    # Prefer the dedicated agents clone so the documenter never commits in the
    # tester worktree (mid-sprint) or the serving uat clone.
    cwd_path = (cfg.worktree_agents or cfg.worktree_tester) if cfg else Path.cwd()
    logs_dir = cfg.logs_dir if cfg else Path.cwd()
    log_path = logs_dir / f"sprint-{state.sprint_label}-documenter.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure tester clone has the sprint branch up to date before committing docs
    sys.stdout.write(str(f"  [documenter] Checking out {sprint_branch} in tester clone ...") + "\n")
    sys.stdout.flush()
    try:
        _sm_ref.subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        _sm_ref.subprocess.run(
            ["git", "checkout", sprint_branch],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        _sm_ref.subprocess.run(
            ["git", "pull"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
    except Exception as e:
        structured_log.warn("documenter_git_prep_failed", f"[documenter] git prep failed: {e}", exc=str(e))

    documentor_model = cfg.documentor_model if cfg is not None else "claude-sonnet-4-6"
    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    documentor_model = apply_provider_env(
        sub_env, documentor_model,
        sprint_label=state.sprint_label, cfg=cfg, repo=eff_repo,
    )
    cmd = [
        "claude",
        "--model", documentor_model,
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    sub_env["COMMANDER_MERGE_TARGET"] = sprint_branch
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo

    sys.stdout.write(str("  [documenter] Dispatching documenter ...") + "\n")
    sys.stdout.flush()
    try:
        with log_path.open("w") as log_f:
            proc = _sm_ref.subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=sub_env,
            )
    except FileNotFoundError:
        state.documenter_status = "failed"
        raise RuntimeError("[documenter] claude CLI not found — cannot run documenter (AC6)")

    # 5-minute wall-clock cap (AC7) via max_total_secs
    detector = _sm_ref.HangDetector(issue_num=0, log_path=log_path, proc=proc, max_total_secs=timeout_secs)
    detector.start()
    rc = proc.wait()
    detector.stop()

    if detector.killed:
        state.documenter_status = "failed"
        raise RuntimeError(
            f"[documenter] documenter exceeded {timeout_secs}s wall-clock limit and was killed"
            f" — check {log_path} for details (AC6, AC7)"
        )

    if rc != 0:
        state.documenter_status = "failed"
        raise RuntimeError(
            f"[documenter] documenter exited with code {rc}"
            f" — check {log_path} for details (AC6)"
        )

    # Parse exit line: "Documenter complete: <files or 'none'>"
    files_touched: list[str] = []
    try:
        log_text = log_path.read_text(errors="replace")
        for line in reversed(log_text.splitlines()):
            m = re.search(r"Documenter complete:\s*(.+)", line, re.IGNORECASE)
            if m:
                raw_files = m.group(1).strip()
                if raw_files.lower() != "none":
                    files_touched = [f.strip() for f in raw_files.split(",") if f.strip()]
                break
    except Exception as e:
        structured_log.warn("documenter_log_parse_error", f"[documenter] could not parse documenter log: {e}", exc=str(e))

    # Record commit SHA if a doc commit was made
    doc_commit_sha: Optional[str] = None
    if files_touched:
        try:
            r = _sm_ref.subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(cwd_path), capture_output=True, text=True, check=False,
            )
            if r.returncode == 0:
                doc_commit_sha = r.stdout.strip()
        except Exception:
            pass

        # Push the doc commit to remote so it's included in the sprint PR (AC3)
        sys.stdout.write(str(f"  [documenter] Pushing doc commit to {sprint_branch} ...") + "\n")
        sys.stdout.flush()
        push_r = _sm_ref.subprocess.run(
            ["git", "push", "origin", sprint_branch],
            cwd=str(cwd_path), capture_output=True, text=True, check=False,
        )
        if push_r.returncode != 0:
            state.documenter_status = "failed"
            raise RuntimeError(
                f"[documenter] git push failed (rc={push_r.returncode}): {push_r.stderr.strip()} (AC3, AC6)"
            )

    state.documenter_status        = "succeeded"
    state.documenter_files_touched = files_touched
    state.documenter_commit_sha    = doc_commit_sha
    sys.stdout.write(str(f"  [documenter] Done: {len(files_touched)} file(s) touched"
        + (f" (commit {doc_commit_sha[:8]})" if doc_commit_sha else "")) + "\n")
    sys.stdout.flush()


def _dispatch_reviewer(
    state: "SprintState",
    summary_issue_num: Optional[int],
    sprint_branch: str,
    base_sha: str,
    head_sha: str,
    cfg: Optional["SprintConfig"],
    repo_name: Optional[str],
    merge_target: Optional[str] = None,
) -> None:
    """Dispatch the reviewer agent after sprint PR creation.

    Updates state.reviewer_status, state.reviewer_comment_url, and
    state.reviewer_findings in-place.

    Raises RuntimeError when the prompt template contains an unknown placeholder
    (AC-3: unknown placeholders must surface as a loud ERROR, not a silent skip).
    All other failures are advisory (logged and set state.reviewer_status="failed").

    When merge_target is provided, only git-verified shipped issues count as merged.
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)

    # Skip conditions: use git-verified list when merge_target is available
    if merge_target:
        merged = _git_verified_shipped_issues(state, merge_target)
    else:
        merged = [i for i in state.issues if i.status == "done"]
    if not merged:
        sys.stdout.write(str("  [reviewer] skipped: nothing merged this sprint") + "\n")
        state.reviewer_status = "skipped"
        return

    if summary_issue_num is None:
        sys.stdout.write(str("  [reviewer] skipped: no sprint summary issue available") + "\n")
        state.reviewer_status = "skipped"
        return

    # Determine prompt
    if cfg and cfg.reviewer_prompt_template:
        prompt_template = cfg.reviewer_prompt_template
    else:
        prompt_template = DEFAULT_REVIEWER_PROMPT

    sprint_filter_url = (
        f"https://github.com/{eff_repo}/issues"
        f"?q=label%3A{state.sprint_label}"
        if eff_repo else ""
    )

    try:
        prompt = prompt_template.format(
            sprint_label      = state.sprint_label,
            sprint_branch     = sprint_branch,
            base_sha          = base_sha,
            head_sha          = head_sha,
            summary_issue_num = summary_issue_num,
            sprint_filter_url = sprint_filter_url,
            repo_name         = eff_repo or "",
        )
    except KeyError as e:
        structured_log.error("reviewer_template_error", f"[reviewer] prompt template has unknown placeholder {e}", placeholder=str(e))
        state.reviewer_status = "failed"
        raise RuntimeError(
            f"[reviewer] prompt template has unknown placeholder {e}; "
            "check reviewer_prompt_template in sprint.yaml or DEFAULT_REVIEWER_PROMPT"
        ) from e

    # Prefer the dedicated agents clone so post-sprint agents (reviewer / BA /
    # estimator follow-ups) never run in the coder worktree (mid-sprint) or the
    # serving uat clone.
    cwd_path = (cfg.worktree_agents or cfg.worktree_coder) if cfg else Path.cwd()
    logs_dir = cfg.logs_dir if cfg else Path.cwd()
    log_path = logs_dir / f"sprint-{state.sprint_label}-reviewer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure coder clone has the sprint branch up to date
    sys.stdout.write(str(f"  [reviewer] Fetching {sprint_branch} in coder clone ...") + "\n")
    sys.stdout.flush()
    try:
        _sm_ref.subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        _sm_ref.subprocess.run(
            ["git", "checkout", sprint_branch],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        _sm_ref.subprocess.run(
            ["git", "pull"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
    except Exception as e:
        structured_log.warn("reviewer_git_prep_failed", f"[reviewer] git prep failed: {e}", exc=str(e))

    reviewer_model = cfg.reviewer_model if cfg is not None else "claude-haiku-4-5"
    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    reviewer_model = apply_provider_env(
        sub_env, reviewer_model,
        sprint_label=state.sprint_label, cfg=cfg, repo=eff_repo,
    )
    cmd = [
        "claude",
        "--model", reviewer_model,
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    sub_env.update(_agent_identity_env("reviewer", summary_issue_num))  # issue #719
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo
    sub_env["REPO"]                  = eff_repo or ""
    sub_env["SPRINT_LABEL"]          = state.sprint_label
    sub_env["SPRINT_SUMMARY_ISSUE"]  = str(summary_issue_num)
    sub_env["SPRINT_BRANCH"]         = sprint_branch
    sub_env["BASE_REF"]              = base_sha
    sub_env["HEAD_REF"]              = head_sha

    sys.stdout.write(str("  [reviewer] Dispatching reviewer ...") + "\n")
    sys.stdout.flush()
    try:
        with log_path.open("w") as log_f:
            proc = _sm_ref.subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=sub_env,
            )
    except FileNotFoundError:
        structured_log.warn("claude_cli_not_found", "[reviewer] claude CLI not found — skipping", subprocess="reviewer")
        state.reviewer_status = "skipped"
        return

    # Use HangDetector with issue_num=0 as a sentinel for the reviewer
    detector = _sm_ref.HangDetector(issue_num=0, log_path=log_path, proc=proc)
    detector.start()
    rc = proc.wait()
    detector.stop()

    if detector.killed:
        structured_log.warn("subprocess_killed", "[reviewer] reviewer hung and was killed", subprocess="reviewer")
        state.reviewer_status = "failed"
        record_failure(
            0,
            "reviewer",
            detail=_build_crash_detail(log_path, signal="SIGKILL"),
            summary="Sprint reviewer hung and was killed",
        )
        return

    if rc != 0:
        structured_log.error("subprocess_nonzero_exit", f"[reviewer] reviewer exited with code {rc}", subprocess="reviewer", exit_code=rc)
        state.reviewer_status = "failed"
        record_failure(
            0,
            "reviewer",
            detail=_build_crash_detail(log_path, exit_code=rc),
            summary=f"Sprint reviewer exited with code {rc}",
        )
        return

    # Parse exit line: "Reviewer complete: B blockers, S suggestions, I nits, F follow-up tickets opened"
    findings: dict = {"blockers": 0, "suggestions": 0, "nits": 0, "follow_up_tickets": []}
    comment_url: Optional[str] = None
    try:
        log_text = log_path.read_text(errors="replace")
        for line in reversed(log_text.splitlines()):
            m = re.search(
                r"Reviewer complete:\s*(\d+)\s*blockers?,\s*(\d+)\s*suggestions?,\s*(\d+)\s*nits?,\s*(\d+)\s*follow-up",
                line, re.IGNORECASE,
            )
            if m:
                findings["blockers"]    = int(m.group(1))
                findings["suggestions"] = int(m.group(2))
                findings["nits"]        = int(m.group(3))
                break
        # Parse actual follow-up issue numbers from issue creation URLs in the log.
        # gh issue create outputs: https://github.com/<repo>/issues/N
        # Exclude comment URLs (which contain #issuecomment-) and the sprint summary issue.
        findings["follow_up_tickets"] = _extract_follow_up_issue_nums(
            log_text, eff_repo, summary_issue_num
        )
        # Look for comment URL in log
        url_cm = re.search(r"https://github\.com/[^\s]+/issues/\d+#issuecomment-\d+", log_text)
        if url_cm:
            comment_url = url_cm.group(0)
    except Exception as e:
        structured_log.warn("reviewer_log_parse_error", f"[reviewer] could not parse reviewer log: {e}", exc=str(e))

    state.reviewer_status      = "succeeded"
    state.reviewer_comment_url = comment_url
    state.reviewer_findings    = findings
    sys.stdout.write(str(f"  [reviewer] Done: "
        f"{findings['blockers']} blockers, "
        f"{findings['suggestions']} suggestions, "
        f"{findings['nits']} nits, "
        f"{len(findings['follow_up_tickets'])} follow-up tickets") + "\n")
    sys.stdout.flush()


def _dispatch_ba_for_followup(
    issue_num: int,
    eff_repo: str,
    cfg: Optional[object],
    state: Optional[object],
) -> bool:
    """Invoke the BA agent to rewrite a follow-up ticket body to standard format.

    Returns True on success, False on failure.  Failures are printed but not raised
    so callers can continue processing other tickets.
    """
    # Prefer the dedicated agents clone so post-sprint agents (reviewer / BA /
    # estimator follow-ups) never run in the coder worktree (mid-sprint) or the
    # serving uat clone.
    cwd_path = (cfg.worktree_agents or cfg.worktree_coder) if cfg else Path.cwd()
    logs_dir = cfg.logs_dir if cfg else Path.cwd()
    log_path = logs_dir / f"ba-rewrite-{issue_num}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = _BA_REWRITE_PROMPT.format(issue_num=issue_num, repo=eff_repo)

    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    _ba_model = apply_provider_env(
        sub_env, "claude-sonnet-4-6",
        sprint_label=getattr(state, "sprint_label", None), cfg=cfg, repo=eff_repo,
    )
    cmd = [
        "claude",
        "--model", _ba_model,
        "--dangerously-skip-permissions",
    ]
    ba_persona = _load_agent_persona("ba", cwd_path)
    if ba_persona:
        cmd += ["--append-system-prompt", ba_persona]
    cmd += ["-p", prompt]

    sub_env["CLAUDE_AGENT_ROLE"] = "ba"
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo

    sys.stdout.write(str(f"  [ba-rewrite] Rewriting body for follow-up #{issue_num} ...") + "\n")
    sys.stdout.flush()
    try:
        with log_path.open("w") as log_f:
            proc = _sm_ref.subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=sub_env,
            )
    except FileNotFoundError:
        sys.stdout.write(str(f"  [ba-rewrite] WARNING: claude CLI not found — skipping BA rewrite for #{issue_num}") + "\n")
        sys.stdout.flush()
        return False

    try:
        rc = proc.wait(timeout=_BA_DISPATCH_TIMEOUT)
    except _sm_ref.subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        sys.stdout.write(str(f"  [ba-rewrite] WARNING: BA rewrite for #{issue_num} timed out after {_BA_DISPATCH_TIMEOUT}s") + "\n")
        sys.stdout.flush()
        return False

    if rc != 0:
        sys.stdout.write(str(f"  [ba-rewrite] WARNING: BA rewrite for #{issue_num} exited with code {rc}") + "\n")
        sys.stdout.flush()
        return False

    sys.stdout.write(str(f"  [ba-rewrite] Done for #{issue_num}") + "\n")
    sys.stdout.flush()
    return True


def _dispatch_estimator_for_followup(
    issue_num: int,
    eff_repo: str,
    cfg: Optional[object],
) -> bool:
    """Run estimate_issue.py on a follow-up ticket to apply size label and write estimate file.

    Returns True on success, False on failure.
    """
    if not _ESTIMATE_ISSUE_SCRIPT_SM.exists():
        sys.stdout.write(str(f"  [estimator] WARNING: estimate_issue.py not found — skipping #{issue_num}") + "\n")
        sys.stdout.flush()
        return False

    cmd = [
        sys.executable,
        str(_ESTIMATE_ISSUE_SCRIPT_SM),
        "--issue", str(issue_num),
        "--repo", eff_repo,
        "--save-comment",
        "--save-label",
    ]
    # Write estimate JSON to the canonical project-root .commander/ location so
    # calibration can always find it regardless of which clone runs the dashboard.
    if cfg is not None and hasattr(cfg, "sprints_dir"):
        cmd += ["--commander-dir", str(cfg.sprints_dir.parent)]

    sys.stdout.write(str(f"  [estimator] Estimating follow-up #{issue_num} ...") + "\n")
    sys.stdout.flush()
    est_env = os.environ.copy()
    est_env.update(_agent_identity_env("estimator", issue_num))  # issue #719
    # issue #764: track estimator as a per-issue agent run.
    _est_label = est_env.get("CLAUDE_SPRINT_LABEL", "") or os.environ.get("CLAUDE_SPRINT_LABEL", "")
    _db_agent_start_sm(issue_num, _est_label, "estimator")
    _est_t0 = time.monotonic()
    try:
        result = _sm_ref.subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_ESTIMATOR_DISPATCH_TIMEOUT,
            env=est_env,
        )
    except _sm_ref.subprocess.TimeoutExpired:
        _db_agent_finish_sm(issue_num, _est_label, "estimator",
                            duration_seconds=time.monotonic() - _est_t0, outcome="timeout")
        sys.stdout.write(str(f"  [estimator] WARNING: estimation for #{issue_num} timed out after {_ESTIMATOR_DISPATCH_TIMEOUT}s") + "\n")
        sys.stdout.flush()
        return False

    _db_agent_finish_sm(
        issue_num, _est_label, "estimator",
        duration_seconds=time.monotonic() - _est_t0,
        outcome="succeeded" if result.returncode == 0 else "failed",
    )
    if result.returncode != 0:
        sys.stdout.write(str(f"  [estimator] WARNING: estimation for #{issue_num} exited with code {result.returncode}") + "\n")
        sys.stdout.flush()
        return False

    sys.stdout.write(str(f"  [estimator] Done for #{issue_num}") + "\n")
    sys.stdout.flush()
    return True


def _enrich_followup_tickets(
    follow_up_tickets: list,
    eff_repo: str,
    cfg: Optional[object],
    state: Optional[object],
) -> None:
    """Run BA agent + estimator on each reviewer follow-up ticket.

    Processes tickets sequentially: BA then estimator per ticket.
    A failure on one ticket is logged and does not abort the remaining tickets.
    """
    if not follow_up_tickets:
        return

    sys.stdout.write(str(f"  [enrichment] Enriching {len(follow_up_tickets)} follow-up ticket(s): "
        + ", ".join(f"#{n}" for n in follow_up_tickets)) + "\n")
    sys.stdout.flush()

    for issue_num in follow_up_tickets:
        try:
            _sm_ref._dispatch_ba_for_followup(issue_num, eff_repo, cfg, state)
        except Exception as exc:
            sys.stdout.write(str(f"  [enrichment] WARNING: BA rewrite failed for #{issue_num}: {exc}") + "\n")
            sys.stdout.flush()

        try:
            _sm_ref._dispatch_estimator_for_followup(issue_num, eff_repo, cfg)
        except Exception as exc:
            sys.stdout.write(str(f"  [enrichment] WARNING: estimator failed for #{issue_num}: {exc}") + "\n")
            sys.stdout.flush()

    sys.stdout.write(str(f"  [enrichment] Done enriching {len(follow_up_tickets)} follow-up ticket(s).") + "\n")
    sys.stdout.flush()
