"""AC tests for the sprint-branch model (issue #2329).

feature -> sprint/sprint-N -> develop, restored per the operator decision
recorded in docs/milestones/commander-shrink-2026-08.md.

No live HTTP and no live git network: every git/gh invocation is a
mocked/monkeypatched boundary. Tests exercise real code paths (ensure_sprint_branch,
detect_sprint_branch_for_issue, start_feature.main(), finish_feature.main(),
dispatch_runner.execute_run, routers.sprints.dispatch_sprint) rather than
asserting on source text.

Test-order pollution (issue #2337)
----------------------------------
Three tests here used to pass in isolation and fail under `pytest tests/ -q`.
The polluter is `tests/test_643__editable_env_paths.py::client_ctx`, whose
fixture body does::

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects") or mod.startswith("services."):
            sys.modules.pop(mod, None)

That purge drops `services.sprint_manager.sprint_branch` and
`services.sprint_manager.dispatch_runner`, which this module bound at collection
time.  Any *later* `from services.sprint_manager... import ...` then builds a
fresh module object, so a monkeypatch applied to the object this module holds is
invisible to the code under test.

How it was found (repeatable in ~2 minutes, not 12):

1. Run `pytest <every test file that touches sys.modules> tests/test_sprint_branch_model__2329.py`
   with `--continue-on-collection-errors` — that 127-file subset reproduces the
   three failures.
2. Load a throwaway plugin that snapshots `id()` of the canary modules in
   `pytest_runtest_setup`/`teardown` and logs the first test after which one
   changes.  It names the polluter directly; bisecting halves does not, because
   more than one file has this purge shape.
3. Confirm with the two-file minimum: `pytest tests/test_643__editable_env_paths.py
   tests/test_sprint_branch_model__2329.py`.

The fix is to hold module *objects* rather than re-resolve names through
sys.modules: `scripts/start_feature.py` and `scripts/finish_feature.py` import
`sprint_branch` at module scope, and the execute_run-driven tests below use the
module `execute_run` itself was bound from.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager import sprint_branch as sb  # noqa: E402
from services.sprint_manager.dispatch_runner import (  # noqa: E402
    DispatchRun,
    execute_run,
)

# The exact module object `execute_run` above resolves its globals in.
#
# Re-importing by name inside a test (`import services.sprint_manager.dispatch_runner
# as dr`) is NOT guaranteed to be that same object: tests/test_643__editable_env_paths.py's
# `client_ctx` fixture purges every `services.*` key from sys.modules, so a later
# `import` rebuilds a *fresh* module and any patch applied to it lands somewhere the
# already-bound `execute_run` never reads.  Holding the module directly makes the
# execute_run-driven tests below independent of sys.modules state (issue #2337).
dispatch_runner = sys.modules[execute_run.__module__]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sf = _load("start_feature_under_test_2329", "scripts/start_feature.py")
ff = _load("finish_feature_under_test_2329", "scripts/finish_feature.py")


# === AC1: ensure_sprint_branch creates sprint/sprint-N from develop, idempotently ===

def test_ensure_sprint_branch_is_a_noop_when_branch_already_exists(monkeypatch):
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: True)

    def _boom(*cmd, cwd=None):
        raise AssertionError(f"must not touch git when branch already exists: {cmd}")

    monkeypatch.setattr(sb, "_try", _boom)

    result = sb.ensure_sprint_branch("sprint-9", "owner/repo")
    assert result == "sprint/sprint-9"


def test_ensure_sprint_branch_creates_off_develop_when_missing(monkeypatch):
    calls = []
    exists = {"v": False}
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: exists["v"])

    def fake_try(*cmd, cwd=None):
        calls.append(cmd)
        if cmd[:2] == ("git", "push"):
            exists["v"] = True
        return True, ""

    monkeypatch.setattr(sb, "_try", fake_try)

    result = sb.ensure_sprint_branch("sprint-9", "owner/repo")
    assert result == "sprint/sprint-9"
    assert ("git", "fetch", "origin", "develop") in calls
    push_calls = [c for c in calls if c[:2] == ("git", "push")]
    assert push_calls, "must push the new sprint branch"
    assert push_calls[0][2:] == (
        "origin", "refs/remotes/origin/develop:refs/heads/sprint/sprint-9",
    )


def test_ensure_sprint_branch_never_force_pushes_or_rebases(monkeypatch):
    """AC: a sprint branch is never force-pushed or rebased."""
    calls = []
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: False)

    def fake_try(*cmd, cwd=None):
        calls.append(cmd)
        return True, ""

    monkeypatch.setattr(sb, "_try", fake_try)
    sb.ensure_sprint_branch("sprint-9", "owner/repo")

    for cmd in calls:
        assert "-f" not in cmd and "--force" not in cmd, f"force flag in {cmd}"
        assert "rebase" not in cmd, f"rebase in {cmd}"


def test_ensure_sprint_branch_raises_on_unrecoverable_failure(monkeypatch):
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: False)
    monkeypatch.setattr(sb, "_try", lambda *cmd, cwd=None: (False, "boom"))

    with pytest.raises(RuntimeError):
        sb.ensure_sprint_branch("sprint-9", "owner/repo")


# === detect_sprint_branch_for_issue: single lookup used by both scripts ===

def test_detect_returns_branch_when_labelled_and_branch_exists(monkeypatch):
    fake_gh = type("_FakeGH", (), {})()
    fake_gh.get_issue = lambda n, repo_name=None: {
        "labels": [{"name": "sprint-42"}, {"name": "backend"}]
    }
    fake_gh.repo = lambda: "owner/repo"
    monkeypatch.setitem(sys.modules, "github_client", fake_gh)
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: True)

    assert sb.detect_sprint_branch_for_issue(42) == "sprint/sprint-42"


def test_detect_returns_none_when_issue_has_no_sprint_label(monkeypatch):
    fake_gh = type("_FakeGH", (), {})()
    fake_gh.get_issue = lambda n, repo_name=None: {"labels": [{"name": "backend"}]}
    fake_gh.repo = lambda: "owner/repo"
    monkeypatch.setitem(sys.modules, "github_client", fake_gh)
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: True)

    assert sb.detect_sprint_branch_for_issue(42) is None


def test_detect_returns_none_when_sprint_branch_missing_on_remote(monkeypatch):
    """A stale sprint label with no live branch must fall back to develop."""
    fake_gh = type("_FakeGH", (), {})()
    fake_gh.get_issue = lambda n, repo_name=None: {"labels": [{"name": "sprint-42"}]}
    fake_gh.repo = lambda: "owner/repo"
    monkeypatch.setitem(sys.modules, "github_client", fake_gh)
    monkeypatch.setattr(sb, "branch_exists_on_remote", lambda repo, branch: False)

    assert sb.detect_sprint_branch_for_issue(42) is None


def test_detect_never_raises_on_lookup_failure(monkeypatch):
    fake_gh = type("_FakeGH", (), {})()

    def _boom(n, repo_name=None):
        raise RuntimeError("gh api unavailable")

    fake_gh.get_issue = _boom
    monkeypatch.setitem(sys.modules, "github_client", fake_gh)

    assert sb.detect_sprint_branch_for_issue(42) is None


# === AC2: start_feature.py bases the feature branch off the sprint branch ===

def test_start_feature_uses_detected_sprint_branch_as_base(monkeypatch, capsys):
    monkeypatch.setattr(sf, "_try", lambda *c: (True, ""))  # branch "already exists locally"
    monkeypatch.setattr(sf, "_run", lambda *c: "")
    monkeypatch.setattr(
        sf.github_client, "get_issue",
        lambda n, repo_name=None: {"title": "Some Feature"},
    )
    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", lambda n, repo_name=None: "sprint/sprint-42")

    monkeypatch.setattr(sys, "argv", ["start_feature.py", "--issue", "77"])
    sf.main()

    out = capsys.readouterr().out
    assert "Base:           sprint/sprint-42" in out


def test_start_feature_falls_back_to_develop_when_no_sprint_branch(monkeypatch, capsys):
    monkeypatch.setattr(sf, "_try", lambda *c: (True, ""))
    monkeypatch.setattr(sf, "_run", lambda *c: "")
    monkeypatch.setattr(
        sf.github_client, "get_issue",
        lambda n, repo_name=None: {"title": "Some Feature"},
    )
    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", lambda n, repo_name=None: None)

    monkeypatch.setattr(sys, "argv", ["start_feature.py", "--issue", "78"])
    sf.main()

    out = capsys.readouterr().out
    assert "Base:           develop" in out


def test_start_feature_explicit_base_branch_bypasses_detection(monkeypatch, capsys):
    monkeypatch.setattr(sf, "_try", lambda *c: (True, ""))
    monkeypatch.setattr(sf, "_run", lambda *c: "")
    monkeypatch.setattr(
        sf.github_client, "get_issue",
        lambda n, repo_name=None: {"title": "Some Feature"},
    )

    def _boom(n, repo_name=None):
        raise AssertionError("detection must not run when --base-branch is explicit")

    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", _boom)

    monkeypatch.setattr(
        sys, "argv",
        ["start_feature.py", "--issue", "79", "--base-branch", "sprint/sprint-legacy"],
    )
    sf.main()

    out = capsys.readouterr().out
    assert "Base:           sprint/sprint-legacy" in out


# === AC3: finish_feature.py merges into the sprint branch, not develop ===

class _StopAfterBaselineCheck(Exception):
    pass


def _prep_finish_feature(monkeypatch, branch="feature/80-x"):
    monkeypatch.setattr(ff, "_run", lambda *c: "")
    monkeypatch.setattr(ff, "find_branch", lambda issue: branch)
    captured = {}

    def fake_gate(issue_num, br, target, repo, override):
        captured["target"] = target
        raise _StopAfterBaselineCheck()

    monkeypatch.setattr(ff, "_baseline_check_or_exit", fake_gate)
    return captured


def test_finish_feature_merges_into_detected_sprint_branch(monkeypatch):
    monkeypatch.delenv("COMMANDER_MERGE_TARGET", raising=False)
    captured = _prep_finish_feature(monkeypatch)
    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", lambda n, repo_name=None: "sprint/sprint-42")

    monkeypatch.setattr(sys, "argv", ["finish_feature.py", "--issue", "80"])
    with pytest.raises(_StopAfterBaselineCheck):
        ff.main()

    assert captured["target"] == "sprint/sprint-42"


def test_finish_feature_falls_back_to_develop_with_no_sprint_branch(monkeypatch):
    monkeypatch.delenv("COMMANDER_MERGE_TARGET", raising=False)
    captured = _prep_finish_feature(monkeypatch)
    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", lambda n, repo_name=None: None)

    monkeypatch.setattr(sys, "argv", ["finish_feature.py", "--issue", "81"])
    with pytest.raises(_StopAfterBaselineCheck):
        ff.main()

    assert captured["target"] == "develop"


def test_finish_feature_commander_merge_target_env_bypasses_detection(monkeypatch):
    """COMMANDER_MERGE_TARGET override must keep working (explicit AC)."""
    monkeypatch.setenv("COMMANDER_MERGE_TARGET", "custom-target")
    captured = _prep_finish_feature(monkeypatch)

    def _boom(n, repo_name=None):
        raise AssertionError("detection must not run when COMMANDER_MERGE_TARGET is set")

    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", _boom)

    monkeypatch.setattr(sys, "argv", ["finish_feature.py", "--issue", "82"])
    with pytest.raises(_StopAfterBaselineCheck):
        ff.main()

    assert captured["target"] == "custom-target"


def test_finish_feature_explicit_target_branch_flag_bypasses_detection(monkeypatch):
    monkeypatch.delenv("COMMANDER_MERGE_TARGET", raising=False)
    captured = _prep_finish_feature(monkeypatch)

    def _boom(n, repo_name=None):
        raise AssertionError("detection must not run when --target-branch is explicit")

    monkeypatch.setattr(sb, "detect_sprint_branch_for_issue", _boom)

    monkeypatch.setattr(
        sys, "argv",
        ["finish_feature.py", "--issue", "83", "--target-branch", "sprint/sprint-legacy"],
    )
    with pytest.raises(_StopAfterBaselineCheck):
        ff.main()

    assert captured["target"] == "sprint/sprint-legacy"


# === AC5: finishing a sprint opens exactly one PR from sprint/sprint-N into develop ===

def _run_with_branch(tickets, branch="sprint/sprint-1029", repo="owner/repo"):
    return DispatchRun(
        run_id="run2329", sprint_label="sprint-1029", tickets=list(tickets),
        repo=repo, sprint_branch=branch,
    )


def test_sprint_pr_opened_after_all_tickets_succeed(tmp_path, monkeypatch):
    dr = dispatch_runner  # the module execute_run reads (#2337)

    # This test covers the PR-opening path, not the gate — tmp_path has no
    # recorded baseline, and a missing baseline now refuses rather than skips.
    # The gate's own behaviour is covered by the AC4 tests further down.
    monkeypatch.setattr(dr, "_gate_sprint_pr", lambda **kw: (True, "stubbed"))

    gh_calls = []

    def fake_run(cmd, capture_output=None, text=None, cwd=None, timeout=None):
        gh_calls.append(cmd)
        assert cmd[:3] == ["gh", "pr", "create"]

        class _R:
            returncode = 0
            stdout = "https://github.com/owner/repo/pull/555\n"

        return _R()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        return True, "ok"

    run = execute_run(
        _run_with_branch([1]), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None,
    )

    assert run.status == "done"
    assert run.sprint_pr_number == 555
    assert len(gh_calls) == 1
    pr_cmd = gh_calls[0]
    assert "--head" in pr_cmd and "sprint/sprint-1029" in pr_cmd
    assert "--base" in pr_cmd and "develop" in pr_cmd


def test_sprint_pr_not_opened_when_a_ticket_fails(tmp_path, monkeypatch):
    dr = dispatch_runner  # the module execute_run reads (#2337)

    def _boom(*a, **k):
        raise AssertionError("must not open a PR when the sprint run failed")

    monkeypatch.setattr(dr.subprocess, "run", _boom)

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        return False, "coder failed"

    run = execute_run(
        _run_with_branch([1]), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None,
    )

    assert run.status == "failed"
    assert run.sprint_pr_number is None


def test_no_pr_opened_when_no_sprint_branch(tmp_path, monkeypatch):
    dr = dispatch_runner  # the module execute_run reads (#2337)

    def _boom(*a, **k):
        raise AssertionError("must not touch gh when there is no sprint branch")

    monkeypatch.setattr(dr.subprocess, "run", _boom)

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        return True, "ok"

    run = DispatchRun(run_id="r", sprint_label="sprint-1029", tickets=[1], repo="owner/repo")
    run = execute_run(run, repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None)

    assert run.status == "done"
    assert run.sprint_pr_number is None


# === AC6: no child sprint labels are ever minted ===

def test_sprint_pr_flow_never_issues_a_label_command(tmp_path, monkeypatch):
    """Per #2311: opening the sprint->develop PR must never mint a child label."""
    dr = dispatch_runner  # the module execute_run reads (#2337)

    gh_calls = []

    def fake_run(cmd, capture_output=None, text=None, cwd=None, timeout=None):
        gh_calls.append(cmd)

        class _R:
            returncode = 0
            stdout = "https://github.com/owner/repo/pull/9\n"

        return _R()

    monkeypatch.setattr(dr.subprocess, "run", fake_run)

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        return True, "ok"

    execute_run(_run_with_branch([1, 2]), repo_root=tmp_path, cwd=tmp_path, spawn=spawn, verify=None)

    for cmd in gh_calls:
        assert "label" not in " ".join(cmd), f"unexpected label command: {cmd}"
        joined = " ".join(cmd)
        assert not any(f"sprint-1029.{n}" in joined for n in "123"), (
            f"a child sprint label pattern appeared in a gh call: {cmd}"
        )


