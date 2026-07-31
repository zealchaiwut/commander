"""Regression tests for issue #1355 — zombie sprint labels from stale mirror.

Deleted sprint labels must not reappear in /api/sprints because a stale
`raw.labels` blob in the issues mirror still references them.

AC1: Deleting a sprint label removes it from list_sprints() within one
     live-label TTL cycle (300 s), for both open and closed issues, with no
     server restart.

AC2: _mirror_labels / _all_sprint_label_names do not surface a label absent
     from the live label registry when the live fetch succeeds.

AC3: Regression test — seed a mirror row whose raw.labels contains a label
     not present live → list_sprints() excludes it.

AC4: No GitHub API quota regression on the read path (live label fetch is
     already TTL-cached at 300 s; no new calls added).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import github_client as gc  # noqa: E402


# ── AC2: mirror label absent from live registry is suppressed ─────────────────

class TestZombieLabelFiltered:
    def test_deleted_label_not_in_list_sprints(self, monkeypatch):
        """A label deleted from GitHub (absent from live) must not surface in
        list_sprints(), even if it still exists in stale mirror rows."""
        # Mirror still has sprint-66 (deleted label in stale raw.labels blob)
        # alongside sprint-67 (still live).
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""},
                                       {"name": "sprint-67", "color": ""}])
        # Live fetch: sprint-66 label was deleted; only sprint-67 exists.
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-67"])
        nums = gc.list_sprints("owner/repo")
        assert 66 not in nums, "deleted sprint-66 must not appear after it is absent from live"
        assert 67 in nums

    def test_multiple_deleted_labels_all_suppressed(self, monkeypatch):
        """All deleted labels (sprint-59, sprint-66, sprint-67, sprint-68) must
        be suppressed when they are absent from the live label registry."""
        stale = [{"name": f"sprint-{n}", "color": ""} for n in (59, 66, 67, 68, 90)]
        monkeypatch.setattr(gc, "_mirror_labels", lambda r: stale)
        # Only sprint-90 still exists live.
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: ["sprint-90"])
        nums = gc.list_sprints("owner/repo")
        assert nums == [90], f"only sprint-90 should remain, got {nums}"

    def test_valid_mirror_label_still_surfaces(self, monkeypatch):
        """A mirror label that also exists live must still be included."""
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-95", "color": ""},
                                       {"name": "sprint-96", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-95", "sprint-96", "sprint-97"])
        nums = gc.list_sprints("owner/repo")
        assert 95 in nums
        assert 96 in nums
        assert 97 in nums  # empty live label also surfaces

    def test_list_sprint_labels_excludes_deleted(self, monkeypatch):
        """list_sprint_labels() must also exclude deleted labels."""
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""},
                                       {"name": "sprint-67", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-67"])
        labels = gc.list_sprint_labels("owner/repo")
        assert "sprint-66" not in labels
        assert "sprint-67" in labels


# ── AC3: regression test — stale raw.labels does not resurrect deleted label ──

class TestRegressionStaleRaw:
    def test_stale_raw_label_not_in_list_sprints(self, monkeypatch):
        """Regression: mirror row with raw.labels containing sprint-66 (not live)
        must not cause sprint 66 to appear in list_sprints().

        This is the exact scenario from the PRD zombie investigation: raw.labels
        on closed issues retained sprint-66/67/68/etc. after those labels were
        deleted on GitHub."""
        # _mirror_issues returns the raw-reconstructed dict (as _row_to_issue would)
        # with sprint-66 in its labels — the stale data scenario.
        stale_issue = {
            "number": 100,
            "title": "Closed ticket from old sprint",
            "state": "closed",
            "labels": [{"name": "sprint-66", "color": "0075ca"}, {"name": "bug", "color": "d73a4a"}],
        }
        monkeypatch.setattr(gc, "_mirror_issues", lambda r: [stale_issue])
        # Live: sprint-66 label no longer exists on GitHub.
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: ["sprint-90"])
        gc.invalidate()  # ensure _mirror_labels re-derives from patched _mirror_issues

        nums = gc.list_sprints("owner/repo")
        assert 66 not in nums, (
            "sprint-66 label absent from live must not appear even though "
            "stale raw.labels in the mirror still references it"
        )
        assert 90 in nums

    def test_stale_closed_issue_does_not_resurrect_sprint(self, monkeypatch):
        """Closed issues retain their labels in raw — the zombie source.
        Verifies that closed issue rows specifically are handled."""
        closed_issues = [
            {
                "number": i,
                "title": f"Issue {i}",
                "state": "closed",
                "labels": [{"name": "sprint-66", "color": ""}, {"name": "UAT", "color": ""}],
            }
            for i in range(1, 6)
        ]
        monkeypatch.setattr(gc, "_mirror_issues", lambda r: closed_issues)
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: ["sprint-91"])
        gc.invalidate()

        nums = gc.list_sprints("owner/repo")
        assert 66 not in nums
        assert 91 in nums


# ── AC1 / AC2: live fallback still works when gh is down ──────────────────────

class TestFallbackPreserved:
    def test_live_failure_still_uses_mirror(self, monkeypatch):
        """When _live_sprint_label_names returns [] (gh failure), mirror is
        used as the unfiltered fallback — consistent with pre-existing behavior
        (test_empty_sprint_label_visible__1c.py::test_live_fetch_failure_degrades_to_mirror)."""
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-66", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: [])
        nums = gc.list_sprints("owner/repo")
        assert 66 in nums, "mirror must be used as fallback when live is empty/failed"

    def test_empty_orphan_label_still_surfaces_via_live(self, monkeypatch):
        """Empty sprint labels (no issues) must still surface via the live union.
        This is the original fix from issue #1C-B that must not regress."""
        # Mirror has no sprint-69 (it has 0 issues).
        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-67", "color": ""}])
        # Live returns sprint-69 as an existing (empty) label.
        monkeypatch.setattr(gc, "_live_sprint_label_names",
                            lambda r: ["sprint-67", "sprint-69"])
        nums = gc.list_sprints("owner/repo")
        assert 69 in nums, "empty orphan sprint-69 must surface via live union"
        assert 67 in nums


# ── AC4: no extra gh quota on the read path ───────────────────────────────────

class TestQuotaNeutral:
    def test_all_sprint_label_names_calls_only_live_and_mirror(self, monkeypatch):
        """_all_sprint_label_names must not call _json for any additional gh
        requests beyond what _live_sprint_label_names already does."""
        extra_calls = []

        original_json = gc._json

        def tracking_json(*args, **kwargs):
            extra_calls.append(args)
            return original_json(*args, **kwargs)

        monkeypatch.setattr(gc, "_mirror_labels",
                            lambda r: [{"name": "sprint-90", "color": ""}])
        monkeypatch.setattr(gc, "_live_sprint_label_names", lambda r: ["sprint-90"])
        # _json must NOT be called because live and mirror are already patched.
        monkeypatch.setattr(gc, "_json", tracking_json)

        gc._all_sprint_label_names("owner/repo")

        assert extra_calls == [], (
            "_all_sprint_label_names must not make additional gh API calls "
            f"beyond _live_sprint_label_names; got calls: {extra_calls}"
        )
