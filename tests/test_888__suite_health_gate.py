"""Tests for #888: full-suite health gate for sprint summaries.

AC items verified:
  AC-1  write_sprint_summary triggers health gate automatically (run_health_gate=True)
  AC-2  Stats table contains Suite Health row with pass/fail/skip/duration
  AC-3  Health metrics appended to structured log in machine-readable JSON
  AC-4  SUITE FAILING banner present in summary when tests fail
  AC-5  SUITE TIMEOUT banner present when run exceeds timeout threshold
  AC-6  SUITE HEALTH NOT RECORDED banner when gate has never run
  AC-7  Prior sprint summaries with no health JSON show NOT RECORDED retroactively
  AC-8  CLI entry point exists and is importable with correct argparse args
  AC-9  Passing suite produces no warning banner; Stats row shows ✅
"""
from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()

import sprint_manager as sm
from sprint_manager import SprintState, IssueState, generate_sprint_summary, write_sprint_summary

from services.sprint_manager import suite_health_gate as shg
from services.sprint_manager.suite_health_gate import (
    SuiteHealthResult,
    run_gate,
    load_gate_result,
    _health_json_path,
    SUITE_HEALTH_TIMEOUT_DEFAULT,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state(
    sprint_label: str = "sprint-88",
    sprint_number: int = 88,
    done: int = 2,
) -> SprintState:
    s = SprintState(sprint_label=sprint_label, sprint_number=sprint_number)
    s.issues = [
        IssueState(number=i + 100, title=f"Issue {i}", status="done")
        for i in range(done)
    ]
    return s


def _make_health(
    sprint_label: str = "sprint-88",
    passed: int = 10,
    failed: int = 0,
    skipped: int = 1,
    duration_seconds: float = 4.5,
    timed_out: bool = False,
) -> SuiteHealthResult:
    return SuiteHealthResult(
        sprint_label=sprint_label,
        passed=passed,
        failed=failed,
        skipped=skipped,
        duration_seconds=duration_seconds,
        timed_out=timed_out,
        recorded_at="2026-06-14T12:00:00+00:00",
    )


def _write_health_json(sprints_dir: Path, result: SuiteHealthResult) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    path = _health_json_path(result.sprint_label, sprints_dir)
    path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")


# ── AC-1: auto-trigger via write_sprint_summary ───────────────────────────────

class TestAC1AutoTrigger:
    """AC-1: health gate runs automatically when write_sprint_summary is called."""

    def test_run_gate_called_when_flag_set(self, tmp_path, monkeypatch):
        """When run_health_gate=True, run_gate is called for the sprint."""
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)

        run_gate_calls = []

        def fake_run_gate(sprint_label, sprints_dir, **kw):
            run_gate_calls.append(sprint_label)
            return _make_health(sprint_label=sprint_label)

        monkeypatch.setattr(shg, "run_gate", fake_run_gate)
        monkeypatch.setattr(sm, "_suite_health_gate", shg)

        with patch.object(sm, "create_summary_github_issue", return_value=(1, "http://gh/1")):
            write_sprint_summary(
                _make_state(),
                elapsed_secs=60.0,
                end_reason="complete",
                repo_name="test/repo",
                run_health_gate=True,
            )

        assert "sprint-88" in run_gate_calls, "run_gate must be called for the sprint"

    def test_run_gate_not_called_when_flag_false(self, tmp_path, monkeypatch):
        """When run_health_gate=False (default), gate is not auto-run."""
        monkeypatch.setattr(sm, "SPRINTS_DIR", tmp_path)

        run_gate_calls = []

        def fake_run_gate(sprint_label, sprints_dir, **kw):
            run_gate_calls.append(sprint_label)
            return _make_health(sprint_label=sprint_label)

        monkeypatch.setattr(shg, "run_gate", fake_run_gate)
        monkeypatch.setattr(sm, "_suite_health_gate", shg)

        with patch.object(sm, "create_summary_github_issue", return_value=(1, "http://gh/1")):
            write_sprint_summary(
                _make_state(),
                elapsed_secs=60.0,
                end_reason="complete",
                repo_name="test/repo",
            )

        assert run_gate_calls == [], "run_gate must NOT be called when run_health_gate=False"


# ── AC-2: Stats table Suite Health row ───────────────────────────────────────

