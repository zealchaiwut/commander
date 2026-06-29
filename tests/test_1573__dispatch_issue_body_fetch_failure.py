"""Tests for issue #1573: dispatch double-fetches issue body on gh failure path.

Context: #1541 moved the issue-body fetch into the coder dispatch path so
`_build_design_block` no longer re-fetches on the happy path. But on the
failure path the dispatch fetch left `_fetched_issue_body` as None, which made
`_build_design_block` issue its OWN fallback `gh api` call — a second subprocess
round-trip per ticket — and the dispatch-side failure was swallowed by a bare
`except Exception: pass` with no log line.

AC items verified:
  AC-1  On dispatch fetch failure (non-zero exit OR exception), no second `gh`
        subprocess call is made for that ticket's issue body in the same cycle.
  AC-2  The bare `except Exception: pass` is replaced with explicit WARNING/ERROR
        logging that identifies the ticket number and the failure reason.
  AC-3  On failure a sentinel ("" empty string) is passed to `_build_design_block`
        so it skips its own fallback `gh api` fetch.
  AC-4  On failure `_build_design_block` still completes without raising —
        degraded heading-index output is acceptable.
  AC-5  On the happy path (fetch succeeds) behavior is unchanged: the pre-fetched
        body is used and zero additional `gh` calls are made.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.dispatch import _fetch_dispatch_issue_body  # noqa: E402
from services.sprint_manager.sprint_manager import _build_design_block  # noqa: E402

DESIGN_CONTENT = """\
# Project Design

## Sprint Board

The sprint board shows active and past sprints.

## Ticket Lifecycle

