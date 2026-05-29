"""Tests for issue #360: sprint re-run creates sub-label with unfinished tickets.

AC coverage:
- AC-1:  Re-running sprint-N keeps UAT tickets on sprint-N; moves 6 unfinished to sprint-N.1
- AC-2:  sprint-N.1 GitHub label created with same color; both labels exist after re-run
- AC-3:  Dispatch begins on sprint-N.1 with correct per-ticket policy
- AC-4:  UAT tickets on sprint-N untouched (no label change, no dispatch)
- AC-5:  Re-running sprint-N.1 produces sprint-N.2 (flat suffix, not sprint-N.1.1)
- AC-6:  Re-running sprint-N.5 produces sprint-N.6
- AC-7:  New state file written for sprint-N.1; sprint-N state NOT overwritten
- AC-8:  sprint-N marked with rerun_into after re-run; running indicator follows sub-label
- AC-9:  All-UAT no-op: no sub-label created, clear message returned
- AC-10: Actively-running sprint returns HTTP 409
- AC-11: Board renders both sprint-N and sprint-N.1 as separate panes, sorted naturally
- AC-12: Dotted sub-labels rendered identically to plain sprint labels
"""
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import (
    _next_sprint_sublabel,
    _sprint_label_sort_key,
    _load_sprint_order,
    _rerun_policy,
    _SPRINT_LABEL_RE,
    app,
)
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
APP_JS = REPO_ROOT / "apps" / "dashboard" / "static" / "app.js"

client = TestClient(app)


# ── AC-1/5/6: _next_sprint_sublabel ──────────────────────────────────────────

class TestNextSprintSublabel:
    """_next_sprint_sublabel() must increment suffix correctly."""

    def test_plain_sprint_produces_dot_one(self):
        assert _next_sprint_sublabel("sprint-15") == "sprint-15.1"

    def test_plain_sprint_one(self):
        assert _next_sprint_sublabel("sprint-1") == "sprint-1.1"

    def test_dot_one_produces_dot_two(self):
        assert _next_sprint_sublabel("sprint-15.1") == "sprint-15.2"

    def test_dot_three_produces_dot_four(self):
        assert _next_sprint_sublabel("sprint-15.3") == "sprint-15.4"

    def test_sprint_n_five_produces_n_six(self):
        # AC-6: sprint-N.5 → sprint-N.6
        assert _next_sprint_sublabel("sprint-25.5") == "sprint-25.6"

    def test_different_base_number(self):
        assert _next_sprint_sublabel("sprint-100.9") == "sprint-100.10"

    def test_invalid_label_raises(self):
        with pytest.raises(ValueError):
            _next_sprint_sublabel("not-a-sprint")
        with pytest.raises(ValueError):
            _next_sprint_sublabel("sprint-abc")


# ── AC-2: _SPRINT_LABEL_RE accepts dotted labels ─────────────────────────────

class TestSprintLabelRE:
    """_SPRINT_LABEL_RE must accept both plain and dotted labels."""

    def test_plain_sprint_matches(self):
        assert _SPRINT_LABEL_RE.match("sprint-15")
        assert _SPRINT_LABEL_RE.match("sprint-1")
        assert _SPRINT_LABEL_RE.match("sprint-100")

    def test_dotted_sub_label_matches(self):
        assert _SPRINT_LABEL_RE.match("sprint-15.1")
        assert _SPRINT_LABEL_RE.match("sprint-15.10")
        assert _SPRINT_LABEL_RE.match("sprint-1.2")

    def test_invalid_labels_rejected(self):
        assert not _SPRINT_LABEL_RE.match("sprint-abc")
        assert not _SPRINT_LABEL_RE.match("sprint-15.abc")
        assert not _SPRINT_LABEL_RE.match("sprint_15")
        assert not _SPRINT_LABEL_RE.match("Sprint-15")
        assert not _SPRINT_LABEL_RE.match("sprint-15.1.1")


