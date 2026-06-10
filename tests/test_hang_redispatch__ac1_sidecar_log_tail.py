"""Tests for #787 AC1: After M3 grace-kill, sidecar has failure_class=hang and log_tail field."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


def _make_tmproot(tmp_path: Path) -> Path:
    (tmp_path / ".commander" / "runtime").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_log(tmp_path: Path, lines: list[str]) -> Path:
    log_dir = tmp_path / ".commander" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "sprint-issue-42.log"
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def _sidecar(issue_num: int, repo_root: Path) -> Path:
    return repo_root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"


class TestAC1SidecarLogTail:
    def test_record_failure_accepts_log_tail_param(self):
        import inspect
        sig = inspect.signature(sm.record_failure)
        assert "log_tail" in sig.parameters, "record_failure must accept log_tail kwarg"

    def test_sidecar_has_log_tail_field_when_provided(self, tmp_path):
        repo_root = _make_tmproot(tmp_path)
        tail = ["line 1", "line 2", "last line"]
        sm.record_failure(
            42, "hang", "agent hung",
            repo_root=repo_root,
            log_tail=tail,
        )
        data = json.loads(_sidecar(42, repo_root).read_text())
        assert "log_tail" in data, "sidecar must contain log_tail field"
        assert data["log_tail"] == tail

    def test_hang_sidecar_written_when_hang_detected(self, tmp_path):
        """When HangDetector.killed=True, sidecar must have failure_class=hang."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")
        log_lines = [f"agent output line {i}" for i in range(10)]
        log_path = _make_log(tmp_path, log_lines)

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = tmp_path / ".commander" / "logs"

        class FakeProc:
            def wait(self): return -9
            returncode = -9

        fake_detector = MagicMock()
        fake_detector.killed = True
        sidecars = {}

        def capture_failure(issue_num, failure_class, detail, repo_root=None,
                            summary=None, files_to_inspect=None, log_tail=None):
            sidecars[failure_class] = {
                "failure_class": failure_class,
                "log_tail": log_tail,
            }
            return None

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure", side_effect=capture_failure),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_design_docs_guard", return_value=None),
            patch.object(sm, "_worktree_hygiene", return_value=("sha1", "sha2", None)),
            patch.object(sm, "_db_agent_start_sm"),
            patch.object(sm, "_db_update_worktree_shas_sm"),
        ):
            mock_sub.Popen.return_value = FakeProc()
            sm._dispatch_coder(issue_num=42, alert_modes=[], cfg=cfg_stub)

        assert "hang" in sidecars or "HANG" in sidecars, (
            "record_failure must be called with failure_class='hang' on hang-kill"
        )
        fc = "hang" if "hang" in sidecars else "HANG"
        assert sidecars[fc]["log_tail"] is not None, "log_tail must not be None for hang sidecar"

    def test_hang_sidecar_log_tail_is_list_of_strings(self, tmp_path):
        """log_tail must be a list of strings (last N lines of agent stdout/stderr)."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")
        log_lines = [f"output line {i}" for i in range(20)]
        logs_dir = tmp_path / ".commander" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = logs_dir

        class FakeProc:
            def wait(self): return -9
            returncode = -9

        fake_detector = MagicMock()
        fake_detector.killed = True
        captured = {}

        def capture_failure(issue_num, failure_class, detail, repo_root=None,
                            summary=None, files_to_inspect=None, log_tail=None):
            captured["log_tail"] = log_tail
            return None

        def fake_popen(cmd, stdout=None, stderr=subprocess.STDOUT, cwd=None, env=None, **kw):
            if stdout is not None:
                for line in log_lines:
                    stdout.write(line + "\n")
                stdout.flush()
            return FakeProc()

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure", side_effect=capture_failure),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_design_docs_guard", return_value=None),
            patch.object(sm, "_worktree_hygiene", return_value=("sha1", "sha2", None)),
            patch.object(sm, "_db_agent_start_sm"),
            patch.object(sm, "_db_update_worktree_shas_sm"),
        ):
            mock_sub.Popen.side_effect = fake_popen
            sm._dispatch_coder(issue_num=42, alert_modes=[], cfg=cfg_stub)

        tail = captured.get("log_tail")
        assert isinstance(tail, list), f"log_tail must be a list, got {type(tail)}"
        assert all(isinstance(line, str) for line in tail), "log_tail entries must be strings"
        assert len(tail) > 0, "log_tail must be non-empty when log file has content"

    def test_hang_sidecar_log_tail_contains_last_lines(self, tmp_path):
        """log_tail must contain the last lines of the log, not the first."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")
        log_lines = [f"early line {i}" for i in range(50)] + ["LAST_LINE_MARKER"]
        logs_dir = tmp_path / ".commander" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        cfg_stub = MagicMock()
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = None
        cfg_stub.api_url = None
        cfg_stub.coder_prompt_template = None
        cfg_stub.logs_dir = logs_dir

        class FakeProc:
            def wait(self): return -9
            returncode = -9

        fake_detector = MagicMock()
        fake_detector.killed = True
        captured = {}

        def capture_failure(issue_num, failure_class, detail, repo_root=None,
                            summary=None, files_to_inspect=None, log_tail=None):
            captured["log_tail"] = log_tail
            return None

        def fake_popen(cmd, stdout=None, stderr=subprocess.STDOUT, cwd=None, env=None, **kw):
            if stdout is not None:
                for line in log_lines:
                    stdout.write(line + "\n")
                stdout.flush()
            return FakeProc()

        with (
            patch.object(sm, "subprocess") as mock_sub,
            patch.object(sm, "HangDetector", return_value=fake_detector),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_add_blocked_label"),
            patch.object(sm, "dispatch_alerts"),
            patch.object(sm, "record_failure", side_effect=capture_failure),
            patch.object(sm, "_dispatch_doctor", return_value=None),
            patch.object(sm, "_design_docs_guard", return_value=None),
            patch.object(sm, "_worktree_hygiene", return_value=("sha1", "sha2", None)),
            patch.object(sm, "_db_agent_start_sm"),
            patch.object(sm, "_db_update_worktree_shas_sm"),
        ):
            mock_sub.Popen.side_effect = fake_popen
            sm._dispatch_coder(issue_num=42, alert_modes=[], cfg=cfg_stub)

        tail = captured.get("log_tail", [])
        assert "LAST_LINE_MARKER" in tail, (
            f"log_tail must contain the last line of the log; got tail: {tail[-5:]!r}"
        )

    def test_sidecar_log_tail_empty_when_no_log_file(self, tmp_path):
        """record_failure with log_tail=[] must store an empty list."""
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(99, "hang", "agent hung", repo_root=repo_root, log_tail=[])
        data = json.loads(_sidecar(99, repo_root).read_text())
        assert "log_tail" in data
        assert data["log_tail"] == []

    def test_sidecar_log_tail_absent_when_not_provided(self, tmp_path):
        """For non-hang failures (no log_tail provided), sidecar may omit or include empty log_tail."""
        repo_root = _make_tmproot(tmp_path)
        sm.record_failure(77, "crash", "exited with code 1", repo_root=repo_root)
        data = json.loads(_sidecar(77, repo_root).read_text())
        # log_tail field may be absent or empty list — both are valid
        if "log_tail" in data:
            assert data["log_tail"] == [] or data["log_tail"] is None
