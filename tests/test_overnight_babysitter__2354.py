"""Behavioral AC tests for overnight babysitter (issue #2354)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.dispatch_runner import (  # noqa: E402
    DispatchRun,
    execute_run,
    save_run,
)
from services.sprint_manager.overnight_runner import (  # noqa: E402
    execute_overnight,
    OvernightRun,
    request_stop,
)


class FlakySpawn:
    """Fails coder on issue 2 the first time; succeeds thereafter."""

    def __init__(self):
        self.calls = []
        self._fail_once = True

    def __call__(self, step, issue, repo, *, cwd, baseline_note, **kw):
        self.calls.append((step, issue))
        if issue == 2 and step == "coder" and self._fail_once:
            self._fail_once = False
            return False, "simulated fail"
        return True, "ok"


def _start_dispatch_factory(tmp_path, spawn):
    def start_dispatch(**kw):
        run = DispatchRun(
            run_id=f"d{len(list((tmp_path / '.commander' / 'runtime').glob('dispatch-*.json')) if (tmp_path / '.commander' / 'runtime').exists() else []) + 1}",
            sprint_label=kw["sprint_label"],
            tickets=list(kw["tickets"]),
            repo=kw.get("repo"),
            started_at="t0",
        )
        # Unique id
        import uuid
        run.run_id = uuid.uuid4().hex[:8]
        save_run(run, kw["repo_root"])
        return execute_run(
            run,
            repo_root=kw["repo_root"],
            cwd=kw["cwd"],
            spawn=spawn,
            verify=None,
            config=kw.get("config"),
        )

    return start_dispatch


def test_overnight_retries_once_then_done(tmp_path):
    spawn = FlakySpawn()
    resets = []

    def reset_fn(issue, **kw):
        resets.append(issue)
        return None

    overnight = OvernightRun(
        overnight_id="ov1",
        sprint_label="sprint-1030",
        tickets=[1, 2, 3],
        repo="owner/repo",
        max_retries=2,
    )
    result = execute_overnight(
        overnight,
        repo_root=tmp_path,
        cwd=tmp_path,
        github_client=object(),
        spawn=spawn,
        verify=None,
        start_dispatch=_start_dispatch_factory(tmp_path, spawn),
        reset_fn=reset_fn,
    )
    assert result.status == "done"
    assert result.phase == "done"
    assert len(result.dispatch_run_ids) == 2
    assert resets == [2]
    # First run: 1 coder+tester, 2 coder fail. Second: 2+3 coder+tester each.
    assert ( "coder", 2) in spawn.calls


def test_overnight_exhausted_when_max_retries_zero(tmp_path):
    def always_fail(step, issue, repo, *, cwd, baseline_note, **kw):
        return False, "nope"

    overnight = OvernightRun(
        overnight_id="ov2",
        sprint_label="sprint-1030",
        tickets=[5],
        repo="owner/repo",
        max_retries=0,
    )
    result = execute_overnight(
        overnight,
        repo_root=tmp_path,
        cwd=tmp_path,
        spawn=always_fail,
        verify=None,
        start_dispatch=_start_dispatch_factory(tmp_path, always_fail),
        reset_fn=lambda *a, **k: None,
    )
    assert result.status == "exhausted"
    assert result.phase == "exhausted"
    assert len(result.dispatch_run_ids) == 1
    # Failed leaf must not open a sprint PR (no sprint_branch set → None anyway).
    from services.sprint_manager.dispatch_runner import load_run
    leaf = load_run(result.dispatch_run_ids[0], tmp_path)
    assert leaf["status"] == "failed"
    assert leaf.get("sprint_pr_number") in (None, 0)


def test_overnight_stop_at_boundary(tmp_path):
    calls = []

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        calls.append((step, issue))
        # After first ticket completes, request stop before second dispatch would...
        # Actually stop is checked at start of loop and before retry.
        return True, "ok"

    overnight = OvernightRun(
        overnight_id="ov3",
        sprint_label="sprint-1030",
        tickets=[1],
        repo="owner/repo",
        max_retries=2,
    )
    # Pre-set stop flag before execute
    request_stop("ov3", tmp_path)
    # Need overnight file to exist for request_stop — create it
    from services.sprint_manager.overnight_runner import save_overnight
    save_overnight(overnight, tmp_path)
    request_stop("ov3", tmp_path)

    result = execute_overnight(
        overnight,
        repo_root=tmp_path,
        cwd=tmp_path,
        spawn=spawn,
        verify=None,
        start_dispatch=_start_dispatch_factory(tmp_path, spawn),
    )
    assert result.status == "stopped"
    assert calls == []  # stopped before first dispatch


def test_overnight_routes_registered():
    from fastapi.testclient import TestClient
    import server as srv

    client = TestClient(srv.app)
    assert client.get("/api/sprints/overnight/nope").status_code == 404
    assert client.get("/api/sprints/sprint-1030/overnight").status_code == 405
