"""Behavioral tests for issue #2343 — wire reviewer + documentor into dispatch.

After the last ticket's tester step succeeds, execute_run must invoke reviewer
then documentor (once each, sprint-level) before opening the sprint→develop PR.
A wrap-up failure halts the run with failed_step set and never opens the PR.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager.dispatch_runner import (  # noqa: E402
    DispatchRun,
    WRAPUP_STEPS,
    execute_run,
)


def _run(tickets=(1,), sprint_branch="sprint/sprint-1029"):
    return DispatchRun(
        run_id="r2343",
        sprint_label="sprint-1029",
        tickets=list(tickets),
        repo="owner/repo",
        sprint_branch=sprint_branch,
    )


def test_wrapup_steps_run_once_before_pr(tmp_path, monkeypatch):
    """AC1–AC3: reviewer then documentor, once each, before PR-open."""
    import services.sprint_manager.dispatch_runner as dr

    seen_steps: list[tuple[str, int]] = []
    pr_calls: list[str] = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        seen_steps.append((step, issue))
        return True, f"ok:{step}"

    def fake_open(run, *, cwd):
        # Must only be called after wrap-up outcomes exist.
        wrap = [o.step for o in run.outcomes if o.step in WRAPUP_STEPS]
        assert wrap == list(WRAPUP_STEPS), f"PR opened before wrap-up finished: {wrap}"
        pr_calls.append("opened")
        return 42

    monkeypatch.setattr(dr, "_open_sprint_pr", fake_open)

    run = execute_run(
        _run(), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert run.sprint_pr_number == 42
    assert pr_calls == ["opened"]

    # Per-ticket coder/tester, then once-each wrap-up.
    assert seen_steps == [
        ("coder", 1), ("tester", 1),
        ("reviewer", 0), ("documentor", 0),
    ]
    wrap_outcomes = [o for o in run.outcomes if o.step in WRAPUP_STEPS]
    assert [o.step for o in wrap_outcomes] == ["reviewer", "documentor"]
    assert all(o.ok for o in wrap_outcomes)


def test_reviewer_failure_halts_before_pr(tmp_path, monkeypatch):
    """AC4/AC5: reviewer failure → status=failed, failed_step=reviewer, no PR."""
    import services.sprint_manager.dispatch_runner as dr

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step == "reviewer":
            return False, "reviewer blew up"
        return True, "ok"

    def boom_pr(*a, **k):
        raise AssertionError("sprint PR must not open after a wrap-up failure")

    monkeypatch.setattr(dr, "_open_sprint_pr", boom_pr)

    run = execute_run(
        _run(), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None,
    )

    assert run.status == "failed"
    assert run.failed_step == "reviewer"
    assert run.sprint_pr_number is None
    assert any(o.step == "reviewer" and not o.ok for o in run.outcomes)
    assert not any(o.step == "documentor" for o in run.outcomes)


def test_documentor_failure_halts_before_pr(tmp_path, monkeypatch):
    """AC4/AC5: documentor failure → failed_step=documentor, no PR."""
    import services.sprint_manager.dispatch_runner as dr

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step == "documentor":
            return False, "docs failed"
        return True, "ok"

    def boom_pr(*a, **k):
        raise AssertionError("sprint PR must not open after a wrap-up failure")

    monkeypatch.setattr(dr, "_open_sprint_pr", boom_pr)

    run = execute_run(
        _run(), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None,
    )

    assert run.status == "failed"
    assert run.failed_step == "documentor"
    assert run.sprint_pr_number is None


def test_skip_env_flags_bypass_wrapup_but_still_open_pr(tmp_path, monkeypatch):
    """AC5 escape hatch: COMMANDER_SKIP_REVIEW / COMMANDER_SKIP_DOCS."""
    import services.sprint_manager.dispatch_runner as dr

    monkeypatch.setenv("COMMANDER_SKIP_REVIEW", "1")
    monkeypatch.setenv("COMMANDER_SKIP_DOCS", "1")

    seen = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        seen.append(step)
        return True, "ok"

    monkeypatch.setattr(dr, "_open_sprint_pr", lambda run, *, cwd: 7)

    run = execute_run(
        _run(), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert run.sprint_pr_number == 7
    assert "reviewer" not in seen
    assert "documentor" not in seen
    # Outcomes still record the skip so the run log is honest.
    skipped = {o.step: o for o in run.outcomes if o.step in WRAPUP_STEPS}
    assert set(skipped) == {"reviewer", "documentor"}
    assert all("skipped" in o.detail for o in skipped.values())


def test_wrapup_uses_configured_models(tmp_path, monkeypatch):
    """AC7: reviewer/documentor models flow from ProjectDispatchConfig."""
    import services.sprint_manager.dispatch_runner as dr
    from services.sprint_manager.dispatch_runner import ProjectDispatchConfig

    models_seen = {}

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        models_seen[step] = model
        return True, "ok"

    monkeypatch.setattr(dr, "_open_sprint_pr", lambda run, *, cwd: 1)

    cfg = ProjectDispatchConfig(
        repo_name="owner/repo",
        coder_prompt="coder {issue_url}",
        tester_prompt="tester {issue_url}",
        coder_worktree=tmp_path,
        tester_worktree=tmp_path,
        coder_model="coder-model",
        reviewer_model="reviewer-model-x",
        documentor_model="documentor-model-y",
    )

    execute_run(
        _run(), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None, config=cfg,
    )

    assert models_seen["reviewer"] == "reviewer-model-x"
    assert models_seen["documentor"] == "documentor-model-y"


def test_no_sprint_branch_skips_wrapup_and_pr(tmp_path, monkeypatch):
    """Wrap-up is sprint-branch-model only — no branch ⇒ no wrap-up, no PR."""
    import services.sprint_manager.dispatch_runner as dr

    seen = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        seen.append(step)
        return True, "ok"

    def boom_pr(*a, **k):
        raise AssertionError("no sprint branch ⇒ no PR")

    monkeypatch.setattr(dr, "_open_sprint_pr", boom_pr)

    run = execute_run(
        _run(sprint_branch=None), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert seen == ["coder", "tester"]
    assert not any(o.step in WRAPUP_STEPS for o in run.outcomes)
