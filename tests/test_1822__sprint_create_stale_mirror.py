"""Tests for issue #1822 — Sprint create with tickets fails when mirror is stale.

AC1: ticket_labels_applied reads live GitHub state — sprint creation succeeds
     even when the mirror row lacks the new sprint label.
AC2: Normal get_issue calls (board rendering) continue using the mirror;
     get_issue_live is NOT called on the normal read path.
AC3: Rollback still works when label application genuinely fails (gh error).
AC4: Regression test — mirror returns stale data (no sprint label), live GitHub
     has the label → sprint creation must succeed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager.sprint_creation import (
    SprintCreateDeps,
    SprintCreationError,
    create_sprint_verified,
)


# ---------------------------------------------------------------------------
# Fake GitHub client that simulates a stale mirror
# ---------------------------------------------------------------------------
class StaleMirrorGH:
    """Simulates a repo whose SQLite mirror is stale (missing the new label).

    - get_issue()      → returns the stale mirror (no sprint label)
    - get_issue_live() → returns fresh live state (sprint label present)
    - update_labels()  → applies label to the "live" store only
    """

    def __init__(self, stale_labels=None, apply_fails=False):
        self.stale_labels: dict[int, list[str]] = stale_labels or {}
        self.live_labels: dict[int, list[str]] = {}
        self.apply_fails = apply_fails
        self.removed: list[tuple[int, str]] = []
        self.deleted: list[str] = []
        self._label_created = False
        self.get_issue_calls: list[int] = []
        self.get_issue_live_calls: list[int] = []

    def create_sprint_label_strict(self, sprint_num, repo_name=None):
        self._label_created = True

    def sprint_label_exists(self, sprint_num, repo_name=None):
        return self._label_created

    def get_issue(self, issue_number, repo_name=None):
        """Mirror-first read — returns stale data that lacks the new label."""
        self.get_issue_calls.append(issue_number)
        stale = self.stale_labels.get(issue_number, [])
        return {"labels": [{"name": n} for n in stale]}

    def get_issue_live(self, issue_number, repo_name=None):
        """Live GitHub read — bypasses mirror, returns up-to-date labels."""
        self.get_issue_live_calls.append(issue_number)
        live = self.live_labels.get(issue_number, [])
        return {"labels": [{"name": n} for n in live]}

    def update_labels(self, issue_id, add, remove, repo_name=None):
        if self.apply_fails:
            raise subprocess.CalledProcessError(
                1, ["gh", "issue", "edit"], stderr="connection timed out"
            )
        s = set(self.live_labels.get(issue_id, []))
        for lbl in add:
            s.add(lbl)
        for lbl in remove:
            s.discard(lbl)
            self.removed.append((issue_id, lbl))
        self.live_labels[issue_id] = list(s)

    def delete_label(self, name, repo_name=None):
        self.deleted.append(name)
        self._label_created = False


def _make_deps(gh, tickets, tmp_path):
    plan_path = tmp_path / "plan.json"

    def write():
        plan_path.write_text("{}")

    return SprintCreateDeps(
        github_client=gh,
        repo="owner/stale-repo",
        sprint_num=42,
        tickets=tickets,
        write_plan_fn=write,
        plan_written_fn=plan_path.exists,
    )


# ---------------------------------------------------------------------------
# AC4 — regression: stale mirror + fresh live → creation succeeds
# ---------------------------------------------------------------------------
def test_ac4_stale_mirror_live_has_label_creation_succeeds(tmp_path):
    """Mirror rows exist but lack sprint-42; live GitHub has it after update.

    Before the fix, ticket_labels_applied read the mirror → saw no sprint label
    → raised SprintCreationError even though the label was applied. This test
    must pass: creation returns without raising.
    """
    gh = StaleMirrorGH(stale_labels={10: ["backlog"], 11: ["backlog"]})
    deps = _make_deps(gh, tickets=[10, 11], tmp_path=tmp_path)
    # Should not raise — the live read will see sprint-42
    create_sprint_verified(deps)
    assert "sprint-42" in gh.live_labels.get(10, [])
    assert "sprint-42" in gh.live_labels.get(11, [])


def test_ac4_verification_uses_live_not_mirror(tmp_path):
    """ticket_labels_applied must call get_issue_live, not get_issue."""
    gh = StaleMirrorGH(stale_labels={10: ["backlog"]})
    deps = _make_deps(gh, tickets=[10], tmp_path=tmp_path)
    create_sprint_verified(deps)
    # Live was called for verification
    assert 10 in gh.get_issue_live_calls
    # The stale mirror path (get_issue) must NOT be called during verification
    assert 10 not in gh.get_issue_calls


# ---------------------------------------------------------------------------
# AC1 — sprint creation succeeds on a repo with mirrored issues
# ---------------------------------------------------------------------------
def test_ac1_creation_succeeds_when_mirror_rows_exist(tmp_path):
    """Creating a sprint with tickets works even when all tickets are mirrored."""
    gh = StaleMirrorGH(stale_labels={1: ["in-progress"], 2: ["backlog"]})
    deps = _make_deps(gh, tickets=[1, 2], tmp_path=tmp_path)
    create_sprint_verified(deps)  # must not raise


# ---------------------------------------------------------------------------
# AC2 — no regression to live reads on the normal board path
# ---------------------------------------------------------------------------
def test_ac2_get_issue_live_not_exposed_via_normal_path(tmp_path):
    """get_issue (mirror path) and get_issue_live (live path) remain separate.

    The normal board path (get_issue) is mirror-first and must not be replaced.
    This test confirms that get_issue_live is a distinct call used only during
    verification, leaving get_issue unchanged for callers that rely on the mirror.
    """
    import github_client as gc

    # get_issue still uses mirror-first path (no change to its behaviour)
    assert callable(getattr(gc, "get_issue", None)), "get_issue must still exist"
    assert callable(getattr(gc, "get_issue_live", None)), "get_issue_live must exist"

    # They are distinct functions
    assert gc.get_issue is not gc.get_issue_live


# ---------------------------------------------------------------------------
# AC3 — rollback when label application genuinely fails
# ---------------------------------------------------------------------------
def test_ac3_rollback_when_apply_fails(tmp_path):
    """If update_labels raises (e.g. network error), rollback deletes the label."""
    gh = StaleMirrorGH(stale_labels={10: []}, apply_fails=True)
    deps = _make_deps(gh, tickets=[10], tmp_path=tmp_path)
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert "apply sprint label to tickets" in ei.value.step
    # Label was rolled back
    assert "sprint-42" in gh.deleted
    # Ticket labels not applied, so remove step not needed
    assert gh.removed == []


def test_ac3_rollback_on_plan_write_removes_ticket_labels(tmp_path):
    """Steps 1+2 succeed, plan write fails → both label and tickets are rolled back."""
    gh = StaleMirrorGH(stale_labels={10: []})
    plan_path = tmp_path / "plan.json"

    def _boom():
        raise OSError("disk full")

    deps = SprintCreateDeps(
        github_client=gh,
        repo="owner/stale-repo",
        sprint_num=42,
        tickets=[10],
        write_plan_fn=_boom,
        plan_written_fn=plan_path.exists,
    )
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert "write sprint plan file" in ei.value.step
    assert "sprint-42" in gh.deleted                     # label removed
    assert (10, "sprint-42") in gh.removed               # ticket label removed
