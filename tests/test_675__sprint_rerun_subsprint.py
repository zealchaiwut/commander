"""Tests for issue #675: Support Sprint Rerun as Independent Sub-Sprint.

AC coverage:
- AC-1: Triggering Rerun auto-increments sub-sprint number (51 → 51.1, 51.1 → 51.2)
- AC-2: New GitHub label created for sub-sprint (sprint-51.1)
- AC-3: "need rework" issues re-labeled to sprint-51.1; parent label stripped
- AC-4: sprint_manager dispatched with sub_label (creates sprint/sprint-51.1 off develop)
- AC-7: Original sprint plan.json and state untouched; parent state gains rerun_into
- AC-8: If no non-UAT issues, return informative noop (no sub-sprint created)
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import (
    _next_sprint_sublabel,
    _SPRINT_LABEL_RE,
    _sprint_label_sort_key,
    app,
)
from fastapi.testclient import TestClient

client = TestClient(app)


# ── AC-1: sub-sprint number auto-increments ──────────────────────────────────

class TestSubsprintAutoIncrement:
    """_next_sprint_sublabel increments correctly for both plain and dotted labels."""

    def test_plain_to_dot_one(self):
        # 51 → 51.1 when sprint-51.1 does not yet exist
        assert _next_sprint_sublabel("sprint-51", set()) == "sprint-51.1"

    def test_dot_one_to_dot_two(self):
        # 51.1 → 51.2 (flat sibling, not nested)
        assert _next_sprint_sublabel("sprint-51.1", set()) == "sprint-51.2"

    def test_skips_occupied_suffix(self):
        # If sprint-51.1 already exists, next is sprint-51.2
        assert _next_sprint_sublabel("sprint-51", {"sprint-51.1"}) == "sprint-51.2"

    def test_dotted_sibling_increments(self):
        # Running rerun on sprint-51.2 yields sprint-51.3
        assert _next_sprint_sublabel("sprint-51.2", set()) == "sprint-51.3"

    def test_invalid_label_raises(self):
        with pytest.raises(ValueError):
            _next_sprint_sublabel("not-a-sprint", set())


# ── AC-1: SPRINT_LABEL_RE rejects double-dotted labels ───────────────────────

class TestSprintLabelRERejectsTwoLevels:
    """sprint-15.1.1 must be rejected by _SPRINT_LABEL_RE (one dotted level max)."""

    def test_rejects_two_level_dotted(self):
        assert not _SPRINT_LABEL_RE.match("sprint-15.1.1")

    def test_rejects_three_level_dotted(self):
        assert not _SPRINT_LABEL_RE.match("sprint-15.1.2.3")

    def test_accepts_plain(self):
        assert _SPRINT_LABEL_RE.match("sprint-15")

    def test_accepts_one_level_dotted(self):
        assert _SPRINT_LABEL_RE.match("sprint-15.1")


# ── AC-1: sort key returns two-component tuple ────────────────────────────────

class TestSprintLabelSortKeyTwoTuple:
    """_sprint_label_sort_key must return (N, 0) for plain, (N, M) for dotted."""

    def test_plain_returns_zero_suffix(self):
        assert _sprint_label_sort_key("sprint-51") == (51, 0)

    def test_dotted_returns_suffix(self):
        assert _sprint_label_sort_key("sprint-51.1") == (51, 1)
        assert _sprint_label_sort_key("sprint-51.2") == (51, 2)


# ── Helper to run POST /api/sprints/{label}/rerun ────────────────────────────

def _post_rerun(tmp_path, sprint_label, issues, *, fake_sm=True):
    """Post a rerun request with mocked GitHub client and sprint_manager."""
    import github_client as gc
    import subprocess

    gc.list_open_issues_with_body = MagicMock(return_value=issues)
    gc.list_labels = MagicMock(return_value=[])
    gc.get_label_color = MagicMock(return_value="0075ca")
    created_labels = []
    gc.create_label = MagicMock(
        side_effect=lambda n, *a, **kw: created_labels.append(n)
    )
    label_ops = []
    gc.update_labels = MagicMock(
        side_effect=lambda n, add, remove, **kw: label_ops.append((n, add, remove))
    )
    gc.invalidate = MagicMock()

    sm_path = tmp_path / "sprint_manager.py"
    if fake_sm:
        sm_path.touch()

    patches = [
        patch("server._is_sprint_running", return_value=False),
        patch("server._project_root_path", return_value=tmp_path),
        patch("server._coder_clone_path", return_value=tmp_path),
        patch("server.SPRINT_MANAGER_PATH", sm_path),
    ]
    if fake_sm:
        mock_proc = MagicMock()
        mock_proc.pid = 55555
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
        patches.append(patch("subprocess.Popen", return_value=mock_proc))

    with _stack(patches):
        resp = client.post(
            f"/api/sprints/{sprint_label}/rerun?project=owner/repo",
            json={"confirm": True},
        )
    return resp, created_labels, label_ops


class _stack:
    def __init__(self, ctx_managers):
        self._cms = ctx_managers
        self._entered = []

    def __enter__(self):
        for cm in self._cms:
            self._entered.append(cm.__enter__())
        return self

    def __exit__(self, *args):
        for cm, entered in zip(reversed(self._cms), reversed(self._entered)):
            cm.__exit__(*args)


# ── AC-2: new GitHub label created ───────────────────────────────────────────

class TestSubsprintLabelCreated:
    def test_label_sprint_n_dot_one_created(self, tmp_path):
        """AC-2: rerunning sprint-51 creates GitHub label sprint-51.1."""
        issues = [
            {"number": 1, "title": "Needs rework",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        resp, created_labels, _ = _post_rerun(tmp_path, "sprint-51", issues)
        assert resp.status_code == 200
        assert "sprint-51.1" in created_labels

    def test_sub_label_returned_in_response(self, tmp_path):
        issues = [
            {"number": 1, "title": "T",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        resp, _, _ = _post_rerun(tmp_path, "sprint-51", issues)
        assert resp.json()["sub_label"] == "sprint-51.1"


# ── AC-3: need-rework issues re-labeled; parent label stripped ────────────────

class TestNeedReworkIssuesRelabeled:
    def test_needs_rework_issue_moved_to_sublabel(self, tmp_path):
        """AC-3: needs-rework issue gets sub-label added and parent label removed."""
        issues = [
            {"number": 10, "title": "Rework me",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        resp, _, label_ops = _post_rerun(tmp_path, "sprint-51", issues)
        assert resp.status_code == 200
        moved = [op for op in label_ops if op[0] == 10]
        assert moved, "Issue #10 must be label-swapped"
        assert "sprint-51.1" in moved[0][1]  # added
        assert "sprint-51" in moved[0][2]    # removed

    def test_uat_issue_not_moved(self, tmp_path):
        """AC-3: UAT issues keep their parent sprint label."""
        issues = [
            {"number": 20, "title": "Done",
             "labels": [{"name": "sprint-51"}, {"name": "UAT"}]},
            {"number": 21, "title": "Rework",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        resp, _, label_ops = _post_rerun(tmp_path, "sprint-51", issues)
        uat_ops = [op for op in label_ops if op[0] == 20]
        assert not uat_ops, "UAT issue #20 must NOT be re-labeled"

    def test_need_rework_variant_label_moved(self, tmp_path):
        """AC-3: Both 'needs-rework' and 'need-rework' label variants trigger move."""
        issues = [
            {"number": 30, "title": "Old variant",
             "labels": [{"name": "sprint-51"}, {"name": "need-rework"}]},
        ]
        resp, _, label_ops = _post_rerun(tmp_path, "sprint-51", issues)
        moved = [op for op in label_ops if op[0] == 30]
        assert moved, "Issue with 'need-rework' label must be moved"


# ── AC-4: sprint_manager dispatched with sub_label ───────────────────────────

class TestSprintManagerDispatchedWithSubLabel:
    def test_sprint_manager_called_with_sublabel(self, tmp_path):
        """AC-4: sprint_manager is invoked with the sub-sprint label, not the parent."""
        import subprocess

        issues = [
            {"number": 40, "title": "Rework",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=issues)
        gc.list_labels = MagicMock(return_value=[])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        sm_path = tmp_path / "sprint_manager.py"
        sm_path.touch()

        mock_proc = MagicMock()
        mock_proc.pid = 66666
        mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return mock_proc

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", sm_path), \
             patch("subprocess.Popen", side_effect=fake_popen):
            resp = client.post(
                "/api/sprints/sprint-51/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        assert popen_calls, "sprint_manager must be dispatched"
        cmd = popen_calls[0]
        assert "sprint-51.1" in cmd, f"sub_label must appear in command: {cmd}"
        assert "sprint-51" not in [c for c in cmd if c != "sprint-51.1"], \
            "parent label must not appear in subprocess command"


# ── AC-7: original sprint records untouched ──────────────────────────────────

class TestOriginalSprintUntouched:
    def test_parent_plan_json_not_overwritten(self, tmp_path):
        """AC-7: Parent plan.json is not overwritten by the rerun."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        plan_path = sprints_dir / "sprint-51-plan.json"
        original = {"state": "completed", "tickets": [1, 2], "goal": "ship it"}
        plan_path.write_text(json.dumps(original))

        issues = [
            {"number": 50, "title": "Rework",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        _post_rerun(tmp_path, "sprint-51", issues)

        after = json.loads(plan_path.read_text())
        assert after == original, "Parent plan.json must not be modified"

    def test_parent_state_gets_rerun_into(self, tmp_path):
        """AC-7: Parent state.json gains rerun_into = sub_label but keeps other fields."""
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        state_path = sprints_dir / "sprint-51-state.json"
        original = {"sprint_label": "sprint-51", "wall_clock_secs": 300}
        state_path.write_text(json.dumps(original))

        issues = [
            {"number": 51, "title": "Rework",
             "labels": [{"name": "sprint-51"}, {"name": "needs-rework"}]},
        ]
        _post_rerun(tmp_path, "sprint-51", issues)

        after = json.loads(state_path.read_text())
        assert after.get("rerun_into") == "sprint-51.1"
        assert after.get("wall_clock_secs") == 300


# ── AC-8: no non-UAT issues → informative noop ───────────────────────────────

class TestNoReworkIssuesNoop:
    def test_all_uat_returns_noop(self, tmp_path):
        """AC-8: All-UAT sprint returns noop without creating a sub-sprint."""
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=[
            {"number": 60, "title": "Done",
             "labels": [{"name": "sprint-51"}, {"name": "UAT"}]},
        ])
        gc.list_labels = MagicMock(return_value=[])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path):
            resp = client.post(
                "/api/sprints/sprint-51/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("noop") is True
        assert data.get("dispatch_count") == 0
        gc.create_label.assert_not_called()
        gc.update_labels.assert_not_called()

    def test_noop_response_has_message(self, tmp_path):
        """AC-8: Noop response includes an informative message."""
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=[
            {"number": 61, "title": "Done",
             "labels": [{"name": "sprint-51"}, {"name": "UAT-approved"}]},
        ])
        gc.list_labels = MagicMock(return_value=[])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path):
            resp = client.post(
                "/api/sprints/sprint-51/rerun?project=owner/repo",
                json={"confirm": True},
            )

        data = resp.json()
        assert data.get("noop") is True
        assert "message" in data

    def test_empty_sprint_returns_noop(self, tmp_path):
        """AC-8: Sprint with no open issues also returns noop."""
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=[])
        gc.list_labels = MagicMock(return_value=[])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path):
            resp = client.post(
                "/api/sprints/sprint-51/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.json().get("noop") is True

    def test_invalid_label_rejected(self):
        """AC-8 / validation: invalid sprint label returns 400."""
        resp = client.post(
            "/api/sprints/not-a-sprint/rerun?project=owner/repo",
            json={"confirm": True},
        )
        assert resp.status_code == 400

    def test_running_sprint_returns_409(self, tmp_path):
        """AC-8 / guard: 409 when the sprint is currently running."""
        with patch("server._is_sprint_running", return_value=True), \
             patch("server._project_root_path", return_value=tmp_path):
            resp = client.post(
                "/api/sprints/sprint-51/rerun?project=owner/repo",
                json={"confirm": True},
            )
        assert resp.status_code == 409