class TestAC2StatsRow:
    """AC-2: pass/fail/skip/duration appear in the Stats table."""

    def test_stats_row_present_when_passing(self, tmp_path):
        health = _make_health(passed=12, failed=0, skipped=1, duration_seconds=7.3)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "Suite Health" in summary, "Stats table must have a Suite Health row"
        assert "12" in summary, "pass count (12) must appear in summary"
        assert "0" in summary, "fail count (0) must appear in summary"
        assert "1" in summary, "skip count (1) must appear in summary"
        assert "7.3" in summary or "7" in summary, "duration must appear in summary"

    def test_stats_row_pass_fail_skip_all_labelled(self, tmp_path):
        health = _make_health(passed=5, failed=3, skipped=2)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "Suite Health" in summary
        # All three counts appear (order-independent)
        assert "5" in summary and "3" in summary and "2" in summary


# ── AC-3: Structured log machine-readable block ───────────────────────────────

class TestAC3StructuredLog:
    """AC-3: health metrics appended to structured log in machine-readable format."""

    def test_run_gate_appends_to_log(self, tmp_path, monkeypatch):
        """run_gate() writes a JSON-compatible block to the structured log."""
        pytest_output = "10 passed, 0 failed, 1 skipped in 4.50s"
        monkeypatch.setenv("COMMANDER_LOG_LEVEL", "DEBUG")

        logged_events = []

        def fake_event(name, **kwargs):
            logged_events.append({"name": name, **kwargs})

        with patch("services.sprint_manager.suite_health_gate._append_to_log") as mock_log:
            result = run_gate(
                sprint_label="sprint-88",
                sprints_dir=tmp_path,
                repo_root=REPO_ROOT,
                timeout_seconds=SUITE_HEALTH_TIMEOUT_DEFAULT,
                _subprocess_result=subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=pytest_output,
                    stderr="",
                ),
            )
        mock_log.assert_called_once()
        call_result = mock_log.call_args[0][1]  # second positional arg
        assert call_result.passed == 10
        assert call_result.failed == 0
        assert call_result.skipped == 1

    def test_health_result_serialisable_as_json(self, tmp_path):
        """to_dict() produces a dict with machine-readable fields."""
        health = _make_health(passed=5, failed=2, skipped=0, duration_seconds=3.14)
        d = health.to_dict()
        # Must be JSON-serialisable
        text = json.dumps(d)
        parsed = json.loads(text)
        assert parsed["pass"] == 5
        assert parsed["fail"] == 2
        assert parsed["skip"] == 0
        assert parsed["duration_seconds"] == 3.14
        assert "status" in parsed

    def test_log_block_contains_required_fields(self, tmp_path):
        """JSON block from to_dict() has pass, fail, skip, duration_seconds."""
        health = _make_health(passed=8, failed=1, skipped=0, duration_seconds=9.99)
        d = health.to_dict()
        for key in ("pass", "fail", "skip", "duration_seconds"):
            assert key in d, f"missing key: {key}"


# ── AC-4: SUITE FAILING banner ────────────────────────────────────────────────

class TestAC4FailingBanner:
    """AC-4: SUITE FAILING warning banner when tests fail."""

    def test_failing_banner_present_when_failed_gt_0(self, tmp_path):
        health = _make_health(passed=5, failed=2, skipped=0)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "⚠ SUITE FAILING" in summary, "SUITE FAILING banner must appear when failed > 0"

    def test_failing_banner_at_top(self, tmp_path):
        health = _make_health(passed=0, failed=3, skipped=0)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        banner_pos = summary.find("⚠ SUITE FAILING")
        header_pos = summary.find("## Sprint")
        assert banner_pos < header_pos, "SUITE FAILING banner must appear before ## Sprint header"

    def test_failing_banner_absent_when_no_failures(self, tmp_path):
        health = _make_health(passed=10, failed=0, skipped=0)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "⚠ SUITE FAILING" not in summary


# ── AC-5: SUITE TIMEOUT banner ────────────────────────────────────────────────

