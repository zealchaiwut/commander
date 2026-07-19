"""Tests for issue #1945: write commander_report.latest.json at sprint end for Hermes.

AC1: commander_report.latest.json written atomically to COMMANDER_REPORT_PATH on sprint end.
AC2: JSON payload includes all nine required top-level fields with correct types.
AC3: Atomic write (write-to-temp + rename); partial write never leaves a corrupt file.
AC4: Webhook payload and file payload produced by the same builder — structurally identical.
AC5: Missing parent directory logs a clear error and continues; no crash.
AC6: Unit tests cover full serialization, partial run (dead_letter), atomic write, missing directory.
AC7: COMMANDER_REPORT_PATH documented in .env.example.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _write_plan(sprints_dir: Path, label: str, **extra):
    data = {"state": "ready_to_merge", "started_at": "2026-01-01T00:00:00+00:00", **extra}
    (sprints_dir / f"{label}-plan.json").write_text(json.dumps(data))
    return data


def _write_state(sprints_dir: Path, label: str, issues: list, **extra):
    data = {"issues": issues, **extra}
    (sprints_dir / f"{label}-state.json").write_text(json.dumps(data))
    return data


# ── AC2 / AC6: Full payload serialization ────────────────────────────────────

class TestBuildCommanderReport:
    """AC2 + AC6: build_commander_report returns correct structure."""

    def test_all_nine_top_level_keys_present(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "Ticket A", "status": "done",
             "tokens_in": 100, "tokens_out": 200, "tester_attempt_count": 1},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
            started_at="2026-01-01T00:00:00+00:00",
        )
        required_keys = {"run_id", "trigger", "branch", "summary", "completed",
                         "needs_review", "dead_letter", "cost", "actions"}
        assert required_keys <= set(payload.keys()), (
            f"Missing keys: {required_keys - set(payload.keys())}"
        )

    def test_run_id_is_string(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert isinstance(payload["run_id"], str) and payload["run_id"]

    def test_trigger_shape(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        trigger = payload["trigger"]
        assert isinstance(trigger.get("by"), str)
        assert isinstance(trigger.get("confirmed_at"), str)
        assert isinstance(trigger.get("mode"), str)

    def test_branch_is_string(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert isinstance(payload["branch"], str)
        assert "sprint-10" in payload["branch"]

    def test_summary_shape_and_types(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed", "failure_reason": "timeout"},
            {"number": 3, "title": "C", "status": "skipped"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        s = payload["summary"]
        assert isinstance(s["attempted"], int)
        assert isinstance(s["completed"], int)
        assert isinstance(s["failed"], int)
        assert isinstance(s["skipped"], int)

    def test_summary_values_add_up(self, tmp_path):
        """AC3 (UAT step 3): summary.attempted == completed + failed + skipped."""
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed"},
            {"number": 3, "title": "C", "status": "skipped"},
            {"number": 4, "title": "D", "status": "pending"},  # not attempted
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        s = payload["summary"]
        assert s["attempted"] == s["completed"] + s["failed"] + s["skipped"], (
            f"attempted={s['attempted']} != completed={s['completed']} + "
            f"failed={s['failed']} + skipped={s['skipped']}"
        )

    def test_completed_ticket_shape(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 7, "title": "Completed ticket", "status": "done"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert len(payload["completed"]) == 1
        c = payload["completed"][0]
        assert "ticket_id" in c
        assert "title" in c
        assert isinstance(c["commits"], list)
        assert isinstance(c["tests"], list)
        assert "merged_to" in c
        assert "pr_url" in c

    def test_cost_shape_and_types(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [], total_tokens_in=1000, total_tokens_out=500)
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        cost = payload["cost"]
        assert isinstance(cost["tokens"], int)
        assert isinstance(cost["usd"], float)
        assert isinstance(cost["ceiling_hit"], bool)

    def test_actions_is_list(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert isinstance(payload["actions"], list)

    def test_actions_suggest_rerun_for_failed(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-10")
        _write_state(sprints_dir, "sprint-10", [
            {"number": 9, "title": "Broken ticket", "status": "failed"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-10", project="owner/repo"
        )
        assert len(payload["actions"]) == 1
        assert payload["actions"][0]["type"] == "rerun"
        assert payload["actions"][0]["ticket_id"] == "9"


# ── AC6: Partial run with dead_letter ────────────────────────────────────────

class TestPartialRunDeadLetter:
    """AC6: partial-run scenario — some tickets in dead_letter."""

    def test_failed_tickets_in_dead_letter(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-20")
        _write_state(sprints_dir, "sprint-20", [
            {"number": 10, "title": "Passes", "status": "done"},
            {"number": 11, "title": "Fails", "status": "failed",
             "failure_reason": "test suite error", "tester_attempt_count": 3},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-20", project="owner/repo"
        )
        assert len(payload["completed"]) == 1
        assert len(payload["dead_letter"]) == 1
        dl = payload["dead_letter"][0]
        assert dl["ticket_id"] == "11"
        assert dl["title"] == "Fails"
        assert dl["attempts"] == 3
        assert dl["last_error"] == "test suite error"

    def test_dead_letter_shape(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-20")
        _write_state(sprints_dir, "sprint-20", [
            {"number": 5, "title": "Bad ticket", "status": "error"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-20", project="owner/repo"
        )
        assert len(payload["dead_letter"]) == 1
        dl = payload["dead_letter"][0]
        assert {"ticket_id", "title", "attempts", "last_error"} <= set(dl.keys())
        assert isinstance(dl["attempts"], int)
        assert isinstance(dl["last_error"], str)

    def test_summary_counts_in_partial_run(self, tmp_path):
        from routers.sprint_webhook_service import build_commander_report
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-20")
        _write_state(sprints_dir, "sprint-20", [
            {"number": 1, "title": "A", "status": "done"},
            {"number": 2, "title": "B", "status": "failed"},
            {"number": 3, "title": "C", "status": "skipped"},
        ])
        payload = build_commander_report(
            sprints_dir=sprints_dir, sprint_label="sprint-20", project="owner/repo"
        )
        assert payload["summary"]["completed"] == 1
        assert payload["summary"]["failed"] == 1
        assert payload["summary"]["skipped"] == 1
        assert payload["summary"]["attempted"] == 3


# ── AC3 / AC6: Atomic write behavior ─────────────────────────────────────────

class TestWriteCommanderReport:
    """AC3 + AC6: atomic write (temp file cleaned up on failure or success)."""

    def test_successful_write_creates_file(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "commander_report.latest.json"
        payload = {"run_id": "test-run", "summary": {"completed": 1}}
        write_commander_report(payload, report_path)
        assert report_path.exists()
        written = json.loads(report_path.read_text())
        assert written["run_id"] == "test-run"

    def test_temp_file_removed_after_successful_write(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "commander_report.latest.json"
        payload = {"run_id": "test-run"}
        write_commander_report(payload, report_path)
        tmp_file = report_path.with_suffix(".tmp")
        assert not tmp_file.exists(), "Temp file should be removed after atomic rename"

    def test_temp_file_cleaned_on_rename_failure(self, tmp_path):
        """AC6 + AC3: if rename fails, the .tmp file is cleaned up."""
        from routers import sprint_webhook_service
        report_path = tmp_path / "commander_report.latest.json"
        tmp_file = report_path.with_suffix(".tmp")
        payload = {"run_id": "test-run"}

        original_rename = Path.rename

        def _fail_rename(self, target):
            raise OSError("simulated rename failure")

        with patch.object(Path, "rename", _fail_rename):
            sprint_webhook_service.write_commander_report(payload, report_path)

        # The .tmp file should be cleaned up
        assert not tmp_file.exists(), "Temp file must be removed after a failed rename"
        # The destination file should not exist (write failed)
        assert not report_path.exists()

    def test_write_produces_valid_json(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "report.json"
        payload = {"run_id": "r1", "cost": {"tokens": 500, "usd": 0.0, "ceiling_hit": False}}
        write_commander_report(payload, report_path)
        content = report_path.read_text()
        parsed = json.loads(content)
        assert parsed["cost"]["tokens"] == 500

    def test_successive_writes_overwrite_atomically(self, tmp_path):
        """AC6 (UAT step 6): second run overwrites first without intermediate corruption."""
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "commander_report.latest.json"
        write_commander_report({"run_id": "run-1"}, report_path)
        write_commander_report({"run_id": "run-2"}, report_path)
        content = json.loads(report_path.read_text())
        assert content["run_id"] == "run-2"


# ── AC5 / AC6: Missing parent directory ──────────────────────────────────────

class TestMissingOutputDirectory:
    """AC5 + AC6: missing parent directory logs error and continues."""

    def test_missing_parent_does_not_raise(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "nonexistent_dir" / "commander_report.latest.json"
        payload = {"run_id": "test"}
        # Must not raise
        write_commander_report(payload, report_path)

    def test_missing_parent_logs_error(self, tmp_path, caplog):
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "nonexistent_dir" / "commander_report.latest.json"
        payload = {"run_id": "test"}
        with caplog.at_level(logging.ERROR, logger="routers.sprint_webhook_service"):
            write_commander_report(payload, report_path)
        assert any("nonexistent_dir" in r.message or "does not exist" in r.message
                   for r in caplog.records), (
            "Expected a logged error mentioning the missing directory"
        )

    def test_missing_parent_does_not_create_file(self, tmp_path):
        from routers.sprint_webhook_service import write_commander_report
        report_path = tmp_path / "nonexistent_dir" / "commander_report.latest.json"
        payload = {"run_id": "test"}
        write_commander_report(payload, report_path)
        assert not report_path.exists()
        assert not report_path.with_suffix(".tmp").exists()


# ── AC4: Webhook payload == file payload (shared builder) ────────────────────

class TestWebhookFilePayloadIdentical:
    """AC4: build_webhook_payload and build_commander_report return identical structure."""

    def test_webhook_payload_matches_commander_report(self, tmp_path):
        from routers.sprint_webhook_service import (
            build_commander_report,
            build_webhook_payload,
        )
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-30")
        _write_state(sprints_dir, "sprint-30", [
            {"number": 1, "title": "Done", "status": "done"},
            {"number": 2, "title": "Fail", "status": "failed"},
        ])
        kwargs = dict(
            sprints_dir=sprints_dir,
            sprint_label="sprint-30",
            project="owner/repo",
            started_at="2026-01-01T00:00:00+00:00",
        )
        file_payload = build_commander_report(**kwargs)
        webhook_payload = build_webhook_payload(**kwargs)
        assert file_payload == webhook_payload, (
            "Webhook and file payloads must be structurally identical (shared builder)"
        )

    def test_both_payloads_have_same_top_level_keys(self, tmp_path):
        from routers.sprint_webhook_service import (
            build_commander_report,
            build_webhook_payload,
        )
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-31")
        _write_state(sprints_dir, "sprint-31", [])
        kwargs = dict(sprints_dir=sprints_dir, sprint_label="sprint-31", project="owner/repo")
        assert set(build_commander_report(**kwargs).keys()) == set(build_webhook_payload(**kwargs).keys())


# ── AC1 / AC6: start_report_monitor writes file on proc exit ─────────────────

class TestStartReportMonitor:
    """AC1 + AC6: start_report_monitor always starts and writes the report file."""

    def test_monitor_writes_report_on_proc_exit(self, tmp_path, monkeypatch):
        from routers import sprint_webhook_service
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-50")
        _write_state(sprints_dir, "sprint-50", [
            {"number": 1, "title": "T1", "status": "done"},
        ])
        report_path = tmp_path / "commander_report.latest.json"
        monkeypatch.setenv("COMMANDER_REPORT_PATH", str(report_path))

        proc_mock = MagicMock()
        proc_mock.wait = MagicMock(return_value=0)

        t = sprint_webhook_service.start_report_monitor(
            proc=proc_mock,
            sprint_label="sprint-50",
            project="owner/repo",
            sprints_dir=sprints_dir,
            started_at="2026-01-01T00:00:00+00:00",
        )
        # Wait for thread to complete
        deadline = time.time() + 5
        while not report_path.exists() and time.time() < deadline:
            time.sleep(0.05)

        assert report_path.exists(), "Report file must be written after proc exits"
        payload = json.loads(report_path.read_text())
        assert "run_id" in payload
        assert payload["summary"]["completed"] == 1

    def test_monitor_always_starts_thread(self, tmp_path, monkeypatch):
        """start_report_monitor always returns a Thread (unlike start_callback_monitor)."""
        from routers import sprint_webhook_service
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        _write_plan(sprints_dir, "sprint-51")
        _write_state(sprints_dir, "sprint-51", [])
        report_path = tmp_path / "never.json"
        monkeypatch.setenv("COMMANDER_REPORT_PATH", str(report_path))

        proc_mock = MagicMock()
        proc_mock.wait = MagicMock(return_value=0)

        result = sprint_webhook_service.start_report_monitor(
            proc=proc_mock,
            sprint_label="sprint-51",
            project="owner/repo",
            sprints_dir=sprints_dir,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert result is not None, "start_report_monitor must always return a Thread"
        assert isinstance(result, threading.Thread)


# ── AC7: .env.example documents COMMANDER_REPORT_PATH ────────────────────────

class TestEnvExample:
    """AC7: COMMANDER_REPORT_PATH is documented in .env.example."""

    def test_commander_report_path_in_env_example(self):
        env_example = REPO_ROOT / ".env.example"
        assert env_example.exists(), ".env.example must exist"
        content = env_example.read_text()
        assert "COMMANDER_REPORT_PATH" in content, (
            "COMMANDER_REPORT_PATH must be documented in .env.example"
        )
