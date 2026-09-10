"""Independent tester AC coverage for issue #2343.

Wires reviewer + documentor into sprint dispatch: after every ticket's tester
step passes, execute_run must run a once-per-sprint reviewer then documentor
wrap-up before opening the sprint->develop PR. A wrap-up failure halts the run
and the PR must never open. Exercises execute_run() directly with a fake
`spawn` callable and a monkeypatched `_open_sprint_pr` -- no live HTTP, no
`claude` subprocess, no source-text assertions.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.sprint_manager.dispatch_runner import (
    DispatchRun,
    ProjectDispatchConfig,
    WRAPUP_STEPS,
    execute_run,
)


def _make_run(tickets, sprint_branch="sprint/sprint-1029"):
    return DispatchRun(
        run_id="tester-2343",
        sprint_label="sprint-1029",
        tickets=list(tickets),
        repo="owner/repo",
        sprint_branch=sprint_branch,
    )


def test_reviewer_then_documentor_run_once_before_pr_multi_ticket(tmp_path, monkeypatch):
    """AC1-AC3: for a 2-ticket sprint, reviewer/documentor each fire exactly
    once (not once per ticket), only after both tickets' tester steps pass,
    and strictly before the sprint PR opens."""
    import services.sprint_manager.dispatch_runner as dr

    call_log: list[tuple[str, int]] = []
    pr_opened_after: list[str] = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        call_log.append((step, issue))
        return True, f"ok:{step}:{issue}"

    def fake_open_pr(run, *, cwd):
        pr_opened_after.append(",".join(f"{o.step}:{o.issue}" for o in run.outcomes))
        return 101

    monkeypatch.setattr(dr, "_open_sprint_pr", fake_open_pr)

    run = execute_run(
        _make_run([11, 12]), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert run.sprint_pr_number == 101

    reviewer_calls = [c for c in call_log if c[0] == "reviewer"]
    documentor_calls = [c for c in call_log if c[0] == "documentor"]
    assert len(reviewer_calls) == 1, f"reviewer must run once per sprint, got {reviewer_calls}"
    assert len(documentor_calls) == 1, f"documentor must run once per sprint, got {documentor_calls}"

    # Per-ticket steps for both tickets happened, then wrap-up, in order.
    assert call_log == [
        ("coder", 11), ("tester", 11),
        ("coder", 12), ("tester", 12),
        ("reviewer", reviewer_calls[0][1]),
        ("documentor", documentor_calls[0][1]),
    ]

    # PR must not have opened before the wrap-up outcomes existed.
    assert len(pr_opened_after) == 1
    assert "reviewer" in pr_opened_after[0] and "documentor" in pr_opened_after[0]


def test_reviewer_failure_halts_run_before_pr(tmp_path, monkeypatch):
    """AC4: reviewer failure sets status=failed, failed_step=reviewer, and the
    PR must never open."""
    import services.sprint_manager.dispatch_runner as dr

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step == "reviewer":
            return False, "reviewer agent crashed"
        return True, "ok"

    def must_not_open(*a, **k):
        raise AssertionError("sprint PR opened despite reviewer failure")

    monkeypatch.setattr(dr, "_open_sprint_pr", must_not_open)

    run = execute_run(
        _make_run([1]), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    assert run.status == "failed"
    assert run.failed_step == "reviewer"
    assert run.sprint_pr_number is None
    assert not any(o.step == "documentor" for o in run.outcomes), (
        "documentor must not run after reviewer already failed"
    )


def test_documentor_failure_halts_run_before_pr(tmp_path, monkeypatch):
    """AC4: documentor failure (after a successful reviewer) also halts the
    run and blocks the PR."""
    import services.sprint_manager.dispatch_runner as dr

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step == "documentor":
            return False, "docs step crashed"
        return True, "ok"

    def must_not_open(*a, **k):
        raise AssertionError("sprint PR opened despite documentor failure")

    monkeypatch.setattr(dr, "_open_sprint_pr", must_not_open)

    run = execute_run(
        _make_run([1]), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    assert run.status == "failed"
    assert run.failed_step == "documentor"
    assert run.sprint_pr_number is None
    reviewer_outcomes = [o for o in run.outcomes if o.step == "reviewer"]
    assert reviewer_outcomes and reviewer_outcomes[0].ok


def test_skip_escape_hatches_still_open_pr_and_record_skip(tmp_path, monkeypatch):
    """AC5 escape hatch: COMMANDER_SKIP_REVIEW / COMMANDER_SKIP_DOCS bypass the
    agent spawn but the PR still opens, and the skip is recorded in outcomes
    (not silently dropped)."""
    import services.sprint_manager.dispatch_runner as dr

    monkeypatch.setenv("COMMANDER_SKIP_REVIEW", "1")
    monkeypatch.setenv("COMMANDER_SKIP_DOCS", "1")

    spawned_wrapup_steps = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step in WRAPUP_STEPS:
            spawned_wrapup_steps.append(step)
        return True, "ok"

    monkeypatch.setattr(dr, "_open_sprint_pr", lambda run, *, cwd: 55)

    run = execute_run(
        _make_run([1]), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert run.sprint_pr_number == 55
    assert spawned_wrapup_steps == [], "skipped steps must not actually spawn an agent"
    skip_outcomes = {o.step: o for o in run.outcomes if o.step in WRAPUP_STEPS}
    assert set(skip_outcomes) == {"reviewer", "documentor"}
    assert all(o.ok for o in skip_outcomes.values())


def test_default_reviewer_and_documentor_models_used_without_config(tmp_path, monkeypatch):
    """AC7: absent a ProjectDispatchConfig override, the wrap-up steps use the
    module-level defaults (which mirror settings_schema.py's reviewer_model /
    documentor_model defaults)."""
    import services.sprint_manager.dispatch_runner as dr

    models_seen = {}

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step in WRAPUP_STEPS:
            models_seen[step] = model
        return True, "ok"

    monkeypatch.setattr(dr, "_open_sprint_pr", lambda run, *, cwd: 1)

    execute_run(
        _make_run([1]), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None, config=None,
    )

    assert models_seen["reviewer"] == dr.DEFAULT_REVIEWER_MODEL
    assert models_seen["documentor"] == dr.DEFAULT_DOCUMENTOR_MODEL


def test_configured_models_override_defaults(tmp_path, monkeypatch):
    """AC7: agent_config.reviewer_model / documentor_model, when present on the
    project's sprint.yaml-derived config, take priority over the defaults."""
    import services.sprint_manager.dispatch_runner as dr

    models_seen = {}

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        if step in WRAPUP_STEPS:
            models_seen[step] = model
        return True, "ok"

    monkeypatch.setattr(dr, "_open_sprint_pr", lambda run, *, cwd: 1)

    cfg = ProjectDispatchConfig(
        repo_name="owner/repo",
        coder_prompt="coder {issue_url}",
        tester_prompt="tester {issue_url}",
        coder_worktree=tmp_path,
        tester_worktree=tmp_path,
        reviewer_model="custom-reviewer",
        documentor_model="custom-documentor",
    )

    execute_run(
        _make_run([1]), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None, config=cfg,
    )

    assert models_seen["reviewer"] == "custom-reviewer"
    assert models_seen["documentor"] == "custom-documentor"


