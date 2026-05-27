"""Tests for issue #114 — sprint manager distinguishes stale vs valid summary issues.

Static checks validate source files directly (no live server required).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

SPRINT_SCRIPT = Path(__file__).parent.parent.parent.parent / "services" / "sprint_manager" / "sprint_manager.py"


# ── helpers ───────────────────────────────────────────────────────────────────

def _import_sprint_manager():
    """Import sprint_manager as a module with stubbed dependencies."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo                  = lambda: "zealchaiwut/commander"
    gc_stub.update_labels         = lambda *a, **kw: None
    gc_stub.add_comment           = lambda *a, **kw: None
    gc_stub.create_issue          = lambda **kw: (999, "https://github.com/zealchaiwut/commander/issues/999")
    gc_stub.search_issues_by_title = lambda *a, **kw: []
    gc_stub.get_issue             = lambda *a, **kw: {}
    gc_stub.update_issue_body     = lambda *a, **kw: None
    gc_stub.reopen_issue          = lambda *a, **kw: None
    sys.modules.setdefault("github_client", gc_stub)

    mod_name = "sprint_manager_114_import"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, SPRINT_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


_STUB_RUN_BODY = (
    "| End reason | stopped |\n"
    "| Duration | 0h 0m 3s |\n"
    "| TESTER_REJECTED | something |\n"
    "No issues shipped\n"
)

_FAILED_RUN_BODY = (
    "| End reason | stopped |\n"
    "| Duration | 1h 5m 3s |\n"
    "| TESTER_REJECTED | something |\n"
    "No issues shipped\n"
)

_VALID_RUN_BODY = (
    "| End reason | complete |\n"
    "| Duration | 1h 30m 0s |\n"
    "| #42 | Feature A | -- | UAT-approved / closed | -- |\n"
)


def _make_state(sm):
    state = sm.SprintState(
        sprint_label="sprint-5",
        sprint_number=5,
        start_timestamp="2026-05-01T08:00:00Z",
    )
    state.issues.append(sm.IssueState(
        number=1, title="Feature A", status="done",
        tokens_in=100, tokens_out=200,
    ))
    state.total_tokens_in  = 100
    state.total_tokens_out = 200
    return state


# ── AC-1: _is_stale_summary distinguishes valid vs stale ─────────────────────

class TestIsStaleFunction:
    """AC-1: _is_stale_summary correctly classifies summary issues."""

    def test_function_exists(self):
        assert "_is_stale_summary" in SPRINT_SCRIPT.read_text()

    def test_valid_body_not_stale(self):
        sm = _import_sprint_manager()
        is_stale, reason = sm._is_stale_summary(_VALID_RUN_BODY, state_reason=None)
        assert is_stale is False
        assert reason == ""

    def test_stub_run_body_is_stale(self):
        sm = _import_sprint_manager()
        is_stale, reason = sm._is_stale_summary(_STUB_RUN_BODY, state_reason=None)
        assert is_stale is True
        assert reason != ""

    def test_failed_run_body_is_stale(self):
        sm = _import_sprint_manager()
        is_stale, reason = sm._is_stale_summary(_FAILED_RUN_BODY, state_reason=None)
        assert is_stale is True

    def test_not_planned_state_reason_is_stale(self):
        sm = _import_sprint_manager()
        is_stale, reason = sm._is_stale_summary(_VALID_RUN_BODY, state_reason="not_planned")
        assert is_stale is True
        assert "not_planned" in reason

    def test_completed_state_reason_not_stale(self):
        sm = _import_sprint_manager()
        is_stale, reason = sm._is_stale_summary(_VALID_RUN_BODY, state_reason="completed")
        assert is_stale is False


# ── AC-2: Staleness detection conditions ─────────────────────────────────────

