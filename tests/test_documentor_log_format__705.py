"""Tester AC verification for issue #705: audit external tools for compatibility
with the new per-sprint documentor log format.

This is a backend log-format audit ticket with no HTTP surface, so verification
runs in-process rather than against $UAT_BASE_URL. Each test pins one acceptance
criterion derived from the ticket and the coder's audit conclusion (#697 changed
``_run_documentor`` to per-sprint opening/closing log lines; the only on-disk
consumer, ``log_source.read_log``, returns the raw tail verbatim).
"""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm
from apps.dashboard import log_source


def _capture(issue_nums, sprint_label, result=None):
    if result is None:
        result = MagicMock(returncode=0, stdout="", stderr="")
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sm.subprocess, "run", return_value=result):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            sm._run_documentor(issue_nums, sprint_label, "owner/repo")
    return out.getvalue(), err.getvalue()


def test_documentor_log_format__opening_line_is_per_sprint():
    # AC: opening line is a single per-sprint line carrying label, count, list
    out, _ = _capture([12, 34], "sprint-55")
    assert "[documentor] running for sprint sprint-55 (2 ticket(s): [12, 34]) ..." in out
    assert out.count("running for sprint") == 1


def test_documentor_log_format__closing_line_is_per_sprint():
    # AC: closing line is a single per-sprint line carrying the label
    out, _ = _capture([1, 2, 3], "sprint-55")
    assert "[documentor] completed for sprint sprint-55" in out
    assert out.count("completed for sprint") == 1


def test_documentor_log_format__per_ticket_noise_on_stderr():
    # AC: per-ticket failures go to stderr, keeping stdout a clean summary
    out, err = _capture([9], "sprint-55", MagicMock(returncode=1, stdout="", stderr="boom"))
    assert "exited 1 (non-fatal)" in err
    assert "exited 1 (non-fatal)" not in out


def test_documentor_log_format__log_source_passes_through_verbatim(tmp_path):
    # AC: the only on-disk consumer returns documentor lines verbatim (no parse)
    log_dir = tmp_path / ".commander" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "sprint-run-sprint-55-20260610-000000.log").write_text(
        "  [documentor] running for sprint sprint-55 (2 ticket(s): [12, 34]) ...\n"
        "  [documentor] completed for sprint sprint-55\n",
        encoding="utf-8",
    )
    result = log_source.read_log("dispatch", tmp_path, label="sprint-55")
    assert result["found"] is True
    assert "running for sprint sprint-55 (2 ticket(s): [12, 34]) ..." in result["tail"]
    assert "completed for sprint sprint-55" in result["tail"]
