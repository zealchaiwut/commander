"""Tests for logs_stats_service project-root resolution (issue #892)."""
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.dashboard.routers import logs_stats_service


@pytest.fixture
def mock_dev_base(tmp_path):
    """Fixture providing a temporary dev directory structure."""
    dev_base = tmp_path / "dev"
    dev_base.mkdir()
    return dev_base


def test_runtime_dirs_for_project_respects_custom_base(mock_dev_base):
    """_runtime_dirs_for_project should derive runtime dirs from a configurable base."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        dirs = logs_stats_service._runtime_dirs_for_project("owner/commander")

        assert len(dirs) > 0
        assert all(str(mock_dev_base) in str(d) for d in dirs), \
            f"Runtime dirs should be under mock base {mock_dev_base}, got {dirs}"


def test_runtime_dirs_for_project_supports_nested_layout(mock_dev_base):
    """Nested layout: <base>/<slug>/.commander/runtime."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        dirs = logs_stats_service._runtime_dirs_for_project("owner/my-project")

        nested = mock_dev_base / "my-project" / ".commander" / "runtime"
        assert nested in dirs, f"Nested path {nested} should be in {dirs}"


def test_runtime_dirs_for_project_supports_coder_clone_nested(mock_dev_base):
    """Nested layout: <base>/<slug>/coder/.commander/runtime."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        dirs = logs_stats_service._runtime_dirs_for_project("owner/my-project")

        coder_nested = mock_dev_base / "my-project" / "coder" / ".commander" / "runtime"
        assert coder_nested in dirs, f"Coder nested path {coder_nested} should be in {dirs}"


def test_runtime_dirs_for_project_supports_flat_layout(mock_dev_base):
    """Flat layout: <base>/<slug>-coder/.commander/runtime."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        dirs = logs_stats_service._runtime_dirs_for_project("owner/my-project")

        flat = mock_dev_base / "my-project-coder" / ".commander" / "runtime"
        assert flat in dirs, f"Flat layout path {flat} should be in {dirs}"


def test_runtime_dirs_for_project_returns_empty_for_none(mock_dev_base):
    """_runtime_dirs_for_project should return empty list when project is None."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        dirs = logs_stats_service._runtime_dirs_for_project(None)
        assert dirs == []


def test_runtime_dirs_for_project_handles_simple_slug(mock_dev_base):
    """_runtime_dirs_for_project should handle simple slugs (no owner/)."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        dirs = logs_stats_service._runtime_dirs_for_project("my-project")

        assert len(dirs) > 0
        assert any("my-project" in str(d) for d in dirs), \
            f"Should find my-project in paths {dirs}"


def test_read_failure_sidecar_loads_from_runtime_dir(mock_dev_base):
    """_read_failure_sidecar should find and load JSON sidecars from runtime dirs."""
    runtime_dir = mock_dev_base / "test-project" / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True)

    sidecar_file = runtime_dir / "last-failure-123.json"
    test_payload = {"failure_class": "CODER_FAILED", "summary": "Test failure"}
    sidecar_file.write_text('{"failure_class": "CODER_FAILED", "summary": "Test failure"}')

    result = logs_stats_service._read_failure_sidecar(123, [runtime_dir])

    assert result == test_payload, f"Should load sidecar, got {result}"


def test_read_failure_sidecar_returns_none_when_absent(mock_dev_base):
    """_read_failure_sidecar should return None when no sidecar exists."""
    runtime_dir = mock_dev_base / "test-project" / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True)

    result = logs_stats_service._read_failure_sidecar(999, [runtime_dir])

    assert result is None, f"Should return None for absent sidecar, got {result}"


def test_ticket_stats_uses_derived_runtime_dirs(mock_dev_base):
    """ticket_stats should use derived runtime dirs when project is provided."""
    with patch("apps.dashboard.routers.logs_stats_service._get_projects_base", return_value=mock_dev_base):
        result = logs_stats_service.ticket_stats("sprint-1", project="owner/test-project")

        assert isinstance(result, dict)
        assert "sprint_label" in result
        assert "tickets" in result


