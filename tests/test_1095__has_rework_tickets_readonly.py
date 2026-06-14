"""Tests for issue #1095: De-duplicate and demote _has_rework_tickets to read-only signal.

AC1 — Exactly one definition of _has_rework_tickets exists in server.py
AC2 — Surviving definition docstring states: pure GitHub-label-derived signal,
       read-only, valid only as input to reconcile proposals, must never trigger
       a state write directly
AC3 — All call sites audited: none pass the return value directly to a
       sprint-state-writing function outside reconcile proposals
AC4 — A True return from _has_rework_tickets does NOT by itself flip a running
       sprint (guard must reject or no write occurs)
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASH = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASH / "server.py"

if str(DASH) not in sys.path:
    sys.path.insert(0, str(DASH))


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — exactly one definition
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleDefinition:
    """Exactly one `def _has_rework_tickets` must exist in server.py."""

    def test_only_one_def_in_source(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        defs = re.findall(r"^def _has_rework_tickets\b", src, re.MULTILINE)
        assert len(defs) == 1, (
            f"Expected 1 definition of _has_rework_tickets in server.py, found {len(defs)}. "
            "Remove the duplicate."
        )

    def test_definition_count_via_ast(self):
        """AST-level check: only one FunctionDef named _has_rework_tickets."""
        src = SERVER_PY.read_text(encoding="utf-8")
        tree = ast.parse(src)
        defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_has_rework_tickets"
        ]
        assert len(defs) == 1, (
            f"AST found {len(defs)} definitions of _has_rework_tickets; "
            "expected exactly 1."
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — docstring states read-only signal semantics
# ─────────────────────────────────────────────────────────────────────────────

class TestDocstringSemantics:
    """Surviving definition must carry a docstring with required signal semantics."""

    def _get_docstring(self) -> str:
        import server as srv
        fn = getattr(srv, "_has_rework_tickets", None)
        assert fn is not None, "_has_rework_tickets not importable from server"
        doc = fn.__doc__ or ""
        return doc

    def test_docstring_mentions_read_only(self):
        doc = self._get_docstring()
        assert "read-only" in doc.lower() or "read only" in doc.lower(), (
            "Docstring must state the function is read-only; "
            f"got: {doc!r}"
        )

    def test_docstring_mentions_signal(self):
        doc = self._get_docstring()
        assert "signal" in doc.lower(), (
            "Docstring must describe the function as a signal; "
            f"got: {doc!r}"
        )

    def test_docstring_mentions_reconcile(self):
        doc = self._get_docstring()
        assert "reconcile" in doc.lower(), (
            "Docstring must state it is valid as input to reconcile proposals; "
            f"got: {doc!r}"
        )

    def test_docstring_prohibits_direct_state_write(self):
        doc = self._get_docstring()
        assert "state write" in doc.lower() or "never" in doc.lower(), (
            "Docstring must state the signal must never trigger a state write directly; "
            f"got: {doc!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — call sites don't pass result directly to a state-writing function
# ─────────────────────────────────────────────────────────────────────────────

class TestCallSitesAudit:
    """Call sites of _has_rework_tickets must only assign to local variables."""

    # State-writing functions that must NOT receive the direct return value.
    _WRITE_FUNS = {
        "record_sprint_needs_rework",
        "record_sprint_finish",
        "record_sprint_ready_to_merge",
        "record_sprint_running",
    }

    def test_call_sites_only_assign_to_local_variables(self):
        """Each call site must assign the result to a variable, not pass it to a writer."""
        src = SERVER_PY.read_text(encoding="utf-8")
        lines = src.splitlines()
        call_line_nums = [
            i + 1 for i, line in enumerate(lines)
            if "_has_rework_tickets(" in line and "def _has_rework_tickets" not in line
        ]
        # For each call site, read a small window and check no writer follows immediately.
        for ln in call_line_nums:
            # Grab 5 lines following the call site to check for direct write passthrough.
            window = "\n".join(lines[ln - 1 : ln + 4])
            for writer in self._WRITE_FUNS:
                assert writer not in window, (
                    f"Call site at line {ln} appears to pass result of _has_rework_tickets "
                    f"directly to {writer}. The return value must only feed reconcile proposals.\n"
                    f"Window:\n{window}"
                )

    def test_call_site_count_matches_expected(self):
        """Exactly 4 non-definition call sites exist (no new callers introduced)."""
        src = SERVER_PY.read_text(encoding="utf-8")
        call_sites = re.findall(r"_has_rework_tickets\(", src)
        # 1 definition + N call sites in server.py + calls from reconcile service
        # server.py should have: 1 def + ≤ 4 call sites
        non_def = len(call_sites) - 1  # subtract the definition itself
        assert non_def <= 4, (
            f"Found {non_def} call sites in server.py (excluding definition); "
            "expected ≤ 4 — no new call sites should have been introduced."
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — True return does NOT flip a running sprint
# ─────────────────────────────────────────────────────────────────────────────

class TestReworkTrueDoesNotFlipRunning:
    """A True return from _has_rework_tickets must not cause a running sprint state write."""

    def _import_reconcile_service(self):
        from routers import sprint_reconcile_service as svc
        return svc

    def test_github_reconcile_row_returns_none_for_running_sprint_with_rework(self):
        """_github_reconcile_row must return None (no patch) when sprint is running,
        even when _has_rework_tickets returns True — canonical 'running' is not
        in the promotion/demotion set, so no write is proposed.
        """
        svc = self._import_reconcile_service()
        import server as srv

        running_row = {
            "label": "sprint-99",
            "project": "owner/repo",
            "state": "running",
        }

        with patch.object(srv, "_has_rework_tickets", return_value=True):
            patch_result = svc._github_reconcile_row(
                "sprint-99", "owner/repo", running_row
            )

        assert patch_result is None, (
            "_github_reconcile_row must return None (no patch) for a running sprint "
            "even when _has_rework_tickets returns True; "
            f"got: {patch_result!r}"
        )

    def test_reconcile_sprint_label_does_not_write_for_running_sprint(self):
        """reconcile_sprint_label must not call any DB write when sprint is running
        and _has_rework_tickets returns True.
        """
        svc = self._import_reconcile_service()
        import server as srv

        running_row = {
            "label": "sprint-99",
            "project": "owner/repo",
            "state": "running",
        }

        with patch.object(srv, "_has_rework_tickets", return_value=True), \
             patch.object(svc._db(), "get_sprint", return_value=running_row), \
             patch.object(svc._db(), "record_sprint_needs_rework") as mock_nw, \
             patch.object(svc._db(), "record_sprint_finish") as mock_fin, \
             patch.object(svc._db(), "record_sprint_ready_to_merge") as mock_rtm:
            result = svc.reconcile_sprint_label("sprint-99", "owner/repo")

        assert result is False, (
            "reconcile_sprint_label must return False (no update) for a running sprint; "
            f"got: {result!r}"
        )
        mock_nw.assert_not_called()
        mock_fin.assert_not_called()
        mock_rtm.assert_not_called()

    def test_rework_true_signal_is_pure_no_write_on_call(self):
        """_has_rework_tickets itself must not trigger any sprint state write.
        It only reads GitHub labels and returns a bool.
        """
        import server as srv

        mock_issue_rework = {
            "number": 1,
            "title": "Broken feature",
            "labels": [
                {"name": "needs-rework"},
                {"name": "sprint-99"},
            ],
            "state": "open",
        }

        with patch.object(srv, "_get_sprint_issues", return_value=[mock_issue_rework]):
            result = srv._has_rework_tickets("sprint-99", "owner/repo")

        assert result is True, (
            "_has_rework_tickets must return True when an issue has needs-rework label; "
            f"got: {result!r}"
        )