Tickets flow: backlog -> in-progress -> sit -> uat.
"""


def _ok_response(body: str = "") -> MagicMock:
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps({"body": body, "number": 1573, "title": "T"})
    mock.stderr = ""
    return mock


def _fail_response(returncode: int = 1, stderr: str = "HTTP 404") -> MagicMock:
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = ""
    mock.stderr = stderr
    return mock


# ---------------------------------------------------------------------------
# AC-3: sentinel returned on failure so _build_design_block skips its re-fetch
# ---------------------------------------------------------------------------

class TestAC3SentinelOnFailure:
    def test_nonzero_exit_returns_empty_string_sentinel(self):
        """A non-zero gh exit yields the sentinel '' (not None)."""
        with patch("subprocess.run", return_value=_fail_response()):
            result = _fetch_dispatch_issue_body("owner/repo", 1573)
        assert result == "", "Non-zero exit must return the '' sentinel, not None"
        assert result is not None

    def test_exception_returns_empty_string_sentinel(self):
        """An exception during fetch yields the sentinel '' (not None)."""
        with patch("subprocess.run", side_effect=OSError("boom")):
            result = _fetch_dispatch_issue_body("owner/repo", 1573)
        assert result == "", "Exception must return the '' sentinel, not None"
        assert result is not None


# ---------------------------------------------------------------------------
# AC-1: no second gh subprocess call on the failure path
# ---------------------------------------------------------------------------

class TestAC1NoSecondCallOnFailure:
    def test_only_one_subprocess_call_on_nonzero_exit(self, tmp_path):
        """Fetch (fails) + _build_design_block together make exactly ONE gh call."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        with patch("subprocess.run", return_value=_fail_response()) as mock_run:
            body = _fetch_dispatch_issue_body("owner/repo", 1573)
            _build_design_block(1573, "owner/repo", tmp_path, issue_body=body)
        assert mock_run.call_count == 1, (
            "Failure path must issue exactly one gh subprocess call — "
            f"got {mock_run.call_count} (the regression was a double-fetch)"
        )

    def test_only_one_subprocess_call_on_exception(self, tmp_path):
        """Even when the fetch raises, no second gh call happens downstream."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        with patch("subprocess.run", side_effect=OSError("boom")) as mock_run:
            body = _fetch_dispatch_issue_body("owner/repo", 1573)
            assert mock_run.call_count == 1
            _build_design_block(1573, "owner/repo", tmp_path, issue_body=body)
        # No additional call from _build_design_block.
        assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# AC-2: explicit logging identifying ticket number and failure reason
# ---------------------------------------------------------------------------

class TestAC2ExplicitLogging:
    def test_nonzero_exit_logs_warning_with_ticket_and_reason(self):
        """Non-zero exit emits a WARN/ERROR log naming the ticket and the reason."""
        with patch("subprocess.run", return_value=_fail_response(returncode=3)):
            with patch("services.sprint_manager.dispatch.structured_log") as mock_log:
                _fetch_dispatch_issue_body("owner/repo", 1573)
        # A warn or error must have been emitted (no silent swallow).
        assert mock_log.warn.called or mock_log.error.called, (
            "Failure must be logged at WARN or ERROR level, not swallowed"
        )
        call = mock_log.warn.call_args if mock_log.warn.called else mock_log.error.call_args
        # Ticket number must be identifiable in the call.
        all_text = " ".join(str(a) for a in call.args) + " " + str(call.kwargs)
        assert "1573" in all_text, "Log line must identify the ticket number"
        assert "3" in all_text, "Log line must identify the failure reason (exit code)"

    def test_exception_logs_warning_with_ticket_and_reason(self):
        """An exception emits a WARN/ERROR log naming the ticket and the error."""
        with patch("subprocess.run", side_effect=RuntimeError("network down")):
            with patch("services.sprint_manager.dispatch.structured_log") as mock_log:
                _fetch_dispatch_issue_body("owner/repo", 1573)
        assert mock_log.warn.called or mock_log.error.called
        call = mock_log.warn.call_args if mock_log.warn.called else mock_log.error.call_args
        all_text = " ".join(str(a) for a in call.args) + " " + str(call.kwargs)
        assert "1573" in all_text, "Log line must identify the ticket number"
        assert "network down" in all_text, "Log line must carry the error detail"


# ---------------------------------------------------------------------------
# AC-4: _build_design_block does not crash on the degraded path
# ---------------------------------------------------------------------------

class TestAC4NoCrashOnDegradedPath:
    def test_design_block_completes_with_sentinel(self, tmp_path):
        """With the sentinel body, _build_design_block returns without raising."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        with patch("subprocess.run", return_value=_fail_response()):
            body = _fetch_dispatch_issue_body("owner/repo", 1573)
        block = _build_design_block(1573, "owner/repo", tmp_path, issue_body=body)
        # Degraded heading-index output is acceptable; just must not crash.
        assert "Sprint Board" in block or "Ticket Lifecycle" in block


# ---------------------------------------------------------------------------
# AC-5: happy path unchanged — pre-fetched body used, zero extra gh calls
# ---------------------------------------------------------------------------

class TestAC5HappyPathUnchanged:
    def test_success_returns_body_and_no_extra_calls(self, tmp_path):
        """On success the fetched body is returned and reused without re-fetch."""
        (tmp_path / "DESIGN.md").write_text(DESIGN_CONTENT, encoding="utf-8")
        body_text = "## What & Why\n\nHello.\n"
        with patch("subprocess.run", return_value=_ok_response(body_text)) as mock_run:
            body = _fetch_dispatch_issue_body("owner/repo", 1573)
            assert body == body_text
            _build_design_block(1573, "owner/repo", tmp_path, issue_body=body)
        assert mock_run.call_count == 1, (
            "Happy path must make exactly one gh call total (fetch only)"
        )

    def test_no_repo_returns_none_for_legacy_path(self):
        """With no repo there is nothing to fetch; returns None (no subprocess)."""
        with patch("subprocess.run") as mock_run:
            result = _fetch_dispatch_issue_body(None, 1573)
        mock_run.assert_not_called()
        assert result is None