# ── AC-11: _sprint_label_sort_key and natural ordering ───────────────────────

class TestSprintLabelSortKey:
    def test_plain_sprint_sort_key(self):
        assert _sprint_label_sort_key("sprint-15") == (15, 0)
        assert _sprint_label_sort_key("sprint-5") == (5, 0)

    def test_dotted_sprint_sort_key(self):
        assert _sprint_label_sort_key("sprint-15.1") == (15, 1)
        assert _sprint_label_sort_key("sprint-15.10") == (15, 10)

    def test_natural_sort_order(self):
        labels = ["sprint-15.2", "sprint-16", "sprint-15", "sprint-15.1"]
        sorted_labels = sorted(labels, key=_sprint_label_sort_key)
        assert sorted_labels == ["sprint-15", "sprint-15.1", "sprint-15.2", "sprint-16"]

    def test_sprint_n_before_sprint_n_dot_x(self):
        # sprint-N (suffix=0) comes before sprint-N.1 (suffix=1)
        assert _sprint_label_sort_key("sprint-15") < _sprint_label_sort_key("sprint-15.1")
        assert _sprint_label_sort_key("sprint-15.1") < _sprint_label_sort_key("sprint-15.2")
        assert _sprint_label_sort_key("sprint-15.2") < _sprint_label_sort_key("sprint-16")


# ── AC-7: _load_sprint_order handles dotted sub-labels ───────────────────────

class TestLoadSprintOrder:
    def test_sub_labels_included_in_order(self, tmp_path):
        all_labels = ["sprint-15", "sprint-15.1", "sprint-16"]
        result = _load_sprint_order(tmp_path, all_labels)
        assert "sprint-15" in result
        assert "sprint-15.1" in result
        assert "sprint-16" in result

    def test_sub_labels_sorted_naturally(self, tmp_path):
        all_labels = ["sprint-15.2", "sprint-15", "sprint-15.1"]
        result = _load_sprint_order(tmp_path, all_labels)
        # No saved order, so appended in natural sort order
        assert result == ["sprint-15", "sprint-15.1", "sprint-15.2"]

    def test_saved_order_respected(self, tmp_path):
        order_path = tmp_path / ".commander" / "sprint-order.json"
        order_path.parent.mkdir(parents=True)
        order_path.write_text(json.dumps(["sprint-15.1", "sprint-15"]))
        all_labels = ["sprint-15", "sprint-15.1"]
        result = _load_sprint_order(tmp_path, all_labels)
        assert result == ["sprint-15.1", "sprint-15"]  # saved order honored


# ── AC-9: no-op when all tickets are UAT ─────────────────────────────────────

class TestRerunAllUatNoop:
    def test_all_uat_returns_noop(self, tmp_path):
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=[
            {
                "number": 1, "title": "Done ticket",
                "labels": [{"name": "sprint-15"}, {"name": "UAT"}],
            },
        ])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", tmp_path / "fake"):
            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("noop") is True
        assert data["dispatch_count"] == 0
        gc.create_label.assert_not_called()
        gc.update_labels.assert_not_called()

    def test_all_uat_approved_returns_noop(self, tmp_path):
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=[
            {
                "number": 2, "title": "Approved ticket",
                "labels": [{"name": "sprint-15"}, {"name": "UAT-approved"}],
            },
        ])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", tmp_path / "fake"):
            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("noop") is True
        assert "message" in data


# ── AC-10: 409 when sprint is actively running ────────────────────────────────

