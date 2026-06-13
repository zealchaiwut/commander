"""Coder resume context (issue: coder re-implemented from scratch on re-run).
When the feature branch already has commits ahead of base, _dispatch_coder
injects a RESUME instruction so the coder continues instead of restarting."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))
import sprint_manager as sm


def _git_stub(count, log="abc123 feat: prior work", stat=" backend/x.py | 9 +++"):
    def fake(*args, **kw):
        if "rev-list" in args:
            return (0, str(count), "")
        if "log" in args:
            return (0, log, "")
        if "diff" in args:
            return (0, stat, "")
        return (0, "", "")
    return fake


def test_resume_block_when_branch_ahead(tmp_path):
    with patch.object(sm, "_run_timed", side_effect=_git_stub(3)):
        out = sm._coder_resume_context(tmp_path, "sprint/sprint-66.4")
    assert "RESUME" in out
    assert "3 commit(s) ahead of sprint/sprint-66.4" in out
    assert "CONTINUING this work" in out
    assert "feat: prior work" in out          # prior commits shown
    assert "backend/x.py" in out              # files-changed summary shown


def test_no_resume_when_no_prior_work(tmp_path):
    with patch.object(sm, "_run_timed", side_effect=_git_stub(0)):
        assert sm._coder_resume_context(tmp_path, "develop") == ""


def test_no_resume_on_git_error(tmp_path):
    with patch.object(sm, "_run_timed", side_effect=lambda *a, **k: (1, "", "fatal")):
        assert sm._coder_resume_context(tmp_path, "develop") == ""