class TestAC5TimeoutBanner:
    """AC-5: SUITE TIMEOUT banner when run exceeds threshold."""

    def test_timeout_banner_when_timed_out(self, tmp_path):
        health = _make_health(timed_out=True)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "⚠ SUITE TIMEOUT" in summary

    def test_timeout_banner_at_top(self, tmp_path):
        health = _make_health(timed_out=True)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        banner_pos = summary.find("⚠ SUITE TIMEOUT")
        header_pos = summary.find("## Sprint")
        assert banner_pos < header_pos, "SUITE TIMEOUT banner must appear before ## Sprint header"

    def test_run_gate_records_timed_out_when_subprocess_times_out(self, tmp_path):
        """run_gate sets timed_out=True when _subprocess_result is TimeoutExpired."""
        result = run_gate(
            sprint_label="sprint-88",
            sprints_dir=tmp_path,
            repo_root=REPO_ROOT,
            timeout_seconds=1,
            _subprocess_result=subprocess.TimeoutExpired(cmd=[], timeout=1),
        )
        assert result.timed_out is True
        assert result.status == "timeout"

    def test_timeout_configurable(self):
        """SUITE_HEALTH_TIMEOUT_DEFAULT is an int and can be overridden."""
        assert isinstance(SUITE_HEALTH_TIMEOUT_DEFAULT, int)
        assert SUITE_HEALTH_TIMEOUT_DEFAULT > 0


# ── AC-6: NOT RECORDED banner ─────────────────────────────────────────────────

class TestAC6NotRecordedBanner:
    """AC-6: SUITE HEALTH NOT RECORDED banner when gate has never run."""

    def test_not_recorded_banner_when_no_json(self, tmp_path):
        """With no health JSON, banner must appear in summary."""
        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "⚠ SUITE HEALTH NOT RECORDED" in summary

    def test_not_recorded_banner_at_top(self, tmp_path):
        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        banner_pos = summary.find("⚠ SUITE HEALTH NOT RECORDED")
        header_pos = summary.find("## Sprint")
        assert banner_pos >= 0, "NOT RECORDED banner must be present"
        assert banner_pos < header_pos, "banner must appear before ## Sprint header"

    def test_not_recorded_banner_absent_when_health_data_exists(self, tmp_path):
        health = _make_health(passed=3, failed=0, skipped=0)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "⚠ SUITE HEALTH NOT RECORDED" not in summary


# ── AC-7: Retroactive NOT RECORDED for old summaries ─────────────────────────

class TestAC7Retroactive:
    """AC-7: summaries with no health JSON show NOT RECORDED banner at render time."""

    def test_no_health_json_produces_not_recorded(self, tmp_path):
        """Render a summary for a sprint with no health data (simulates old sprint)."""
        state = _make_state(sprint_label="sprint-01", sprint_number=1)
        summary = generate_sprint_summary(state, elapsed_secs=120.0, sprints_dir=tmp_path)
        assert "⚠ SUITE HEALTH NOT RECORDED" in summary

    def test_not_recorded_shown_even_when_sprints_dir_none(self):
        """When sprints_dir is None, no health data → NOT RECORDED banner."""
        state = _make_state(sprint_label="sprint-02", sprint_number=2)
        summary = generate_sprint_summary(state, elapsed_secs=120.0, sprints_dir=None)
        assert "⚠ SUITE HEALTH NOT RECORDED" in summary


# ── AC-8: CLI entry point ─────────────────────────────────────────────────────

class TestAC8CLI:
    """AC-8: CLI command to trigger health gate manually."""

    def test_cli_script_exists(self):
        """scripts/run_suite_health_gate.py must exist."""
        script = REPO_ROOT / "scripts" / "run_suite_health_gate.py"
        assert script.exists(), "scripts/run_suite_health_gate.py must exist"

    def test_cli_script_has_main(self):
        """CLI script must define a main() function."""
        script = REPO_ROOT / "scripts" / "run_suite_health_gate.py"
        source = script.read_text(encoding="utf-8")
        assert "def main(" in source or "def main():" in source

    def test_cli_script_has_sprint_label_arg(self):
        """CLI script must accept --sprint-label argument."""
        script = REPO_ROOT / "scripts" / "run_suite_health_gate.py"
        source = script.read_text(encoding="utf-8")
        assert "sprint-label" in source or "sprint_label" in source

    def test_cli_script_importable(self):
        """CLI script must be importable without errors."""
        import importlib.util
        script = REPO_ROOT / "scripts" / "run_suite_health_gate.py"
        spec = importlib.util.spec_from_file_location("run_suite_health_gate", script)
        mod = importlib.util.module_from_spec(spec)
        # Should not raise on import
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")


# ── AC-9: Passing suite — no banner, ✅ in stats ─────────────────────────────