# === AC1 (dispatch side) + AC8: dispatch endpoint creates the sprint branch =========

def _load_sprints_router():
    import routers.sprints as routers_sprints
    return routers_sprints


def test_dispatch_endpoint_creates_sprint_branch_and_passes_it_through(tmp_path, monkeypatch):
    routers_sprints = _load_sprints_router()

    monkeypatch.setattr(routers_sprints, "_commander_repo_root", lambda: tmp_path)

    import services.sprint_manager.dispatch_runner as dr

    class _Config:
        repo_name = "owner/repo"

    monkeypatch.setattr(dr, "load_project_config", lambda cwd: _Config())

    ensure_calls = []

    def fake_ensure(sprint_label, repo, cwd=None):
        ensure_calls.append((sprint_label, repo))
        return f"sprint/{sprint_label}"

    monkeypatch.setattr(
        "services.sprint_manager.sprint_branch.ensure_sprint_branch", fake_ensure
    )

    captured_start_run = {}

    def fake_start_run(sprint_label, tickets, *, repo, repo_root, cwd, config, sprint_branch=None, **kw):
        captured_start_run["sprint_branch"] = sprint_branch
        captured_start_run["repo"] = repo

        class _Run:
            def to_dict(self):
                return {"run_id": "x", "sprint_branch": sprint_branch}

        return _Run()

    monkeypatch.setattr(dr, "start_run", fake_start_run)

    body = routers_sprints.SprintDispatchBody(tickets=[1, 2], repo="owner/repo", cwd=str(tmp_path))
    result = routers_sprints.dispatch_sprint("sprint-1029", body)

    assert ensure_calls == [("sprint-1029", "owner/repo")]
    assert captured_start_run["sprint_branch"] == "sprint/sprint-1029"
    assert result["sprint_branch"] == "sprint/sprint-1029"


