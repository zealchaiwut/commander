"""Pre-merge sync must hard-reset the local sprint branch to its remote tip.

Bug: completing a sprint ran `git checkout <branch>` in the coder clone, whose
sprint branch was STALE (behind origin from earlier dispatch hygiene). develop
merged onto the old tip and the push was rejected as non-fast-forward
("tip is behind its remote counterpart"), so the sprint couldn't complete. The
fix resets the local branch to origin/<branch> before merging develop.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_prepare_force_syncs_local_branch_to_origin(monkeypatch, tmp_path):
    import startup

    calls = []

    def fake_run(args, **kw):
        calls.append(list(args))
        return _R(0)

    monkeypatch.setattr(startup, "_git_repo_for_merge", lambda repo: tmp_path)
    monkeypatch.setattr(startup.subprocess, "run", fake_run)

    ok, detail = startup._prepare_sprint_branch_for_develop_merge("owner/repo", "sprint/sprint-8")
    assert ok is True, detail

    # The branch is reset to the remote tip (force-sync), not a stale local checkout.
    assert ["git", "checkout", "-B", "sprint/sprint-8", "origin/sprint/sprint-8"] in calls
    assert ["git", "checkout", "sprint/sprint-8"] not in calls
    # Dirty state in the transient coder clone is discarded first.
    assert ["git", "reset", "--hard"] in calls