class TestRerun409WhenRunning:
    def test_409_when_sprint_running(self, tmp_path):
        with patch("server._is_sprint_running", return_value=True), \
             patch("server._project_root_path", return_value=tmp_path):
            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )
        assert resp.status_code == 409

    def test_invalid_label_rejected(self):
        resp = client.post(
            "/api/sprints/invalid-label/rerun?project=owner/repo",
            json={"confirm": True},
        )
        assert resp.status_code == 400

    def test_dotted_label_accepted(self, tmp_path):
        """sprint-15.1 must pass _SPRINT_LABEL_RE validation."""
        import github_client as gc
        gc.list_open_issues_with_body = MagicMock(return_value=[])
        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", tmp_path / "fake"):
            resp = client.post(
                "/api/sprints/sprint-15.1/rerun?project=owner/repo",
                json={"confirm": True},
            )
        # No tickets means noop — but not a 400
        assert resp.status_code == 200


# ── AC-1/2/3/4: sub-label created, tickets moved, UAT left alone ─────────────

class TestRerunSubLabel:
    def test_sublabel_computed_correctly(self, tmp_path):
        """sprint-15 re-run should create sprint-15.1 label and move unfinished tickets."""
        import github_client as gc

        uat_issue = {
            "number": 1, "title": "UAT ticket",
            "labels": [{"name": "sprint-15"}, {"name": "UAT"}],
        }
        inprog_issue = {
            "number": 2, "title": "In-progress ticket",
            "labels": [{"name": "sprint-15"}, {"name": "in-progress"}],
        }
        gc.list_open_issues_with_body = MagicMock(return_value=[uat_issue, inprog_issue])
        gc.get_label_color = MagicMock(return_value="0075ca")
        created_labels = []
        gc.create_label = MagicMock(side_effect=lambda n, *a, **kw: created_labels.append(n))
        label_updates = []
        gc.update_labels = MagicMock(
            side_effect=lambda n, add, remove, **kw: label_updates.append((n, add, remove))
        )
        gc.invalidate = MagicMock()

        fake_sm = tmp_path / "sprint_manager.py"
        fake_sm.touch()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", fake_sm), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            import subprocess
            mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
            mock_popen.return_value = mock_proc

            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        # AC-2: sub_label is sprint-15.1
        assert data.get("sub_label") == "sprint-15.1"
        assert data.get("noop") is not True

        # AC-2: sprint-15.1 label was created on GitHub
        assert "sprint-15.1" in created_labels

        # AC-3: unfinished ticket #2 moved to sprint-15.1 (parent label removed)
        moved = [u for u in label_updates if u[0] == 2]
        assert moved, "In-progress ticket #2 should be moved to sub-label"
        move_op = moved[0]
        assert "sprint-15.1" in move_op[1]  # added
        assert "sprint-15" in move_op[2]    # removed

        # AC-4: UAT ticket #1 was NOT moved
        uat_moved = [u for u in label_updates if u[0] == 1]
        assert not uat_moved, "UAT ticket #1 should NOT be moved"

    def test_sublabel_dotted_increments(self, tmp_path):
        """AC-5: Rerunning sprint-15.1 creates sprint-15.2 (not sprint-15.1.1)."""
        import github_client as gc
        import subprocess

        issue = {
            "number": 3, "title": "Needs rework",
            "labels": [{"name": "sprint-15.1"}, {"name": "needs-rework"}],
        }
        gc.list_open_issues_with_body = MagicMock(return_value=[issue])
        gc.get_label_color = MagicMock(return_value="0075ca")
        created = []
        gc.create_label = MagicMock(side_effect=lambda n, *a, **kw: created.append(n))
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        fake_sm = tmp_path / "sprint_manager.py"
        fake_sm.touch()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", fake_sm), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12346
            mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
            mock_popen.return_value = mock_proc

            resp = client.post(
                "/api/sprints/sprint-15.1/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("sub_label") == "sprint-15.2", "AC-5: Should produce sprint-15.2, not sprint-15.1.1"
        assert "sprint-15.2" in created

    def test_parent_state_not_deleted(self, tmp_path):
        """AC-7: Parent sprint state file is updated with rerun_into, NOT deleted."""
        import github_client as gc
        import subprocess

        issue = {
            "number": 4, "title": "SIT ticket",
            "labels": [{"name": "sprint-15"}, {"name": "SIT"}],
        }
        gc.list_open_issues_with_body = MagicMock(return_value=[issue])
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        # Create a parent state file
        sprints_dir = tmp_path / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        parent_state_path = sprints_dir / "sprint-15-state.json"
        parent_state_path.write_text(
            json.dumps({"sprint_label": "sprint-15", "wall_clock_secs": 100})
        )

        fake_sm = tmp_path / "sprint_manager.py"
        fake_sm.touch()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", fake_sm), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12347
            mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
            mock_popen.return_value = mock_proc

            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200

        # Parent state file must still exist (AC-7: not deleted)
        assert parent_state_path.exists(), "Parent state file must not be deleted"
        state = json.loads(parent_state_path.read_text())
        # AC-8: Parent has rerun_into field
        assert state.get("rerun_into") == "sprint-15.1", \
            "Parent state must have rerun_into = sprint-15.1"
        assert "wall_clock_secs" in state, "Original fields must be preserved"

    def test_same_color_used_for_sub_label(self, tmp_path):
        """AC-2: Sub-label created with same color as parent sprint label."""
        import github_client as gc
        import subprocess

        issue = {
            "number": 5, "title": "In-progress",
            "labels": [{"name": "sprint-15"}, {"name": "in-progress"}],
        }
        gc.list_open_issues_with_body = MagicMock(return_value=[issue])
        parent_color = "ff5733"
        gc.get_label_color = MagicMock(return_value=parent_color)
        create_calls = []
        gc.create_label = MagicMock(
            side_effect=lambda n, c, **kw: create_calls.append((n, c))
        )
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        fake_sm = tmp_path / "sprint_manager.py"
        fake_sm.touch()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", fake_sm), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12348
            mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
            mock_popen.return_value = mock_proc

            client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )

        # The created label should have the same color as the parent
        sub_label_creates = [(n, c) for n, c in create_calls if n == "sprint-15.1"]
        assert sub_label_creates, "sprint-15.1 label must be created"
        assert sub_label_creates[0][1] == parent_color, \
            f"Sub-label color must match parent ({parent_color})"