def test_dispatch_endpoint_tolerates_sprint_branch_creation_failure(tmp_path, monkeypatch):
    """Non-fatal: a project without the sprint-branch model must keep dispatching."""
    routers_sprints = _load_sprints_router()
    monkeypatch.setattr(routers_sprints, "_commander_repo_root", lambda: tmp_path)

    import services.sprint_manager.dispatch_runner as dr

    class _Config:
        repo_name = "owner/repo"

    monkeypatch.setattr(dr, "load_project_config", lambda cwd: _Config())

    def _boom(sprint_label, repo, cwd=None):
        raise RuntimeError("no permission to create branches")

    monkeypatch.setattr(
        "services.sprint_manager.sprint_branch.ensure_sprint_branch", _boom
    )

    captured = {}

    def fake_start_run(sprint_label, tickets, *, repo, repo_root, cwd, config, sprint_branch=None, **kw):
        captured["sprint_branch"] = sprint_branch

        class _Run:
            def to_dict(self):
                return {"run_id": "x"}

        return _Run()

    monkeypatch.setattr(dr, "start_run", fake_start_run)

    body = routers_sprints.SprintDispatchBody(tickets=[1], repo="owner/repo", cwd=str(tmp_path))
    routers_sprints.dispatch_sprint("sprint-1029", body)

    assert captured["sprint_branch"] is None


