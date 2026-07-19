"""Tests for issue #1951: write_commander_report must use a unique temp file per call.

The fix replaces the shared fixed temp path (path.with_suffix('.tmp')) with a
per-call unique file from tempfile.mkstemp(dir=path.parent), then os.replace.
This prevents concurrent writers from corrupting each other's temp file.

AC1: Each write_commander_report call creates a unique temp file, not a shared fixed path.
AC2: Concurrent writes for the same destination both finish; final file is valid JSON.
AC3: Failed os.replace cleans up the unique temp file; destination is not written.
AC4: No .tmp files remain in the directory after a successful write.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


class TestUniqueTemPath:
    """AC1: each call uses a unique temp path, not path.with_suffix('.tmp')."""

    def test_fixed_tmp_path_never_created(self, tmp_path):
        """The fixed shared path (path.with_suffix('.tmp')) must never appear on disk."""
        from routers.sprint_webhook_service import write_commander_report

        report_path = tmp_path / "commander_report.latest.json"
        fixed_tmp = report_path.with_suffix(".tmp")

        created_paths = []
        real_mkstemp = __import__("tempfile").mkstemp

        def capturing_mkstemp(**kwargs):
            fd, path = real_mkstemp(**kwargs)
            created_paths.append(path)
            return fd, path

        with patch("tempfile.mkstemp", side_effect=capturing_mkstemp):
            write_commander_report({"run_id": "r1"}, report_path)

        assert not fixed_tmp.exists(), (
            "Fixed shared temp path must never be created; use tempfile.mkstemp"
        )
        # The temp path actually created must differ from the fixed shared path
        assert all(p != str(fixed_tmp) for p in created_paths), (
            "tempfile.mkstemp must not return the fixed .tmp path"
        )

    def test_two_calls_use_different_temp_paths(self, tmp_path):
        """Two concurrent calls must create two distinct temp files."""
        from routers.sprint_webhook_service import write_commander_report

        report_path = tmp_path / "report.json"
        real_mkstemp = __import__("tempfile").mkstemp
        created_paths = []

        def capturing_mkstemp(**kwargs):
            fd, path = real_mkstemp(**kwargs)
            created_paths.append(path)
            return fd, path

        with patch("tempfile.mkstemp", side_effect=capturing_mkstemp):
            write_commander_report({"run_id": "r1"}, report_path)
            write_commander_report({"run_id": "r2"}, report_path)

        assert len(created_paths) == 2, "Expected exactly two mkstemp calls"
        assert created_paths[0] != created_paths[1], (
            "Each write call must use a distinct temp path"
        )


class TestConcurrentWrites:
    """AC2: concurrent writes for the same destination never corrupt the output."""

    def test_concurrent_writes_produce_valid_json(self, tmp_path):
        """Two threads writing concurrently must leave a valid JSON file."""
        from routers.sprint_webhook_service import write_commander_report

        report_path = tmp_path / "commander_report.latest.json"
        errors = []

        def writer(run_id: str):
            try:
                write_commander_report({"run_id": run_id, "data": "x" * 4096}, report_path)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=writer, args=("concurrent-1",))
        t2 = threading.Thread(target=writer, args=("concurrent-2",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Concurrent write raised: {errors}"
        assert report_path.exists(), "Report file must exist after concurrent writes"
        content = report_path.read_text(encoding="utf-8")
        parsed = json.loads(content)  # must not raise — file must be valid JSON
        assert parsed["run_id"] in ("concurrent-1", "concurrent-2")

    def test_concurrent_writes_leave_no_temp_files(self, tmp_path):
        """No temp files should remain in the directory after concurrent writes finish."""
        from routers.sprint_webhook_service import write_commander_report

        report_path = tmp_path / "commander_report.latest.json"

        def writer(run_id: str):
            write_commander_report({"run_id": run_id}, report_path)

        threads = [threading.Thread(target=writer, args=(f"run-{i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        leftover_tmps = glob.glob(str(tmp_path / "*.tmp"))
        assert leftover_tmps == [], (
            f"Temp files must be cleaned up after write; found: {leftover_tmps}"
        )


class TestOsReplace:
    """AC3: failed os.replace cleans up the unique temp file; destination not written."""

    def test_failed_replace_cleans_unique_temp(self, tmp_path):
        """If os.replace fails, the unique temp file must be removed."""
        from routers import sprint_webhook_service

        report_path = tmp_path / "commander_report.latest.json"

        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("simulated replace failure")

        with patch("os.replace", side_effect=failing_replace):
            sprint_webhook_service.write_commander_report({"run_id": "fail"}, report_path)

        leftover_tmps = glob.glob(str(tmp_path / "*.tmp"))
        assert leftover_tmps == [], (
            f"Unique temp file must be cleaned up after failed os.replace; found: {leftover_tmps}"
        )

    def test_failed_replace_does_not_write_destination(self, tmp_path):
        """If os.replace fails, the destination file must not be left in a partial state."""
        from routers import sprint_webhook_service

        report_path = tmp_path / "commander_report.latest.json"

        def failing_replace(src, dst):
            raise OSError("simulated replace failure")

        with patch("os.replace", side_effect=failing_replace):
            sprint_webhook_service.write_commander_report({"run_id": "fail"}, report_path)

        assert not report_path.exists(), (
            "Destination must not exist if os.replace failed"
        )


class TestNoTempFilesAfterSuccess:
    """AC4: no temp files remain after a successful write."""

    def test_no_tmp_files_after_single_write(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report

        report_path = tmp_path / "report.json"
        write_commander_report({"run_id": "clean"}, report_path)

        leftover_tmps = glob.glob(str(tmp_path / "*.tmp"))
        assert leftover_tmps == [], (
            f"No .tmp files should remain after successful write; found: {leftover_tmps}"
        )

    def test_final_file_is_valid_json(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report

        report_path = tmp_path / "report.json"
        write_commander_report({"run_id": "ok", "value": 42}, report_path)

        parsed = json.loads(report_path.read_text(encoding="utf-8"))
        assert parsed["run_id"] == "ok"
        assert parsed["value"] == 42
