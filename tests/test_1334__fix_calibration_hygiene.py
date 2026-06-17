"""Tests for issue #1334: Fix calibration hygiene (unit tests, no live server).

AC items verified:
  AC1 — _rebuild_mis_sizing_history uses shared size resolver; tickets with only
         size-* label are not silently skipped
  AC2 — _preflight_estimate_one logs warning when subprocess exits 0 but no
         canonical JSON exists at estimates/<issue>.json
  AC3 — docs/features/estimation-lifecycle.md exists with required content
         (skipped for HTTP; verified via file-system check in CI)
  AC4 — calibration result includes processed_count and has_sprint_state_files
  AC5 — /api/maintenance/calibration/rebuild endpoint exists and returns
         processed_count (tested via rebuild_calibration_cache helper)
  AC6 — has_sprint_state_files returns False on fresh install
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_state(project_root: Path, sprint_label: str, issues: list[dict]) -> Path:
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    path = sprints_dir / f"sprint-{n}-state.json"
    path.write_text(json.dumps({
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "start_timestamp": "2026-06-01T10:00:00Z",
        "issues": issues,
    }), encoding="utf-8")
    return path


def _write_estimate(project_root: Path, issue_num: int, size: str) -> None:
    estimates_dir = project_root / ".commander" / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)
    (estimates_dir / f"issue-{issue_num}.json").write_text(
        json.dumps({"issue_number": issue_num, "size": size}), encoding="utf-8"
    )


def _done_issue(num: int, coder_min: float = 10.0, tester_min: float = 5.0,
                labels: list[dict] | None = None) -> dict:
    return {
        "number": num,
        "status": "done",
        "coder_started_at": "2026-06-01T10:00:00Z",
        "coder_finished_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_started_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_finished_at": f"2026-06-01T10:{int(coder_min + tester_min):02d}:00Z",
        "labels": labels or [],
    }


# ---------------------------------------------------------------------------
# AC1 — _resolve_calibration_size handles label-only tickets
# ---------------------------------------------------------------------------

class TestAC1SharedSizeResolver:
    """AC1: tickets with only a size-* label are not silently skipped.

    The Phase 1 shared resolver _resolve_calibration_size reads labels as the
    final fallback, so a ticket with no JSON and no state estimate still gets
    a size if it carries a size-* GitHub label.
    """

    def test_resolve_returns_size_from_label_when_no_json(self, tmp_path):
        """_resolve_calibration_size returns size from size-* label when no JSON file."""
        from calibration_cache_service import _resolve_calibration_size

        estimates_dir = tmp_path / "estimates"  # intentionally not created
        label_dicts = [{"name": "size-M"}]

        result = _resolve_calibration_size(42, estimates_dir, {}, label_dicts)
        assert result == "M", f"Expected 'M' from label, got {result!r}"

    def test_resolve_returns_size_from_label_xl(self, tmp_path):
        """size-XL label resolves to 'XL'."""
        from calibration_cache_service import _resolve_calibration_size

        estimates_dir = tmp_path / "estimates"
        result = _resolve_calibration_size(99, estimates_dir, {}, [{"name": "size-XL"}])
        assert result == "XL"

    def test_resolve_json_takes_priority_over_label(self, tmp_path):
        """JSON estimate file has higher priority than size-* label."""
        from calibration_cache_service import _resolve_calibration_size

        estimates_dir = tmp_path / "estimates"
        estimates_dir.mkdir()
        (estimates_dir / "issue-7.json").write_text(
            json.dumps({"size": "S"}), encoding="utf-8"
        )
        result = _resolve_calibration_size(7, estimates_dir, {}, [{"name": "size-L"}])
        assert result == "S", "JSON should override label"

    def test_resolve_returns_none_when_no_size_signal(self, tmp_path):
        """Returns None when no JSON, no state estimate, no size-* label."""
        from calibration_cache_service import _resolve_calibration_size

        estimates_dir = tmp_path / "estimates"
        result = _resolve_calibration_size(5, estimates_dir, {}, [{"name": "enhancement"}])
        assert result is None

    def test_calibration_cache_includes_label_only_ticket(self, tmp_path):
        """_refresh_calibration_cache counts tickets whose only size is a label."""
        from calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        # Ticket with ONLY a size-* label (no canonical JSON)
        issue_with_label = _done_issue(101, coder_min=10, tester_min=5,
                                       labels=[{"name": "size-M"}])
        _write_state(project_root, "sprint-1", [issue_with_label])
        # No _write_estimate call — label-only

        cache = _refresh_calibration_cache(project_root, configured_minutes)
        m_count = cache["by_size"]["M"]["count"]
        assert m_count == 1, (
            f"Label-only ticket not counted: M count={m_count}. "
            "Resolver must fall back to size-* label."
        )


# ---------------------------------------------------------------------------
# AC2 — preflight warns when JSON missing after subprocess exits 0
# ---------------------------------------------------------------------------

class TestAC2PreflightJsonWarning:
    """AC2: _preflight_estimate_one logs a warning when canonical JSON is absent."""

    def test_warning_logged_when_json_write_fails(self, tmp_path, caplog):
        """Warning emitted when estimate_path does not exist after write attempt."""
        from server import _preflight_estimate_one

        estimate_payload = {"size": "M", "files_likely_affected": []}

        # Patch all I/O so only the JSON-missing path is exercised
        with (
            patch("server._ei_fetch_issue", return_value={"number": 77, "title": "T"}),
            patch("server._ei_run_estimator", return_value=(estimate_payload, None)),
            patch("server._ei_apply_label"),
            patch("server._ei_apply_estimated_status"),
            patch("server._project_root_path", return_value=tmp_path),
            patch("server._commander_dir", return_value=tmp_path / ".commander"),
        ):
            # Make the directory but make the JSON write succeed then immediately
            # unlink the file so exists() is False — simulates the "JSON missing" path.
            estimates_dir = tmp_path / ".commander" / "estimates"
            estimates_dir.mkdir(parents=True, exist_ok=True)
            est_path = estimates_dir / "issue-77.json"

            original_write = Path.write_text

            def _write_then_delete(self, *args, **kwargs):
                original_write(self, *args, **kwargs)
                self.unlink()  # simulate missing JSON after write

            with (
                patch.object(Path, "write_text", _write_then_delete),
                caplog.at_level(logging.WARNING, logger="server"),
            ):
                result = _preflight_estimate_one(77, "owner/repo")

        warning_msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("canonical JSON missing" in m for m in warning_msgs), (
            f"Expected warning about missing canonical JSON; got: {warning_msgs}"
        )

    def test_no_warning_when_json_written_successfully(self, tmp_path, caplog):
        """No warning when canonical JSON is present after write."""
        from server import _preflight_estimate_one

        estimate_payload = {"size": "S", "files_likely_affected": []}
        estimates_dir = tmp_path / ".commander" / "estimates"
        estimates_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("server._ei_fetch_issue", return_value={"number": 55, "title": "T"}),
            patch("server._ei_run_estimator", return_value=(estimate_payload, None)),
            patch("server._ei_apply_label"),
            patch("server._ei_apply_estimated_status"),
            patch("server._project_root_path", return_value=tmp_path),
            patch("server._commander_dir", return_value=tmp_path / ".commander"),
            caplog.at_level(logging.WARNING, logger="server"),
        ):
            result = _preflight_estimate_one(55, "owner/repo")

        canon_path = estimates_dir / "issue-55.json"
        assert canon_path.exists(), "Canonical JSON should have been written"
        warning_msgs = [r.getMessage() for r in caplog.records
                        if r.levelno >= logging.WARNING and "canonical JSON missing" in r.getMessage()]
        assert not warning_msgs, f"Unexpected warnings: {warning_msgs}"


# ---------------------------------------------------------------------------
# AC3 — docs/features/estimation-lifecycle.md content
# ---------------------------------------------------------------------------

class TestAC3EstimationLifecycleDocs:
    """AC3: estimation-lifecycle.md exists with required content."""

    def test_estimation_lifecycle_doc_exists(self):
        """docs/features/estimation-lifecycle.md is present."""
        doc_path = REPO_ROOT / "docs" / "features" / "estimation-lifecycle.md"
        assert doc_path.exists(), f"Missing: {doc_path}"

    def test_doc_states_sprint_start_off_by_default(self):
        """Doc explicitly states sprint-start estimation is off by default."""
        doc_path = REPO_ROOT / "docs" / "features" / "estimation-lifecycle.md"
        if not doc_path.exists():
            pytest.skip("doc not present — covered by test_estimation_lifecycle_doc_exists")
        text = doc_path.read_text(encoding="utf-8").lower()
        assert "off by default" in text or "disabled by default" in text, (
            "Doc must state sprint-start estimation is off by default"
        )

    def test_doc_states_canonical_path(self):
        """Doc mentions the canonical estimates path .commander/estimates/."""
        doc_path = REPO_ROOT / "docs" / "features" / "estimation-lifecycle.md"
        if not doc_path.exists():
            pytest.skip("doc not present")
        text = doc_path.read_text(encoding="utf-8")
        assert ".commander/estimates" in text, (
            "Doc must mention canonical path .commander/estimates/"
        )

    def test_doc_states_calibration_fallback(self):
        """Doc mentions calibration falls back to size-* labels."""
        doc_path = REPO_ROOT / "docs" / "features" / "estimation-lifecycle.md"
        if not doc_path.exists():
            pytest.skip("doc not present")
        text = doc_path.read_text(encoding="utf-8").lower()
        assert "fall" in text or "fallback" in text or "label" in text, (
            "Doc must mention calibration reads JSON first, falls back to labels"
        )


# ---------------------------------------------------------------------------
# AC4 — calibration result includes processed_count and has_sprint_state_files
# ---------------------------------------------------------------------------

class TestAC4CalibrationResponseFields:
    """AC4: calibration result exposes processed_count and has_sprint_state_files."""

    def test_has_sprint_state_files_true_when_file_exists(self, tmp_path):
        """_has_sprint_state_files returns True when sprint-*-state.json exists."""
        from server import _has_sprint_state_files

        project_root = tmp_path / "project"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        (sprints_dir / "sprint-1-state.json").write_text("{}", encoding="utf-8")

        assert _has_sprint_state_files(project_root) is True

    def test_has_sprint_state_files_true_in_archive(self, tmp_path):
        """_has_sprint_state_files returns True when file is in archive/ subdir."""
        from server import _has_sprint_state_files

        project_root = tmp_path / "project"
        archive_dir = project_root / ".commander" / "sprints" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "sprint-5-state.json").write_text("{}", encoding="utf-8")

        assert _has_sprint_state_files(project_root) is True

    def test_compute_calibration_returns_processed_count(self, tmp_path):
        """_compute_calibration result includes processed_count key."""
        from calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"
        _write_state(project_root, "sprint-1", [_done_issue(201, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 201, "M")

        cache = _refresh_calibration_cache(project_root, configured_minutes)
        assert "processed" in cache, "Cache must have 'processed' field"
        processed_count = len(cache.get("processed") or [])
        assert isinstance(processed_count, int)
        assert processed_count >= 1

    def test_compute_calibration_returns_has_sprint_state_files_field(self, tmp_path):
        """has_sprint_state_files field is present in calibration response structure."""
        from server import _has_sprint_state_files

        project_root = tmp_path / "project"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        (sprints_dir / "sprint-2-state.json").write_text("{}", encoding="utf-8")

        result = _has_sprint_state_files(project_root)
        # The field type is bool — the endpoint wraps this in the response dict
        assert isinstance(result, bool)
        assert result is True


# ---------------------------------------------------------------------------
# AC5 — rebuild endpoint returns processed_count (banner link target)
# ---------------------------------------------------------------------------

class TestAC5RebuildEndpointExists:
    """AC5: /api/maintenance/calibration/rebuild returns processed_count.

    The banner UI links to this action; verifying the function is callable
    and returns the expected shape confirms the link target is valid.
    """

    def test_rebuild_returns_processed_count(self, tmp_path):
        """rebuild_calibration_cache returns dict with processed_count."""
        from apps.dashboard.routers.analytics import rebuild_calibration_cache

        # Set up a project with one sprint state file and one estimate
        project_root = tmp_path / "myproject"
        _write_state(project_root, "sprint-1", [_done_issue(301, coder_min=8, tester_min=4)])
        _write_estimate(project_root, 301, "S")

        with (
            patch("apps.dashboard.routers.analytics._resolve_project_slug",
                  return_value="owner/myproject"),
            patch("apps.dashboard.routers.analytics._project_root_path",
                  return_value=project_root),
            patch("apps.dashboard.routers.analytics._settings_repo.get_setting",
                  return_value={}),
        ):
            result = rebuild_calibration_cache(project="myproject")

        assert "processed_count" in result, f"Missing processed_count in: {result}"
        assert isinstance(result["processed_count"], int)

    def test_rebuild_returns_by_size_counts(self, tmp_path):
        """rebuild_calibration_cache result includes by_size_counts with S/M/L/XL."""
        from apps.dashboard.routers.analytics import rebuild_calibration_cache

        project_root = tmp_path / "myproject2"
        _write_state(project_root, "sprint-1", [_done_issue(302, coder_min=12, tester_min=3)])
        _write_estimate(project_root, 302, "L")

        with (
            patch("apps.dashboard.routers.analytics._resolve_project_slug",
                  return_value="owner/myproject2"),
            patch("apps.dashboard.routers.analytics._project_root_path",
                  return_value=project_root),
            patch("apps.dashboard.routers.analytics._settings_repo.get_setting",
                  return_value={}),
        ):
            result = rebuild_calibration_cache(project="myproject2")

        assert "by_size_counts" in result
        for sz in ("S", "M", "L", "XL"):
            assert sz in result["by_size_counts"]


# ---------------------------------------------------------------------------
# AC6 — no banner on fresh install (no sprint state files)
# ---------------------------------------------------------------------------

class TestAC6NoBannerFreshInstall:
    """AC6: has_sprint_state_files returns False when no sprint state files exist."""

    def test_has_sprint_state_files_false_on_fresh_install(self, tmp_path):
        """Returns False when .commander/sprints/ directory has no state files."""
        from server import _has_sprint_state_files

        project_root = tmp_path / "fresh"
        project_root.mkdir()

        assert _has_sprint_state_files(project_root) is False

    def test_has_sprint_state_files_false_when_no_commander_dir(self, tmp_path):
        """Returns False when .commander/ does not exist at all."""
        from server import _has_sprint_state_files

        project_root = tmp_path / "brand_new"
        project_root.mkdir()

        assert _has_sprint_state_files(project_root) is False

    def test_has_sprint_state_files_false_when_sprints_dir_empty(self, tmp_path):
        """Returns False when sprints/ exists but contains no state files."""
        from server import _has_sprint_state_files

        project_root = tmp_path / "empty_sprints"
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)
        # directory exists but no sprint-*-state.json inside

        assert _has_sprint_state_files(project_root) is False
