"""Tests for issue #2151: warning emitted when merge tip-SHA fetch fails.

AC (issue #2151):
- When _get_branch_tip_sha returns None (transient gh api error/timeout),
  _merge_sprint_branches_for_label emits a structured logger.warning instead
  of silently skipping the reachability check.
- The warning must identify the branch so the skip is observable in logs.
- The positive path (SHA available) must not emit a spurious warning.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import startup  # noqa: E402


def _step(head: str = "feature/test-branch", base: str = "develop") -> dict:
    return {
        "head": head,
        "base": base,
        "title": f"Merge {head} into {base}",
        "delete_branch": False,
    }


# ── AC: warning when tip SHA unavailable ──────────────────────────────────────

def test_warning_logged_when_tip_sha_is_none():
    """AC: _get_branch_tip_sha returns None → logger.warning is called, not silent skip."""
    step = _step()
    with (
        patch.object(startup, "_finish_merge_steps", return_value=[step]),
        patch.object(startup, "_project_root_path", return_value=Path("/fake/project")),
        patch.object(startup, "_get_branch_tip_sha", return_value=None),
        patch.object(startup, "_gh_merge_branch_via_pr", return_value=(True, "merged", None)),
        patch.object(startup, "_is_commit_merged_into_branch") as mock_verify,
        patch.object(startup.logger, "warning") as mock_warn,
    ):
        errors, _ = startup._merge_sprint_branches_for_label("owner/repo", "sprint-1")

    # Reachability check must be skipped (no SHA to check)
    mock_verify.assert_not_called()
    # Warning must be emitted — not a silent skip
    assert mock_warn.called, "Expected logger.warning when _get_branch_tip_sha returns None"
    # Warning must identify the branch
    all_args = " ".join(str(a) for call in mock_warn.call_args_list for a in call[0])
    assert "feature/test-branch" in all_args, (
        f"Warning must mention branch name 'feature/test-branch'. Got: {all_args}"
    )
    # No merge errors (the merge itself succeeded)
    assert errors == []


def test_warning_mentions_sha_unavailable():
    """AC: the warning message communicates that the SHA was unavailable."""
    step = _step(head="sprint/sprint-42")
    with (
        patch.object(startup, "_finish_merge_steps", return_value=[step]),
        patch.object(startup, "_project_root_path", return_value=Path("/fake/project")),
        patch.object(startup, "_get_branch_tip_sha", return_value=None),
        patch.object(startup, "_gh_merge_branch_via_pr", return_value=(True, "merged", None)),
        patch.object(startup, "_is_commit_merged_into_branch"),
        patch.object(startup.logger, "warning") as mock_warn,
    ):
        startup._merge_sprint_branches_for_label("owner/repo", "sprint-42")

    assert mock_warn.called
    all_args = " ".join(str(a) for call in mock_warn.call_args_list for a in call[0])
    # Message should say something about SHA unavailability so the reader knows WHY it was skipped
    assert any(
        kw in all_args.lower()
        for kw in ("sha", "tip", "unavailable", "verify", "reachability")
    ), f"Warning should describe why verification was skipped. Got: {all_args}"


# ── Negative path: no warning when SHA is available ───────────────────────────

def test_no_warning_when_tip_sha_is_available():
    """When SHA is available and commit is reachable, no spurious warning is logged."""
    step = _step()
    with (
        patch.object(startup, "_finish_merge_steps", return_value=[step]),
        patch.object(startup, "_project_root_path", return_value=Path("/fake/project")),
        patch.object(startup, "_get_branch_tip_sha", return_value="abc12345deadbeef"),
        patch.object(startup, "_gh_merge_branch_via_pr", return_value=(True, "merged", None)),
        patch.object(startup, "_is_commit_merged_into_branch", return_value=True),
        patch.object(startup.logger, "warning") as mock_warn,
    ):
        errors, _ = startup._merge_sprint_branches_for_label("owner/repo", "sprint-1")

    assert not mock_warn.called, (
        "logger.warning must NOT be called when the tip SHA is available"
    )
    assert errors == []


# ── Warning must not suppress merge error from main ok=False path ─────────────

def test_no_warning_when_merge_itself_fails():
    """When the merge fails (ok=False), no spurious SHA warning is added."""
    step = _step()
    with (
        patch.object(startup, "_finish_merge_steps", return_value=[step]),
        patch.object(startup, "_project_root_path", return_value=Path("/fake/project")),
        patch.object(startup, "_get_branch_tip_sha", return_value=None),
        patch.object(startup, "_gh_merge_branch_via_pr", return_value=(False, "merge conflict", None)),
        patch.object(startup, "_is_commit_merged_into_branch"),
        patch.object(startup.logger, "warning") as mock_warn,
    ):
        errors, _ = startup._merge_sprint_branches_for_label("owner/repo", "sprint-1")

    # The merge failure error must be reported
    assert errors, "Merge failure must be recorded in errors list"
    assert "merge conflict" in errors[0]
    # No SHA warning — the verification code is only reached when merge succeeded
    assert not mock_warn.called, (
        "SHA unavailable warning must not fire when the merge itself failed (ok=False)"
    )