class TestStalenessConditions:
    """AC-2: Specific conditions that mark a summary as stale."""

    def test_stopped_zero_duration_all_rejected_nothing_shipped_is_stale(self):
        sm = _import_sprint_manager()
        body = (
            "| End reason | stopped |\n"
            "| Duration | 0h 0m 8s |\n"
            "TESTER_REJECTED\n"
            "No issues shipped\n"
        )
        is_stale, _ = sm._is_stale_summary(body, state_reason=None)
        assert is_stale is True

    def test_missing_one_stale_condition_not_stale(self):
        sm = _import_sprint_manager()
        # Has stopped + zero duration + TESTER_REJECTED but DOES have shipped issues
        body = (
            "| End reason | stopped |\n"
            "| Duration | 0h 0m 8s |\n"
            "TESTER_REJECTED\n"
            "| #1 | Feature A | -- | UAT-approved / closed | -- |\n"
        )
        is_stale, _ = sm._is_stale_summary(body, state_reason=None)
        assert is_stale is False

    def test_stopped_with_rejected_nothing_shipped_no_zero_dur_is_stale(self):
        # Covers the "failed-run summary" branch (no zero-duration requirement)
        sm = _import_sprint_manager()
        body = (
            "| End reason | stopped |\n"
            "| Duration | 0h 45m 3s |\n"
            "TESTER_REJECTED\n"
            "No issues shipped\n"
        )
        is_stale, reason = sm._is_stale_summary(body, state_reason=None)
        assert is_stale is True

    def test_not_planned_overrides_valid_body(self):
        sm = _import_sprint_manager()
        # Even a completely valid-looking body is stale if closed-as-not-planned
        is_stale, reason = sm._is_stale_summary(_VALID_RUN_BODY, state_reason="not_planned")
        assert is_stale is True
        assert "not_planned" in reason

    def test_state_file_newer_than_issue_is_stale(self):
        """AC-2: state file mtime newer than issue createdAt marks summary stale."""
        sm = _import_sprint_manager()
        # state file: 2025-05-01 (newer); issue created: 2025-01-01 (older)
        state_file_mtime = 1746057600.0  # 2025-05-01T00:00:00Z
        issue_created_at = "2025-01-01T00:00:00Z"  # 1735689600 < 1746057600
        is_stale, reason = sm._is_stale_summary(
            _VALID_RUN_BODY,
            state_reason=None,
            state_file_mtime=state_file_mtime,
            issue_created_at=issue_created_at,
        )
        assert is_stale is True
        assert "state file" in reason.lower() or "newer" in reason.lower()

    def test_state_file_older_than_issue_not_stale(self):
        """AC-2: state file mtime older than issue createdAt does not mark as stale."""
        sm = _import_sprint_manager()
        # state file: 2025-01-01 (older); issue created: 2025-05-01 (newer)
        state_file_mtime = 1735689600.0  # 2025-01-01T00:00:00Z
        issue_created_at = "2025-05-01T00:00:00Z"  # 1746057600 > 1735689600
        is_stale, reason = sm._is_stale_summary(
            _VALID_RUN_BODY,
            state_reason=None,
            state_file_mtime=state_file_mtime,
            issue_created_at=issue_created_at,
        )
        assert is_stale is False

    def test_state_file_mtime_none_no_error(self):
        """AC-2: no error when state_file_mtime is None (file absent)."""
        sm = _import_sprint_manager()
        is_stale, reason = sm._is_stale_summary(
            _VALID_RUN_BODY,
            state_reason=None,
            state_file_mtime=None,
            issue_created_at="2026-05-01T00:00:00Z",
        )
        assert is_stale is False


# ── AC-3: Stale summary triggers update, reopen, and comment ─────────────────

class TestStaleSummaryUpdate:
    """AC-3: Stale summary is updated in place, reopened if closed, comment added."""

    def _run_with_stale_existing(self, sm, existing_state="open", state_reason=None, body=None):
        """Helper: call create_summary_github_issue with a stale existing issue."""
        body = body or _STUB_RUN_BODY
        calls = {"update": False, "reopen": False, "comment": None}

        def fake_search(title, repo_name=None):
            return [{"number": 55, "url": "https://github.com/x/y/issues/55", "state": existing_state}]

        def fake_get_issue(num, repo_name=None):
            return {"number": num, "body": body, "stateReason": state_reason}

        def fake_update_body(num, content, repo_name=None):
            calls["update"] = True

        def fake_reopen(num, repo_name=None):
            calls["reopen"] = True

        def fake_comment(num, comment, repo_name=None):
            calls["comment"] = comment

        with mock.patch.object(sm.github_client, "search_issues_by_title", side_effect=fake_search), \
             mock.patch.object(sm.github_client, "get_issue", side_effect=fake_get_issue), \
             mock.patch.object(sm.github_client, "update_issue_body", side_effect=fake_update_body), \
             mock.patch.object(sm.github_client, "reopen_issue", side_effect=fake_reopen), \
             mock.patch.object(sm.github_client, "add_comment", side_effect=fake_comment):
            result = sm.create_summary_github_issue("new content", 5, "sprint-5")

        return calls, result

    def test_stale_stub_triggers_body_update(self):
        sm = _import_sprint_manager()
        calls, _ = self._run_with_stale_existing(sm)
        assert calls["update"] is True

    def test_stale_closed_issue_is_reopened(self):
        sm = _import_sprint_manager()
        calls, _ = self._run_with_stale_existing(sm, existing_state="closed")
        assert calls["reopen"] is True

    def test_open_stale_issue_not_reopened(self):
        sm = _import_sprint_manager()
        calls, _ = self._run_with_stale_existing(sm, existing_state="open")
        assert calls["reopen"] is False

    def test_stale_triggers_update_comment(self):
        sm = _import_sprint_manager()
        calls, _ = self._run_with_stale_existing(sm)
        assert calls["comment"] is not None
        assert "Summary updated after fresh sprint run on" in calls["comment"]
        assert "failed run" in calls["comment"]

    def test_stale_returns_existing_issue_num(self):
        sm = _import_sprint_manager()
        _, result = self._run_with_stale_existing(sm)
        assert result[0] == 55

    def test_not_planned_triggers_update(self):
        sm = _import_sprint_manager()
        calls, _ = self._run_with_stale_existing(sm, state_reason="not_planned", body=_VALID_RUN_BODY)
        assert calls["update"] is True

    def test_not_planned_closed_is_reopened(self):
        sm = _import_sprint_manager()
        calls, _ = self._run_with_stale_existing(
            sm, existing_state="closed", state_reason="not_planned", body=_VALID_RUN_BODY
        )
        assert calls["reopen"] is True