# === AC4: the baseline-delta check must gate the final sprint->develop merge too ===

def test_sprint_pr_opening_is_gated_by_the_baseline_delta_check(monkeypatch):
    """AC4: the gate must actually run before `gh pr create`, not merely exist.

    The original form of this test asserted `hasattr(dr, "_gate_sprint_pr")` and
    stopped there — it built a fake gate and never called anything. That is the
    source-structure check CLAUDE.md #1746 forbids: it passes against a gate that
    is defined and never invoked. This calls the real _open_sprint_pr and watches
    what it does.
    """
    import services.sprint_manager.dispatch_runner as dr

    calls = {"gate": [], "pr": []}

    def fake_gate(*, sprint_branch, target, repo, cwd, **kw):
        calls["gate"].append((sprint_branch, target, repo))
        return True, "ok"

    def fake_run(cmd, *a, **kw):
        calls["pr"].append(cmd)
        class R:
            returncode = 0
            stdout = "https://github.com/o/r/pull/42\n"
            stderr = ""
        return R()

    monkeypatch.setattr(dr, "_gate_sprint_pr", fake_gate)
    monkeypatch.setattr(dr.subprocess, "run", fake_run)

    run = dr.DispatchRun(
        run_id="t", sprint_label="sprint-1028", tickets=[1],
        repo="zealchaiwut/commander",
    )
    run.sprint_branch = "sprint/sprint-1028"
    dr._open_sprint_pr(run, cwd=Path("."))

    assert calls["gate"], "_open_sprint_pr opened a PR without consulting the gate"
    assert calls["gate"][0][1] == "develop", "the final sprint merge must be gated against develop"