# ── AC-3: ticket routing policy on sub-label ─────────────────────────────────

class TestRerunTicketRouting:
    """The decisions list in the response must route tickets correctly per AC-3."""

    def _run_rerun(self, tmp_path, issue_labels):
        import github_client as gc
        import subprocess

        issues = [
            {"number": i + 1, "title": f"Issue {i + 1}", "labels": lbls}
            for i, lbls in enumerate(issue_labels)
        ]
        gc.list_open_issues_with_body = MagicMock(return_value=issues)
        gc.get_label_color = MagicMock(return_value="0075ca")
        gc.create_label = MagicMock()
        gc.update_labels = MagicMock()
        gc.invalidate = MagicMock()

        fake_sm = tmp_path / "sprint_manager.py"
        fake_sm.touch()

        with patch("server._is_sprint_running", return_value=False), \
             patch("server._project_root_path", return_value=tmp_path), \
             patch("server._coder_clone_path", return_value=tmp_path), \
             patch("server.SPRINT_MANAGER_PATH", fake_sm), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
            mock_popen.return_value = mock_proc

            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )
        return resp.json()

    def test_sit_dispatched_to_tester(self, tmp_path):
        data = self._run_rerun(tmp_path, [
            [{"name": "sprint-15"}, {"name": "SIT"}],
        ])
        tester_decisions = [d for d in data["decisions"] if d["action"] == "dispatch_tester"]
        assert len(tester_decisions) == 1

    def test_in_progress_dispatched_to_coder(self, tmp_path):
        data = self._run_rerun(tmp_path, [
            [{"name": "sprint-15"}, {"name": "in-progress"}],
        ])
        coder_decisions = [d for d in data["decisions"] if d["action"] == "dispatch_coder"]
        assert len(coder_decisions) == 1

    def test_uat_skipped(self, tmp_path):
        data = self._run_rerun(tmp_path, [
            [{"name": "sprint-15"}, {"name": "UAT"}],
            [{"name": "sprint-15"}, {"name": "in-progress"}],
        ])
        skipped = [d for d in data["decisions"] if d["action"] == "skip"]
        dispatched = [d for d in data["decisions"] if d["action"] != "skip"]
        assert len(skipped) == 1
        assert len(dispatched) == 1