# ── AC-4: Valid existing summary skips creation ───────────────────────────────

class TestValidSummarySkipsCreation:
    """AC-4: Valid existing summary issues are not duplicated or updated."""

    def test_valid_existing_skips_create(self):
        sm = _import_sprint_manager()
        created = {"called": False}
        updated = {"called": False}

        def fake_search(title, repo_name=None):
            return [{"number": 55, "url": "https://github.com/x/y/issues/55", "state": "open"}]

        def fake_get_issue(num, repo_name=None):
            return {"number": num, "body": _VALID_RUN_BODY, "stateReason": None}

        def fake_create(**kw):
            created["called"] = True
            return (99, "https://x.com/issues/99")

        def fake_update(num, content, repo_name=None):
            updated["called"] = True

        with mock.patch.object(sm.github_client, "search_issues_by_title", side_effect=fake_search), \
             mock.patch.object(sm.github_client, "get_issue", side_effect=fake_get_issue), \
             mock.patch.object(sm.github_client, "create_issue", side_effect=fake_create), \
             mock.patch.object(sm.github_client, "update_issue_body", side_effect=fake_update):
            result = sm.create_summary_github_issue("new content", 5, "sprint-5")

        assert created["called"] is False, "Should not create a new issue"
        assert updated["called"] is False, "Should not update a valid summary"
        assert result[0] == 55

    def test_valid_existing_logs_skip(self, capsys):
        sm = _import_sprint_manager()

        def fake_search(title, repo_name=None):
            return [{"number": 55, "url": "https://github.com/x/y/issues/55", "state": "open"}]

        def fake_get_issue(num, repo_name=None):
            return {"number": num, "body": _VALID_RUN_BODY, "stateReason": None}

        with mock.patch.object(sm.github_client, "search_issues_by_title", side_effect=fake_search), \
             mock.patch.object(sm.github_client, "get_issue", side_effect=fake_get_issue):
            sm.create_summary_github_issue("new content", 5, "sprint-5")

        out = capsys.readouterr().out
        assert "skipping creation" in out


# ── AC-5: --force-summary always updates ─────────────────────────────────────