def test_a_refusing_gate_prevents_the_sprint_pr(monkeypatch):
    """'Neither merge may bypass it' — a refusal must stop `gh pr create`."""
    import services.sprint_manager.dispatch_runner as dr

    pr_cmds = []

    monkeypatch.setattr(
        dr, "_gate_sprint_pr",
        lambda **kw: (False, "failure count rose from 2377 to 2400"),
    )

    def fake_run(cmd, *a, **kw):
        pr_cmds.append(cmd)
        raise AssertionError("gh pr create ran despite the gate refusing")

    monkeypatch.setattr(dr.subprocess, "run", fake_run)

    run = dr.DispatchRun(
        run_id="t", sprint_label="sprint-1028", tickets=[1],
        repo="zealchaiwut/commander",
    )
    run.sprint_branch = "sprint/sprint-1028"
    assert dr._open_sprint_pr(run, cwd=Path(".")) is None
    assert not pr_cmds


def test_missing_baseline_refuses_rather_than_skipping(tmp_path, monkeypatch):
    """A missing baseline is a refusal, not a free pass (#2316, AC4 of #2329).

    The first implementation returned True here, reasoning that finish_feature.py
    had already enforced a baseline for per-ticket merges. That does not hold —
    per-ticket merges can be waved through with --override-baseline, leaving the
    sprint PR ungated.
    """
    import services.sprint_manager.dispatch_runner as dr
    import services.sprint_manager.pytest_runner as pr

    monkeypatch.setattr(dr, "load_project_config", lambda *a, **k: None, raising=False)
    monkeypatch.setenv("COMMANDER_BASELINE_CHECK", "1")
    (tmp_path / ".commander").mkdir(parents=True)

    def no_baseline(project, commander_dir):
        return None

    import services.sprint_manager.merge_baseline as mb
    monkeypatch.setattr(mb, "load_baseline", no_baseline)

    def boom(*a, **kw):
        raise AssertionError("the suite ran despite there being no baseline to compare against")

    monkeypatch.setattr(pr, "run_pytest", boom)

    allowed, reason = dr._gate_sprint_pr(
        sprint_branch="sprint/sprint-1028", target="develop",
        repo="zealchaiwut/commander", cwd=tmp_path,
    )
    assert allowed is False
    assert "baseline" in reason.lower()


