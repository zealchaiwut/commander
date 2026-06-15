"""Tests for issue #892: Derive failure-sidecar base path from project-root resolution, not hardcoded ~/dev"""
import os
import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

# Add dashboard to path like logs_stats_service does
import sys
_dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(_dashboard_root) not in sys.path:
    sys.path.insert(0, str(_dashboard_root))

from routers.logs_stats_service import _get_projects_base, _runtime_dirs_for_project, _read_failure_sidecar


# AC1: _PROJECTS_BASE resolves from COMMANDER_BASE, not hardcoded ~/dev
def test_failure_sidecar__projects_base_from_env():
    """AC1: _PROJECTS_BASE resolves from COMMANDER_BASE env var, not hardcoded ~/dev"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.dict(os.environ, {"COMMANDER_BASE": tmpdir}):
            result = _get_projects_base()
            assert result == Path(tmpdir)
            assert str(result) != str(Path.home() / "dev")


# AC2: When resolved base contains last-failure-<issue>.json, failure message is returned (not just fallback)
def test_failure_sidecar__reads_sidecar_message():
    """AC2: When sidecar exists under resolved base, failure message is returned"""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir) / "my-project"
        project_dir.mkdir()
        runtime_dir = project_dir / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True)

        sidecar_path = runtime_dir / "last-failure-99.json"
        sidecar_data = {
            "issue": 99,
            "failure_class": "CODER_FAILED",
            "summary": "Test failure reason"
        }
        sidecar_path.write_text(json.dumps(sidecar_data))

        runtime_dirs = [runtime_dir]
        sidecar = _read_failure_sidecar(99, runtime_dirs)

        assert sidecar is not None
        assert sidecar.get("failure_class") == "CODER_FAILED"
        assert sidecar.get("summary") == "Test failure reason"


# AC3: When no sidecar exists, code degrades gracefully to _DERIVED_CLASS fallback
def test_failure_sidecar__no_sidecar_degradation():
    """AC3: When no sidecar exists, gracefully returns None (fallback used later)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        runtime_dir = Path(tmpdir) / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True)

        # No sidecar file created
        sidecar = _read_failure_sidecar(99, [runtime_dir])

        assert sidecar is None


# AC4: Resolved base path is logged at startup (DEBUG level)
def test_failure_sidecar__logs_resolved_path_debug():
    """AC4: Resolved base path is logged at DEBUG level"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.dict(os.environ, {"COMMANDER_BASE": tmpdir}):
            with mock.patch("routers.logs_stats_service._log") as mock_log:
                result = _get_projects_base()
                # Should have called _log.debug with the resolved path
                mock_log.debug.assert_called()
                call_args = mock_log.debug.call_args
                assert tmpdir in str(call_args)


# AC5: If COMMANDER_BASE is invalid, fallback to ~/dev with warning
def test_failure_sidecar__invalid_base_fallback():
    """AC5: If COMMANDER_BASE points to non-existent path, falls back to ~/dev and warns"""
    invalid_path = "/nonexistent/path/that/does/not/exist/12345"
    with mock.patch.dict(os.environ, {"COMMANDER_BASE": invalid_path}):
        with mock.patch("routers.logs_stats_service._log") as mock_log:
            result = _get_projects_base()
            # Should fallback to ~/dev
            assert result == Path.home() / "dev"
            # Should have logged a warning
            mock_log.warning.assert_called()
            warning_call = str(mock_log.warning.call_args)
            assert invalid_path in warning_call


def test_failure_sidecar__runtime_dirs_uses_resolved_base():
    """Runtime dirs helper uses resolved base path (tests integration of _get_projects_base)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with mock.patch.dict(os.environ, {"COMMANDER_BASE": tmpdir}):
            with mock.patch("routers.logs_stats_service._get_projects_base") as mock_base:
                mock_base.return_value = Path(tmpdir)
                runtime_dirs = _runtime_dirs_for_project("zealchaiwut/commander")
                # Should include the mocked base in the candidate paths
                assert any(str(tmpdir) in str(d) for d in runtime_dirs)