# ── AC1/AC2: _get_projects_base replaces hardcoded _PROJECTS_BASE ─────────────

def test_get_projects_base_returns_custom_path_from_env(mock_dev_base, monkeypatch):
    """AC1/AC2: When COMMANDER_BASE env var is set to a valid dir, return it."""
    monkeypatch.setenv("COMMANDER_BASE", str(mock_dev_base))

    with patch.object(logs_stats_service._log, "debug"):
        result = logs_stats_service._get_projects_base()

    assert result == mock_dev_base


def test_get_projects_base_returns_home_dev_when_no_env(monkeypatch):
    """AC1: When COMMANDER_BASE is not set, return Path.home() / 'dev'."""
    monkeypatch.delenv("COMMANDER_BASE", raising=False)

    with patch.object(logs_stats_service._log, "debug"):
        result = logs_stats_service._get_projects_base()

    assert result == Path.home() / "dev"


# ── AC3: graceful degradation when no sidecar ────────────────────────────────

def test_runtime_dirs_absent_causes_no_exception(tmp_path):
    """AC3: When no sidecar exists under resolved path, no exception is raised."""
    empty_dirs = [tmp_path / "nonexistent" / ".commander" / "runtime"]
    result = logs_stats_service._read_failure_sidecar(42, empty_dirs)
    assert result is None


# ── AC4: startup debug logging ────────────────────────────────────────────────

def test_get_projects_base_logs_resolved_path_at_debug(monkeypatch):
    """AC4: _get_projects_base must emit a debug log with the resolved path."""
    monkeypatch.delenv("COMMANDER_BASE", raising=False)

    with patch.object(logs_stats_service._log, "debug") as mock_debug:
        result = logs_stats_service._get_projects_base()

    assert result is not None
    assert mock_debug.called, "Expected _log.debug to be called with resolved path"
    logged_args = " ".join(str(a) for call in mock_debug.call_args_list for a in call.args)
    assert str(result) in logged_args, (
        f"Expected resolved path '{result}' in debug log args, got: {logged_args}"
    )


def test_get_projects_base_logs_custom_base_at_debug(mock_dev_base, monkeypatch):
    """AC4: _get_projects_base must log the COMMANDER_BASE-resolved path."""
    monkeypatch.setenv("COMMANDER_BASE", str(mock_dev_base))

    with patch.object(logs_stats_service._log, "debug") as mock_debug:
        result = logs_stats_service._get_projects_base()

    assert result == mock_dev_base
    assert mock_debug.called, "Expected _log.debug to be called"
    logged_args = " ".join(str(a) for call in mock_debug.call_args_list for a in call.args)
    assert str(mock_dev_base) in logged_args, (
        f"Expected '{mock_dev_base}' in debug log, got: {logged_args}"
    )


# ── AC5: invalid path fallback with warning ───────────────────────────────────

def test_get_projects_base_warns_and_falls_back_on_invalid_path(tmp_path, monkeypatch):
    """AC5: If COMMANDER_BASE points to a non-existent path, warn and return ~/dev."""
    bad_path = tmp_path / "does_not_exist"
    monkeypatch.setenv("COMMANDER_BASE", str(bad_path))

    with (
        patch.object(logs_stats_service._log, "warning") as mock_warn,
        patch.object(logs_stats_service._log, "debug"),
    ):
        result = logs_stats_service._get_projects_base()

    assert result == Path.home() / "dev", (
        f"Should fall back to ~/dev when COMMANDER_BASE is invalid, got {result}"
    )
    assert mock_warn.called, "Expected a warning when COMMANDER_BASE path does not exist"
    warn_args = " ".join(str(a) for call in mock_warn.call_args_list for a in call.args)
    assert str(bad_path) in warn_args, (
        f"Warning should mention the invalid path '{bad_path}', got: {warn_args}"
    )