# ── Frontend JS tests ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def js():
    return APP_JS.read_text(encoding="utf-8")


class TestFrontendHelpers:
    """sprintLabelCompare and sprintLabelDisplay must be defined correctly."""

    def test_sprint_label_compare_defined(self, js):
        assert "function sprintLabelCompare" in js

    def test_sprint_label_display_defined(self, js):
        assert "function sprintLabelDisplay" in js

    def test_display_returns_dotted_name(self, js):
        fn_match = re.search(
            r"function sprintLabelDisplay\(label\)\s*\{(.*?)\n\}",
            js, re.DOTALL
        )
        assert fn_match, "sprintLabelDisplay not found"
        body = fn_match.group(1)
        # Must handle both plain and dotted forms
        assert "m[2]" in body, "Display function must handle dotted sub-labels (m[2] check)"

    def test_compare_parses_suffix(self, js):
        fn_match = re.search(
            r"function sprintLabelCompare\(a, b\)\s*\{(.*?)\n\}",
            js, re.DOTALL
        )
        assert fn_match, "sprintLabelCompare not found"
        body = fn_match.group(1)
        assert "parseInt" in body, "Compare must parse integers"


class TestFrontendSprintRendering:
    """smgmtRender must use allSprintEntries with label-based sorting."""

    def test_all_sprint_entries_used(self, js):
        assert "allSprintEntries" in js, "allSprintEntries must be used in smgmtRender"

    def test_all_sprint_nums_removed(self, js):
        assert "const allSprintNums" not in js, \
            "allSprintNums must be replaced by allSprintEntries"

    def test_sprint_label_compare_used_in_sort(self, js):
        # smgmtRender must sort using sprintLabelCompare
        render_idx = js.find("function smgmtRender()")
        assert render_idx >= 0, "smgmtRender not found"
        render_body = js[render_idx:render_idx + 5000]
        assert "sprintLabelCompare" in render_body, \
            "smgmtRender must use sprintLabelCompare for sorting"

    def test_sprint_label_field_used_for_grouping(self, js):
        assert "iss.sprint_label" in js, "Issue grouping must use sprint_label field from API"

    def test_sprint_label_display_used_in_block(self, js):
        fn_idx = js.find("function smgmtSprintBlockHtml(")
        assert fn_idx >= 0, "smgmtSprintBlockHtml not found"
        fn_body = js[fn_idx:fn_idx + 3000]
        assert "sprintLabelDisplay" in fn_body, \
            "smgmtSprintBlockHtml must use sprintLabelDisplay for header text"

    def test_id_sanitization_handles_dots(self, js):
        # IDs should replace both hyphens and dots to avoid CSS selector issues
        assert r"replace(/\./g, '_')" in js, \
            "Dot characters in labels must be sanitized for HTML IDs"


class TestFrontendDragDrop:
    """Drag-drop and bulk-move must handle dotted sub-labels correctly."""

    def test_drop_uses_sprint_label_for_dotted(self, js):
        assert "isDotted" in js, \
            "Drag-drop must detect and specially handle dotted sub-labels"

    def test_api_body_uses_sprint_label_field(self, js):
        assert "sprint_label: targetSprintLabel" in js, \
            "Drop handler API call must use sprint_label field for dotted labels"

    def test_bulk_move_uses_sprint_label(self, js):
        assert "isDottedDest" in js, \
            "Bulk-move must detect dotted destinations"