class TestForceSummaryFlag:
    """AC-5: --force-summary updates the summary regardless of staleness."""

    def test_force_summary_arg_exists_in_cli(self):
        content = SPRINT_SCRIPT.read_text()
        assert "--force-summary" in content

    def test_force_summary_updates_valid_issue(self):
        sm = _import_sprint_manager()
        updated = {"called": False}

        def fake_search(title, repo_name=None):
            return [{"number": 55, "url": "https://github.com/x/y/issues/55", "state": "open"}]

        def fake_get_issue(num, repo_name=None):
            return {"number": num, "body": _VALID_RUN_BODY, "stateReason": None}

        def fake_update(num, content, repo_name=None):
            updated["called"] = True

        def fake_comment(num, comment, repo_name=None):
            pass

        with mock.patch.object(sm.github_client, "search_issues_by_title", side_effect=fake_search), \
             mock.patch.object(sm.github_client, "get_issue", side_effect=fake_get_issue), \
             mock.patch.object(sm.github_client, "update_issue_body", side_effect=fake_update), \
             mock.patch.object(sm.github_client, "add_comment", side_effect=fake_comment):
            sm.create_summary_github_issue("new content", 5, "sprint-5", force_summary=True)

        assert updated["called"] is True

    def test_force_summary_adds_comment(self):
        sm = _import_sprint_manager()
        comments = []

        def fake_search(title, repo_name=None):
            return [{"number": 55, "url": "https://github.com/x/y/issues/55", "state": "open"}]

        def fake_get_issue(num, repo_name=None):
            return {"number": num, "body": _VALID_RUN_BODY, "stateReason": None}

        def fake_comment(num, comment, repo_name=None):
            comments.append(comment)

        with mock.patch.object(sm.github_client, "search_issues_by_title", side_effect=fake_search), \
             mock.patch.object(sm.github_client, "get_issue", side_effect=fake_get_issue), \
             mock.patch.object(sm.github_client, "update_issue_body", return_value=None), \
             mock.patch.object(sm.github_client, "add_comment", side_effect=fake_comment):
            sm.create_summary_github_issue("new content", 5, "sprint-5", force_summary=True)

        assert len(comments) == 1
        assert "Summary updated after fresh sprint run on" in comments[0]

    def test_force_summary_creates_when_no_existing(self):
        """--force-summary with no existing issue still creates normally."""
        sm = _import_sprint_manager()
        created = {"called": False}

        def fake_create(title, body, labels, repo_name=None):
            created["called"] = True
            return (99, "https://x.com/issues/99")

        with mock.patch.object(sm.github_client, "search_issues_by_title", return_value=[]), \
             mock.patch.object(sm.github_client, "create_issue", side_effect=fake_create), \
             mock.patch.object(sm, "_ensure_github_labels"):
            sm.create_summary_github_issue("new content", 5, "sprint-5", force_summary=True)

        assert created["called"] is True

    def test_write_sprint_summary_passes_force_summary(self, tmp_path):
        """write_sprint_summary propagates force_summary to create_summary_github_issue."""
        sm    = _import_sprint_manager()
        state = _make_state(sm)
        captured = {}

        def fake_create_issue(content, sprint_number, sprint_label,
                              repo_name=None, force_summary=False,
                              state_file_path=None):
            captured["force_summary"] = force_summary
            return (99, "https://x.com/issues/99")

        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue", side_effect=fake_create_issue), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            sm.write_sprint_summary(state, elapsed_secs=60, force_summary=True)

        assert captured.get("force_summary") is True


# ── AC-6: Summary file always written to disk ─────────────────────────────────

class TestSummaryFileAlwaysWritten:
    """AC-6: Sprint summary .md file is written regardless of GitHub issue outcome."""

    def test_summary_written_when_issue_skipped(self, tmp_path):
        """File is written even when a valid existing issue is skipped."""
        sm    = _import_sprint_manager()
        state = _make_state(sm)

        def fake_create_issue(*a, **kw):
            return (55, "https://x.com/issues/55")

        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue", side_effect=fake_create_issue), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            path = sm.write_sprint_summary(state, elapsed_secs=60)

        assert path.exists(), "Summary .md file must be written to disk"
        assert "sprint-5" in path.name

    def test_summary_written_when_issue_creation_fails(self, tmp_path):
        """File is written even when GitHub issue creation raises an exception."""
        sm    = _import_sprint_manager()
        state = _make_state(sm)

        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue",
                               side_effect=Exception("network error")), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            path = sm.write_sprint_summary(state, elapsed_secs=60)

        assert path.exists(), "Summary .md file must be written even on GitHub error"

    def test_summary_written_when_stale_update_fails(self, tmp_path):
        """File is written even when stale-update throws."""
        sm    = _import_sprint_manager()
        state = _make_state(sm)

        def fake_create_issue(*a, **kw):
            raise RuntimeError("gh CLI error")

        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue", side_effect=fake_create_issue), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            path = sm.write_sprint_summary(state, elapsed_secs=60)

        assert path.exists()

    def test_summary_content_present_in_file(self, tmp_path):
        sm    = _import_sprint_manager()
        state = _make_state(sm)

        with mock.patch.object(sm, "SPRINTS_DIR", tmp_path / "sprints"), \
             mock.patch.object(sm, "create_summary_github_issue", return_value=(99, "")), \
             mock.patch.object(sm, "_prompt_learnings", return_value=""):
            path = sm.write_sprint_summary(state, elapsed_secs=60)

        content = path.read_text()
        assert "Sprint 5" in content


# ── New github_client helpers ─────────────────────────────────────────────────

class TestGithubClientHelpers:
    """Verify reopen_issue and update_issue_body are wired up in github_client.py."""

    _GC_PATH = Path(__file__).parent.parent / "github_client.py"

    def test_reopen_issue_in_github_client(self):
        assert "def reopen_issue" in self._GC_PATH.read_text()

    def test_update_issue_body_in_github_client(self):
        assert "def update_issue_body" in self._GC_PATH.read_text()

    def test_get_issue_fetches_state_reason(self):
        content = self._GC_PATH.read_text()
        assert "stateReason" in content
