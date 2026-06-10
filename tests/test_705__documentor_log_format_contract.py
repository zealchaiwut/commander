"""Tests for issue #705: Audit external tools for compatibility with the new
documentor log format.

Context (#697): ``_run_documentor()`` changed from per-ticket log lines to a
single per-sprint pair:

    [documentor] running for sprint {label} (N ticket(s): [list]) ...
    [documentor] completed for sprint {label}

The audit found that the only on-disk sprint-log consumer is
``apps/dashboard/log_source.py``, which returns the raw log *tail* verbatim and
does **not** structurally parse documentor lines. The dashboard log view is
therefore format-agnostic and compatible with the new format.

These regression tests pin that contract so a future format change is caught:

  AC-1  Opening log line uses the per-sprint format and carries the sprint
        label, the ticket count, and the full ticket list.
  AC-2  Closing log line uses the per-sprint format and carries the sprint label.
  AC-3  Per-ticket noise (non-zero exit, stderr) is written to stderr, keeping
        stdout a clean per-sprint summary for any log scraper.
  AC-4  log_source.read_log passes documentor lines through verbatim — no
        structured parsing — confirming the dashboard log view is format-agnostic.
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm
from apps.dashboard import log_source


def _run_documentor_capture(issue_nums, sprint_label, repo_name="owner/repo",
                            per_ticket_result=None):
    """Invoke _run_documentor with subprocess mocked; return (stdout, stderr)."""
    if per_ticket_result is None:
        per_ticket_result = MagicMock(returncode=0, stdout="", stderr="")

    out_buf, err_buf = io.StringIO(), io.StringIO()
    with patch.object(sm.subprocess, "run", return_value=per_ticket_result):
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            sm._run_documentor(issue_nums, sprint_label, repo_name)
    return out_buf.getvalue(), err_buf.getvalue()


# ---------------------------------------------------------------------------
# AC-1: opening line — per-sprint format with label, count, full list
# ---------------------------------------------------------------------------

class TestAC1OpeningLineFormat:
    def test_opening_line_has_label_count_and_list(self):
        out, _ = _run_documentor_capture([12, 34], "sprint-55")
        assert "[documentor] running for sprint sprint-55 (2 ticket(s): [12, 34]) ..." in out

    def test_opening_line_single_ticket(self):
        out, _ = _run_documentor_capture([7], "sprint-55")
        assert "[documentor] running for sprint sprint-55 (1 ticket(s): [7]) ..." in out

    def test_opening_line_is_per_sprint_not_per_ticket(self):
        """Exactly one 'running for sprint' line regardless of ticket count."""
        out, _ = _run_documentor_capture([1, 2, 3], "sprint-55")
        assert out.count("running for sprint") == 1


# ---------------------------------------------------------------------------
# AC-2: closing line — per-sprint format with label
# ---------------------------------------------------------------------------

class TestAC2ClosingLineFormat:
    def test_closing_line_has_label(self):
        out, _ = _run_documentor_capture([12, 34], "sprint-55")
        assert "[documentor] completed for sprint sprint-55" in out

    def test_closing_line_is_per_sprint(self):
        out, _ = _run_documentor_capture([1, 2, 3], "sprint-55")
        assert out.count("completed for sprint") == 1


# ---------------------------------------------------------------------------
# AC-3: per-ticket noise goes to stderr, stdout stays a clean summary
# ---------------------------------------------------------------------------

class TestAC3NoiseOnStderr:
    def test_nonzero_exit_reported_on_stderr_not_stdout(self):
        bad = MagicMock(returncode=1, stdout="", stderr="boom")
        out, err = _run_documentor_capture([9], "sprint-55", per_ticket_result=bad)
        assert "exited 1 (non-fatal)" in err
        assert "exited 1 (non-fatal)" not in out
        # The per-sprint summary lines remain on stdout, uncontaminated.
        assert "running for sprint sprint-55" in out
        assert "completed for sprint sprint-55" in out


# ---------------------------------------------------------------------------
# AC-4: log_source is format-agnostic — raw passthrough, no structured parsing
# ---------------------------------------------------------------------------

class TestAC4LogSourceFormatAgnostic:
    def _write_dispatch_log(self, project_root: Path, label: str, body: str) -> Path:
        log_dir = project_root / ".commander" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"sprint-run-{label}-20260610-000000.log"
        log_path.write_text(body, encoding="utf-8")
        return log_path

    def test_new_format_lines_pass_through_verbatim(self, tmp_path):
        label = "sprint-55"
        body = (
            "  [documentor] running for sprint sprint-55 (2 ticket(s): [12, 34]) ...\n"
            "  [documentor] completed for sprint sprint-55\n"
        )
        self._write_dispatch_log(tmp_path, label, body)

        result = log_source.read_log("dispatch", tmp_path, label=label)

        assert result["found"] is True
        # Raw passthrough: the documentor lines appear verbatim, unmodified.
        assert "[documentor] running for sprint sprint-55 (2 ticket(s): [12, 34]) ..." in result["tail"]
        assert "[documentor] completed for sprint sprint-55" in result["tail"]

    def test_old_per_ticket_format_also_passes_through(self, tmp_path):
        """A format-agnostic reader handles the old per-ticket lines too —
        proving read_log does not depend on either format."""
        label = "sprint-55"
        body = (
            "  [documentor] running for issue #12 ...\n"
            "  [documentor] running for issue #34 ...\n"
        )
        self._write_dispatch_log(tmp_path, label, body)

        result = log_source.read_log("dispatch", tmp_path, label=label)

        assert result["found"] is True
        assert "running for issue #12" in result["tail"]
        assert "running for issue #34" in result["tail"]