def test_ticket_order_preserved_through_to_wrapup(tmp_path, monkeypatch):
    """AC6 invariant carried from #2311/#2329: dispatch never reorders
    tickets. Wrap-up outcomes use issue=0 (sprint-level), never rewriting or
    reassigning a per-ticket outcome -- so the per-ticket outcome order in
    `run.outcomes` must exactly match the order tickets were handed in,
    regardless of ticket number magnitude."""
    import services.sprint_manager.dispatch_runner as dr

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        return True, "ok"

    monkeypatch.setattr(dr, "_open_sprint_pr", lambda run, *, cwd: 1)

    given_order = [42, 7, 19]
    run = execute_run(
        _make_run(given_order), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    per_ticket_issues = [o.issue for o in run.outcomes if o.step in ("coder", "tester")]
    assert per_ticket_issues == [42, 42, 7, 7, 19, 19]
    assert run.tickets == given_order  # never mutated/sorted

    wrapup_outcomes = [o for o in run.outcomes if o.step in WRAPUP_STEPS]
    assert all(o.issue == 0 for o in wrapup_outcomes), (
        "wrap-up outcomes must be sprint-level (issue=0), not tied to any single ticket"
    )


def test_no_sprint_branch_means_no_wrapup_and_no_pr(tmp_path, monkeypatch):
    """Wrap-up only applies to the sprint-branch model (#2329). Without a
    sprint branch there is nothing to review/document against and no PR to
    gate, so wrap-up must be skipped entirely."""
    import services.sprint_manager.dispatch_runner as dr

    seen_steps = []

    def spawn(step, issue, repo, *, cwd, baseline_note, prompt=None, model=None, **kw):
        seen_steps.append(step)
        return True, "ok"

    def must_not_open(*a, **k):
        raise AssertionError("no sprint branch means no sprint PR")

    monkeypatch.setattr(dr, "_open_sprint_pr", must_not_open)

    run = execute_run(
        _make_run([1], sprint_branch=None), repo_root=tmp_path, cwd=tmp_path,
        spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert seen_steps == ["coder", "tester"]
    assert not any(o.step in WRAPUP_STEPS for o in run.outcomes)