class TestAC9PassingNoWarning:
    """AC-9: passing suite shows no warning banner; Stats row shows ✅."""

    def test_no_banner_when_all_pass(self, tmp_path):
        health = _make_health(passed=15, failed=0, skipped=0, timed_out=False)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "⚠ SUITE FAILING" not in summary
        assert "⚠ SUITE TIMEOUT" not in summary
        assert "⚠ SUITE HEALTH NOT RECORDED" not in summary

    def test_stats_row_shows_checkmark_when_passing(self, tmp_path):
        health = _make_health(passed=8, failed=0, skipped=2, timed_out=False)
        _write_health_json(tmp_path, health)

        summary = generate_sprint_summary(
            _make_state(), elapsed_secs=60.0, sprints_dir=tmp_path
        )

        assert "✅" in summary, "Stats table must show ✅ when suite passes"
        assert "Suite Health" in summary


# ── Unit tests for suite_health_gate module ───────────────────────────────────

class TestSuiteHealthResultDataclass:
    """Unit tests for SuiteHealthResult."""

    def test_status_passing(self):
        r = _make_health(passed=5, failed=0, timed_out=False)
        assert r.status == "passing"

    def test_status_failing(self):
        r = _make_health(passed=5, failed=1, timed_out=False)
        assert r.status == "failing"

    def test_status_timeout(self):
        r = _make_health(passed=0, failed=0, timed_out=True)
        assert r.status == "timeout"

    def test_to_dict_keys(self):
        r = _make_health()
        d = r.to_dict()
        for key in ("pass", "fail", "skip", "duration_seconds", "status", "sprint_label"):
            assert key in d, f"missing key: {key}"

    def test_from_dict_roundtrip(self):
        r = _make_health(passed=7, failed=2, skipped=1, duration_seconds=12.5)
        d = r.to_dict()
        r2 = SuiteHealthResult.from_dict(d)
        assert r2.passed == 7
        assert r2.failed == 2
        assert r2.skipped == 1
        assert r2.duration_seconds == 12.5


class TestLoadGateResult:
    """load_gate_result returns None when file missing, SuiteHealthResult when present."""

    def test_returns_none_when_no_file(self, tmp_path):
        result = load_gate_result("sprint-99", tmp_path)
        assert result is None

    def test_returns_result_when_file_exists(self, tmp_path):
        health = _make_health(sprint_label="sprint-99", passed=6, failed=1)
        _write_health_json(tmp_path, health)

        result = load_gate_result("sprint-99", tmp_path)
        assert result is not None
        assert result.passed == 6
        assert result.failed == 1

    def test_returns_none_on_corrupt_json(self, tmp_path):
        path = _health_json_path("sprint-99", tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json", encoding="utf-8")

        result = load_gate_result("sprint-99", tmp_path)
        assert result is None


class TestRunGateParsesPytestOutput:
    """run_gate correctly parses pytest -q output."""

    def _run_with_output(self, output: str, tmp_path: Path, returncode: int = 0) -> SuiteHealthResult:
        return run_gate(
            sprint_label="sprint-88",
            sprints_dir=tmp_path,
            repo_root=REPO_ROOT,
            timeout_seconds=SUITE_HEALTH_TIMEOUT_DEFAULT,
            _subprocess_result=subprocess.CompletedProcess(
                args=[],
                returncode=returncode,
                stdout=output,
                stderr="",
            ),
        )

    def test_parses_passed_count(self, tmp_path):
        r = self._run_with_output("5 passed in 2.00s", tmp_path)
        assert r.passed == 5

    def test_parses_failed_count(self, tmp_path):
        r = self._run_with_output("3 passed, 2 failed in 1.00s", tmp_path, returncode=1)
        assert r.failed == 2

    def test_parses_skipped_count(self, tmp_path):
        r = self._run_with_output("4 passed, 1 skipped in 3.00s", tmp_path)
        assert r.skipped == 1

    def test_saves_json_to_sprints_dir(self, tmp_path):
        self._run_with_output("10 passed in 5.00s", tmp_path)
        path = _health_json_path("sprint-88", tmp_path)
        assert path.exists(), "health JSON must be saved after run_gate()"

    def test_timed_out_false_on_normal_run(self, tmp_path):
        r = self._run_with_output("8 passed in 2.50s", tmp_path)
        assert r.timed_out is False
