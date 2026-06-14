"""Tests for issue #410: Audit _next_sprint_sublabel callers for new parameter.

AC coverage:
- AC-1: _next_sprint_sublabel signature requires existing_label_names parameter
- AC-2: Function skips already-existing labels (collision avoidance)
- AC-3: All production call sites in rerun_sprint pass existing_label_names
- AC-4: Basic incrementing still works with empty existing set
- AC-5: Invalid labels still raise ValueError
"""
import inspect
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import _next_sprint_sublabel, app
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
SERVER_PY = REPO_ROOT / "apps" / "dashboard" / "server.py"

client = TestClient(app)


# ── AC-1: signature requires existing_label_names ────────────────────────────

class TestSignature:
    def test_existing_label_names_is_required_parameter(self):
        sig = inspect.signature(_next_sprint_sublabel)
        assert "existing_label_names" in sig.parameters

    def test_missing_param_raises_type_error(self):
        with pytest.raises(TypeError):
            _next_sprint_sublabel("sprint-15")  # type: ignore[call-arg]


# ── AC-2: collision avoidance ─────────────────────────────────────────────────

class TestCollisionAvoidance:
    def test_skips_existing_dot_one(self):
        existing = {"sprint-15.1"}
        assert _next_sprint_sublabel("sprint-15", existing) == "sprint-15.2"

    def test_skips_multiple_existing(self):
        existing = {"sprint-15.1", "sprint-15.2", "sprint-15.3"}
        assert _next_sprint_sublabel("sprint-15", existing) == "sprint-15.4"

    def test_skips_non_contiguous_gap(self):
        existing = {"sprint-15.1", "sprint-15.3"}
        assert _next_sprint_sublabel("sprint-15", existing) == "sprint-15.2"

    def test_dotted_base_skips_existing(self):
        existing = {"sprint-15.2"}
        assert _next_sprint_sublabel("sprint-15.1", existing) == "sprint-15.3"

    def test_unrelated_labels_not_counted(self):
        existing = {"sprint-14.1", "sprint-16.1", "other-label"}
        assert _next_sprint_sublabel("sprint-15", existing) == "sprint-15.1"


# ── AC-4: basic behavior preserved with empty set ────────────────────────────

class TestBasicBehaviorPreserved:
    def test_plain_sprint_empty_set(self):
        assert _next_sprint_sublabel("sprint-15", set()) == "sprint-15.1"

    def test_dotted_sprint_empty_set(self):
        assert _next_sprint_sublabel("sprint-15.3", set()) == "sprint-15.4"

    def test_sprint_one_empty_set(self):
        assert _next_sprint_sublabel("sprint-1", set()) == "sprint-1.1"


# ── AC-5: invalid labels still raise ─────────────────────────────────────────

class TestInvalidLabels:
    def test_non_sprint_string_raises(self):
        with pytest.raises(ValueError):
            _next_sprint_sublabel("not-a-sprint", set())

    def test_nested_dot_raises(self):
        with pytest.raises(ValueError):
            _next_sprint_sublabel("sprint-15.1.1", set())


# ── AC-3: production call site passes existing_label_names ───────────────────

class TestCallSiteAudit:
    def test_all_call_sites_pass_existing_label_names(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        calls = re.findall(
            r"_next_sprint_sublabel\s*\(\s*sprint_label,\s*existing_label_names",
            source,
        )
        assert len(calls) == 3, f"Expected 3 call sites, found {len(calls)}"

    def test_call_site_passes_existing_label_names(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        call_match = re.search(
            r"_next_sprint_sublabel\s*\((?!.*def\s)([^)]+)\)",
            source,
        )
        assert call_match, "Could not find _next_sprint_sublabel call site"
        args = call_match.group(1)
        assert "existing_label_names" in args, \
            f"Call site does not pass existing_label_names. Args found: {args!r}"

    def test_rerun_fetches_labels_before_call(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        rerun_fn_match = re.search(
            r"def rerun_sprint\b.*?(?=\ndef \w|\Z)",
            source,
            re.DOTALL,
        )
        assert rerun_fn_match, "rerun_sprint function not found"
        body = rerun_fn_match.group(0)
        assert "list_labels" in body, "rerun_sprint must call list_labels to populate existing_label_names"
        list_labels_pos = body.find("list_labels")
        call_site_pos = body.find("_next_sprint_sublabel(")
        assert list_labels_pos < call_site_pos, \
            "list_labels must be called BEFORE _next_sprint_sublabel in rerun_sprint"


# ── Integration: rerun endpoint uses collision-safe sublabel ─────────────────

class TestRerunEndpointCollisionSafe:
    def test_endpoint_skips_existing_sublabel(self, tmp_path):
        """When sprint-15.1 already exists on GitHub, rerun should produce sprint-15.2."""
        import github_client as gc
        import subprocess

        issue = {
            "number": 10, "title": "Needs work",
            "labels": [{"name": "sprint-15"}, {"name": "in-progress"}],
        }
        gc.list_open_issues_with_body = MagicMock(return_value=[issue])
        # Simulate sprint-15.1 already exists on GitHub
        gc.list_labels = MagicMock(return_value=[
            {"name": "sprint-15"},
            {"name": "sprint-15.1"},
        ])
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
            mock_proc.pid = 55555
            mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)
            mock_popen.return_value = mock_proc

            resp = client.post(
                "/api/sprints/sprint-15/rerun?project=owner/repo",
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("sub_label") == "sprint-15.2", \
            "Should skip existing sprint-15.1 and produce sprint-15.2"
        assert "sprint-15.2" in created
        assert "sprint-15.1" not in created