def test_gate_reads_the_last_summary_line_not_the_first_counts(tmp_path, monkeypatch):
    r"""The gate must not re-implement the parser fixed in #2331.

    It runs `pytest tests/ -q`, the exact invocation whose output carries counts
    from commander's own pytest-in-subprocess tests before the real summary. An
    inline `re.search(r"(\d+) failed", out)` reported 999 for a 2377-failure run.
    """
    import services.sprint_manager.dispatch_runner as dr
    import services.sprint_manager.merge_baseline as mb
    import services.sprint_manager.pytest_runner as pr

    baseline = mb.Baseline(
        project="zealchaiwut/commander", failed=2377, passed=7279,
        skipped=351, failed_test_ids=[], pytest_args="tests/ -q",
    )
    monkeypatch.setattr(mb, "load_baseline", lambda *a, **k: baseline)
    (tmp_path / ".commander").mkdir(parents=True)

    output = (
        "  E           assert '5 passed, 999 failed in 1.02s' in result.stdout\n"
        "2377 failed, 7279 passed, 351 skipped, 469 errors in 741.96s (0:12:21)\n"
    )

    seen = {}

    def fake_run(args, *, cwd, timeout=None, env=None, isolate_db=True):
        seen["args"] = args
        seen["cwd"] = cwd
        class R:
            returncode = 1
            stdout = output
            stderr = ""
        return R()

    monkeypatch.setattr(pr, "run_pytest", fake_run)

    allowed, reason = dr._gate_sprint_pr(
        sprint_branch="sprint/sprint-1028", target="develop",
        repo="zealchaiwut/commander", cwd=tmp_path,
    )
    # 2377 == baseline 2377 and no new ids -> allowed. Misparsing as 999 would
    # also "allow", so assert on the reported number, not just the verdict.
    assert "2377" in reason, f"gate did not read the real failure count: {reason}"
    assert "999" not in reason
    assert allowed is True


def test_gate_invokes_the_running_interpreter(tmp_path, monkeypatch):
    """Gate drives pytest via run_pytest with the baseline's pytest args.

    ``run_pytest`` itself always prefixes with ``sys.executable`` (#2345);
    this test asserts the gate still passes the baseline args through.
    """
    import services.sprint_manager.dispatch_runner as dr
    import services.sprint_manager.merge_baseline as mb
    import services.sprint_manager.pytest_runner as pr

    monkeypatch.setattr(
        mb, "load_baseline",
        lambda *a, **k: mb.Baseline(project="o/r", failed=0, passed=1, pytest_args="tests/ -q"),
    )
    (tmp_path / ".commander").mkdir(parents=True)

    seen = {}

    def fake_run(args, *, cwd, timeout=None, env=None, isolate_db=True):
        seen["args"] = list(args)
        seen["cwd"] = cwd
        seen["timeout"] = timeout
        class R:
            returncode = 0
            stdout = "1 passed in 0.1s\n"
            stderr = ""
        return R()

    monkeypatch.setattr(pr, "run_pytest", fake_run)
    dr._gate_sprint_pr(
        sprint_branch="sprint/sprint-1028", target="develop",
        repo="o/r", cwd=tmp_path,
    )
    assert seen.get("args") == ["tests/", "-q"], seen
    assert seen.get("cwd") == str(tmp_path)
