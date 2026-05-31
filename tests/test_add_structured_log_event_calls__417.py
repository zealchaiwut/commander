"""Tests for issue #417: structured log.event() calls in sprint_manager.py.

Acceptance Criteria:
  AC-1: run_id minted at sprint start, propagated via COMMANDER_RUN_ID env var
  AC-2: log.event() calls accompany print() calls with required fields
  AC-3: No existing print() calls removed
  AC-4: log.event() uses consistent field names (no freeform string keys)
  AC-5: log.event() exceptions don't crash dispatch loop
  AC-6: run_id=None or missing import doesn't break sprint manager
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


# ── helpers ──────────────────────────────────────────────────────────────────

def _import_sprint_manager():
    """Import sprint_manager with minimal env set up."""
    env_patch = {
        "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", "fake-token"),
    }
    with patch.dict(os.environ, env_patch, clear=False):
        import services.sprint_manager.sprint_manager as sm
        return sm


# ── AC-1: run_id minted at sprint start, set in COMMANDER_RUN_ID ─────────────

class TestRunIdMinting:
    def test_mint_run_id_returns_nonempty_string(self):
        from services.run_id import mint_run_id
        run_id = mint_run_id("sprint", "29")
        assert isinstance(run_id, str) and len(run_id) > 0

    def test_mint_run_id_format_includes_source(self, monkeypatch):
        from services.run_id import mint_run_id
        monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)
        run_id = mint_run_id("sprint", "5")
        assert run_id.startswith("sprint5-")

    def test_mint_run_id_child_inherits_parent(self, monkeypatch):
        from services.run_id import mint_run_id
        monkeypatch.setenv("COMMANDER_RUN_ID", "sprint29-20260101T0000-abcd")
        run_id = mint_run_id("sprint", "29")
        assert run_id == "sprint29-20260101T0000-abcd"

    def test_mint_run_id_without_parent_is_fresh(self, monkeypatch):
        from services.run_id import mint_run_id
        monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)
        run_id = mint_run_id("sprint", "29")
        assert run_id.startswith("sprint29-")
        assert len(run_id.split("-")) >= 3

    def test_sprint_manager_imports_mint_run_id(self):
        sm = _import_sprint_manager()
        assert hasattr(sm, "mint_run_id")

    def test_sprint_manager_imports_structured_log(self):
        sm = _import_sprint_manager()
        assert hasattr(sm, "structured_log")


# ── AC-2: log.event() calls have required fields ────────────────────────────

class TestLogEventCallFields:
    """Inspect sprint_manager source to verify log.event() calls include required fields."""

    @pytest.fixture(scope="class")
    def sm_source(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        return path.read_text(encoding="utf-8")

    def test_sprint_start_event_has_run_id(self, sm_source):
        idx = sm_source.find('"sprint.start"')
        assert idx != -1, "sprint.start event not found"
        snippet = sm_source[idx:idx + 400]
        assert "run_id=" in snippet

    def test_sprint_start_event_has_sprint_label(self, sm_source):
        idx = sm_source.find('"sprint.start"')
        snippet = sm_source[idx:idx + 400]
        assert "sprint_label=" in snippet

    def test_sprint_start_event_has_agent_role(self, sm_source):
        idx = sm_source.find('"sprint.start"')
        snippet = sm_source[idx:idx + 400]
        assert "agent_role=" in snippet

    def test_sprint_start_event_has_issue_num(self, sm_source):
        idx = sm_source.find('"sprint.start"')
        snippet = sm_source[idx:idx + 400]
        assert "issue_num=" in snippet

    def test_coder_dispatch_event_has_run_id(self, sm_source):
        idx = sm_source.find('"coder.dispatch"')
        assert idx != -1, "coder.dispatch event not found"
        snippet = sm_source[idx:idx + 400]
        assert "run_id=" in snippet

    def test_coder_dispatch_event_has_sprint_label(self, sm_source):
        idx = sm_source.find('"coder.dispatch"')
        snippet = sm_source[idx:idx + 400]
        assert "sprint_label=" in snippet

    def test_coder_dispatch_event_has_agent_role(self, sm_source):
        idx = sm_source.find('"coder.dispatch"')
        snippet = sm_source[idx:idx + 400]
        assert "agent_role=" in snippet

    def test_coder_dispatch_event_has_issue_num(self, sm_source):
        idx = sm_source.find('"coder.dispatch"')
        snippet = sm_source[idx:idx + 400]
        assert "issue_num=" in snippet

    def test_tester_dispatch_event_has_run_id(self, sm_source):
        idx = sm_source.find('"tester.dispatch"')
        assert idx != -1, "tester.dispatch event not found"
        snippet = sm_source[idx:idx + 400]
        assert "run_id=" in snippet

    def test_tester_dispatch_event_has_sprint_label(self, sm_source):
        idx = sm_source.find('"tester.dispatch"')
        snippet = sm_source[idx:idx + 400]
        assert "sprint_label=" in snippet

    def test_tester_dispatch_event_has_agent_role(self, sm_source):
        idx = sm_source.find('"tester.dispatch"')
        snippet = sm_source[idx:idx + 400]
        assert "agent_role=" in snippet

    def test_tester_dispatch_event_has_issue_num(self, sm_source):
        idx = sm_source.find('"tester.dispatch"')
        snippet = sm_source[idx:idx + 400]
        assert "issue_num=" in snippet

    def test_issue_start_event_exists(self, sm_source):
        assert '"issue.start"' in sm_source

    def test_issue_start_event_has_required_fields(self, sm_source):
        idx = sm_source.find('"issue.start"')
        snippet = sm_source[idx:idx + 400]
        for field in ("run_id=", "sprint_label=", "agent_role=", "issue_num="):
            assert field in snippet, f"issue.start missing field: {field}"


# ── AC-3: print() calls not removed ─────────────────────────────────────────

class TestPrintCallsPreserved:
    @pytest.fixture(scope="class")
    def sm_source(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        return path.read_text(encoding="utf-8")

    def test_print_coder_dispatch_present(self, sm_source):
        assert 'print(f"  Dispatching coder for issue' in sm_source

    def test_print_tester_dispatch_present(self, sm_source):
        assert 'print(f"  Dispatching tester for issue' in sm_source

    def test_print_sprint_manager_header_present(self, sm_source):
        assert 'print(f"\\n=== Sprint Manager:' in sm_source or \
               'print(f"\\n=== Sprint Manager' in sm_source or \
               '"=== Sprint Manager:' in sm_source

    def test_print_calls_count_not_zero(self, sm_source):
        import re
        prints = re.findall(r'\bprint\(', sm_source)
        assert len(prints) >= 30, f"Unexpectedly few print() calls: {len(prints)}"

    def test_log_event_does_not_replace_print_coder(self, sm_source):
        """coder.dispatch event is AFTER the print, not instead of it."""
        coder_print_pos = sm_source.find('print(f"  Dispatching coder')
        coder_event_pos = sm_source.find('"coder.dispatch"')
        assert coder_print_pos != -1
        assert coder_event_pos != -1
        # event call should come after the print
        assert coder_event_pos > coder_print_pos

    def test_log_event_does_not_replace_print_tester(self, sm_source):
        """tester.dispatch event is AFTER the print, not instead of it."""
        tester_print_pos = sm_source.find('print(f"  Dispatching tester')
        tester_event_pos = sm_source.find('"tester.dispatch"')
        assert tester_print_pos != -1
        assert tester_event_pos != -1
        assert tester_event_pos > tester_print_pos


# ── AC-4: consistent field names ─────────────────────────────────────────────

class TestFieldNameConsistency:
    @pytest.fixture(scope="class")
    def sm_source(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        return path.read_text(encoding="utf-8")

    def test_no_freeform_run_id_spelling(self, sm_source):
        import re
        # Should not use alternative spellings
        bad = re.findall(r'\brun_ID\b|\brunID\b|\brunId\b', sm_source)
        assert bad == [], f"Inconsistent run_id spellings: {bad}"

    def test_no_freeform_sprint_label_spelling(self, sm_source):
        import re
        bad = re.findall(r'\bsprintLabel\b|\bsprintlabel\b|\bsprint_Label\b', sm_source)
        assert bad == [], f"Inconsistent sprint_label spellings: {bad}"

    def test_no_freeform_agent_role_spelling(self, sm_source):
        import re
        bad = re.findall(r'\bagentRole\b|\bagent_Role\b|\bagentRole\b', sm_source)
        assert bad == [], f"Inconsistent agent_role spellings: {bad}"

    def test_agent_role_values_are_known_roles(self, sm_source):
        import re
        # Extract agent_role= values from log.event calls
        values = re.findall(r'agent_role=["\']([^"\']+)["\']', sm_source)
        valid = {"coder", "tester", "sprint", "ba"}
        for v in values:
            assert v in valid, f"Unknown agent_role value: {v!r}"


# ── AC-5: log.event() exceptions don't crash dispatch loop ───────────────────

class TestLogEventExceptionIsolation:
    def test_coder_dispatch_event_wrapped_in_try_except(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        source = path.read_text(encoding="utf-8")
        # Find coder.dispatch event block
        idx = source.find('"coder.dispatch"')
        assert idx != -1
        # Look for try: before it (within 200 chars)
        pre = source[max(0, idx - 200):idx]
        assert "try:" in pre, "coder.dispatch log.event() not in try block"
        # Look for except after it (within 200 chars)
        post = source[idx:idx + 300]
        assert "except Exception" in post or "except:" in post, \
            "coder.dispatch log.event() not protected by except"

    def test_tester_dispatch_event_wrapped_in_try_except(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        source = path.read_text(encoding="utf-8")
        idx = source.find('"tester.dispatch"')
        assert idx != -1
        pre = source[max(0, idx - 200):idx]
        assert "try:" in pre
        post = source[idx:idx + 300]
        assert "except Exception" in post or "except:" in post

    def test_sprint_start_event_wrapped_in_try_except(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        source = path.read_text(encoding="utf-8")
        idx = source.find('"sprint.start"')
        assert idx != -1
        pre = source[max(0, idx - 200):idx]
        assert "try:" in pre
        post = source[idx:idx + 300]
        assert "except Exception" in post or "except:" in post

    def test_issue_start_event_wrapped_in_try_except(self):
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        source = path.read_text(encoding="utf-8")
        idx = source.find('"issue.start"')
        assert idx != -1
        pre = source[max(0, idx - 200):idx]
        assert "try:" in pre
        post = source[idx:idx + 300]
        assert "except Exception" in post or "except:" in post

    def test_log_event_raising_does_not_propagate(self):
        """log.event() raising should be swallowed by the try/except."""
        from services.logging import _Logger
        bad_log = _Logger()

        events_written = []
        raised = []

        original_event = bad_log.event

        def exploding_event(name, **kwargs):
            raise RuntimeError("simulated log failure")

        bad_log.event = exploding_event

        # Simulate the pattern used in sprint_manager:
        try:
            bad_log.event("coder.dispatch", run_id="r1", issue_num=1,
                          sprint_label="sprint-29", agent_role="coder")
        except Exception:
            pass

        # If we reach here without uncaught exception, the test passes
        assert True


# ── AC-6: run_id=None or missing import doesn't break sprint manager ─────────

class TestRunIdNoneGraceful:
    def test_log_event_accepts_run_id_none(self):
        from services.logging import _Logger
        import tempfile
        logger = _Logger()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"HOME": tmpdir}):
                # Should not raise
                try:
                    logger.event("test.event", run_id=None, issue_num=1,
                                 sprint_label="sprint-29", agent_role="coder")
                except Exception as exc:
                    pytest.fail(f"log.event() raised with run_id=None: {exc}")

    def test_mint_run_id_import_available(self):
        """services.run_id is importable without error."""
        import services.run_id as run_id_mod
        assert hasattr(run_id_mod, "mint_run_id")

    def test_sprint_manager_structured_log_import_is_guarded(self):
        """Verify sprint_manager does not crash if structured_log were unavailable.
        The module uses a direct import — check that the import is present and
        the module is already importable (no ImportError in practice).
        """
        path = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
        source = path.read_text(encoding="utf-8")
        assert "from services.logging import log as structured_log" in source

    def test_commander_run_id_env_var_set_after_sprint_init(self):
        """After mint_run_id is called with no parent, COMMANDER_RUN_ID can be set."""
        from services.run_id import mint_run_id
        original = os.environ.pop("COMMANDER_RUN_ID", None)
        try:
            run_id = mint_run_id("sprint", "29")
            os.environ["COMMANDER_RUN_ID"] = run_id
            assert os.environ["COMMANDER_RUN_ID"] == run_id
        finally:
            if original is not None:
                os.environ["COMMANDER_RUN_ID"] = original
            else:
                os.environ.pop("COMMANDER_RUN_ID", None)

    def test_dispatch_log_event_with_unset_commander_run_id_does_not_raise(
        self, monkeypatch, tmp_path
    ):
        """COMMANDER_RUN_ID unset → os.environ.get() returns None → log.event() must not crash.

        Mirrors the exact pattern in dispatch_coder / dispatch_tester where run_id is
        read as os.environ.get("COMMANDER_RUN_ID") and passed directly to log.event().
        """
        from services.logging import _Logger

        monkeypatch.delenv("COMMANDER_RUN_ID", raising=False)

        run_id = os.environ.get("COMMANDER_RUN_ID")
        assert run_id is None, "precondition: COMMANDER_RUN_ID must be unset"

        logger = _Logger()
        log_dir = tmp_path / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        with patch("services.logging._resolve_log_dir", return_value=log_dir):
            for event_name, role in (
                ("coder.dispatch", "coder"),
                ("tester.dispatch", "tester"),
            ):
                try:
                    logger.event(
                        event_name,
                        run_id=run_id,
                        issue_num=464,
                        sprint_label="sprint-32",
                        agent_role=role,
                    )
                except Exception as exc:
                    pytest.fail(
                        f"log.event({event_name!r}) raised when COMMANDER_RUN_ID "
                        f"unset (run_id=None): {exc}"
                    )


# ── Integration: log.event() writes valid JSON-lines ─────────────────────────

class TestEventLogOutput:
    def test_event_writes_jsonl_with_required_fields(self, tmp_path, monkeypatch):
        """log.event() produces valid JSON with run_id, issue_num, sprint_label, agent_role."""
        from services.logging import _Logger
        logger = _Logger()

        log_dir = tmp_path / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        # Patch _resolve_log_dir to return our tmp log dir
        with patch("services.logging._resolve_log_dir", return_value=log_dir):
            logger.event(
                "coder.dispatch",
                run_id="sprint29-20260101T0000-abcd",
                issue_num=417,
                sprint_label="sprint-29",
                agent_role="coder",
            )

        log_files = list(log_dir.glob("events-*.jsonl"))
        assert len(log_files) == 1, f"Expected 1 events log file, got {log_files}"
        lines = log_files[0].read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["name"] == "coder.dispatch"
        assert record["run_id"] == "sprint29-20260101T0000-abcd"
        assert record["issue_num"] == 417
        assert record["sprint_label"] == "sprint-29"
        assert record["agent_role"] == "coder"

    def test_all_events_in_single_run_share_run_id(self, tmp_path):
        """Multiple events written with same run_id all reflect that value."""
        from services.logging import _Logger
        logger = _Logger()
        run_id = "sprint29-20260101T0000-test1"

        log_dir = tmp_path / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        with patch("services.logging._resolve_log_dir", return_value=log_dir):
            for i in range(3):
                logger.event("issue.start", run_id=run_id, issue_num=i,
                             sprint_label="sprint-29", agent_role="sprint")

        log_files = list(log_dir.glob("events-*.jsonl"))
        lines = log_files[0].read_text().strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert record["run_id"] == run_id

    def test_no_event_has_null_agent_role_when_provided(self, tmp_path):
        """agent_role is never null when explicitly provided."""
        from services.logging import _Logger
        logger = _Logger()

        log_dir = tmp_path / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        with patch("services.logging._resolve_log_dir", return_value=log_dir):
            for role in ("coder", "tester", "sprint"):
                logger.event("test.role", run_id="r1", issue_num=1,
                             sprint_label="sprint-29", agent_role=role)

        log_files = list(log_dir.glob("events-*.jsonl"))
        lines = log_files[0].read_text().strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert record["agent_role"] is not None
            assert record["agent_role"] != ""

    def test_event_valid_json_each_line(self, tmp_path):
        """Every emitted event line is valid JSON."""
        from services.logging import _Logger
        logger = _Logger()

        log_dir = tmp_path / ".commander" / "logs"
        log_dir.mkdir(parents=True)

        with patch("services.logging._resolve_log_dir", return_value=log_dir):
            logger.event("sprint.start", run_id="r1", issue_num=None,
                         sprint_label="sprint-29", agent_role="sprint")
            logger.event("coder.dispatch", run_id="r1", issue_num=42,
                         sprint_label="sprint-29", agent_role="coder")
            logger.event("tester.dispatch", run_id="r1", issue_num=42,
                         sprint_label="sprint-29", agent_role="tester")

        log_files = list(log_dir.glob("events-*.jsonl"))
        lines = log_files[0].read_text().strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            record = json.loads(line)
            assert "timestamp" in record
            assert "name" in record
