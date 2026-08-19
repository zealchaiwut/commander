"""AC tests for the dispatch endpoint and queue consumer (issue #2315).

No live HTTP and no real agents: `spawn` is injected everywhere, so no
`claude` subprocess is ever started. Route registration is proved with an
in-process TestClient GET (405 = registered POST-only).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.dispatch_runner import (  # noqa: E402
    AGENT_PREAMBLE,
    DispatchRun,
    execute_run,
    load_run,
    request_stop,
    start_run,
    stop_flag_path,
)


class RecordingSpawn:
    """Records every (step, issue) it is asked to run."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on or set()

    def __call__(self, step, issue, repo, *, cwd, baseline_note, **kw):
        self.calls.append((step, issue))
        if (step, issue) in self.fail_on:
            return False, f"{step} failed on #{issue}"
        return True, "ok"


def _run(tickets, tmp_path, repo="owner/repo"):
    return DispatchRun(
        run_id="testrun", sprint_label="sprint-1027", tickets=list(tickets), repo=repo
    )


# --- AC: executes in the order given, never reordering --------------------

def test_tickets_run_in_the_order_given(tmp_path):
    spawn = RecordingSpawn()
    execute_run(_run([30, 10, 20], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spawn)

    issues_in_order = [issue for _step, issue in spawn.calls]
    # 30 before 10 before 20 — not sorted, not reversed.
    assert issues_in_order == [30, 30, 10, 10, 20, 20]


def test_each_ticket_runs_coder_then_tester(tmp_path):
    spawn = RecordingSpawn()
    execute_run(_run([7], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spawn)
    assert spawn.calls == [("coder", 7), ("tester", 7)]


# --- AC: stops on failure and records which ticket failed -----------------

def test_failure_stops_the_run_and_records_the_ticket(tmp_path):
    spawn = RecordingSpawn(fail_on={("coder", 10)})
    run = execute_run(_run([30, 10, 20], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spawn)

    assert run.status == "failed"
    assert run.failed_issue == 10
    # 20 must never be attempted — a broken ticket must not poison its dependents.
    assert 20 not in [issue for _s, issue in spawn.calls]


def test_tester_failure_also_stops_the_run(tmp_path):
    spawn = RecordingSpawn(fail_on={("tester", 30)})
    run = execute_run(_run([30, 40], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spawn)

    assert run.status == "failed"
    assert run.failed_issue == 30
    assert 40 not in [issue for _s, issue in spawn.calls]


# --- AC: observable without tailing a log ---------------------------------

def test_status_is_persisted_and_loadable(tmp_path):
    spawn = RecordingSpawn()
    run = execute_run(_run([5], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spawn)

    data = load_run(run.run_id, tmp_path)
    assert data is not None
    assert data["status"] == "done"
    assert data["sprint_label"] == "sprint-1027"
    assert [o["step"] for o in data["outcomes"]] == ["coder", "tester"]


def test_progress_is_visible_mid_run(tmp_path):
    """The status file must show the in-flight ticket, not only the final state."""
    seen = {}

    def spy(step, issue, repo, *, cwd, baseline_note, **kw):
        snapshot = load_run("testrun", tmp_path)
        seen[(step, issue)] = (snapshot["current_issue"], snapshot["current_step"])
        return True, "ok"

    execute_run(_run([9], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spy)
    assert seen[("coder", 9)] == (9, "coder")
    assert seen[("tester", 9)] == (9, "tester")


# --- AC: stoppable, and a stop leaves no half-labelled ticket -------------

def test_stop_is_honoured_at_the_next_step_boundary(tmp_path):
    calls = []

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        calls.append((step, issue))
        # Ask to stop while the first coder step is running.
        request_stop("testrun", tmp_path)
        return True, "ok"

    run = execute_run(_run([1, 2], tmp_path), repo_root=tmp_path, cwd=tmp_path, spawn=spawn)

    assert run.status == "stopped"
    # The in-flight step completed; the next one never started.
    assert calls == [("coder", 1)]
    assert run.current_issue is None and run.current_step is None


def test_request_stop_on_unknown_run_returns_false(tmp_path):
    assert request_stop("nope", tmp_path) is False
    assert not stop_flag_path("nope", tmp_path).exists()


# --- AC: agents receive the quality-bar instructions ----------------------

def test_agents_are_given_the_baseline_and_no_live_http_rules():
    """With gates deleted, the prompt is most of the remaining quality bar."""
    text = AGENT_PREAMBLE.format(baseline_note="X")
    assert "no NEW failures" in text
    assert "live HTTP" in text
    assert "TestClient" in text


def test_baseline_note_is_passed_through_to_the_agent(tmp_path):
    received = {}

    def spawn(step, issue, repo, *, cwd, baseline_note, **kw):
        received["note"] = baseline_note
        return True, "ok"

    execute_run(
        _run([1], tmp_path), repo_root=tmp_path, cwd=tmp_path,
        baseline_note="75 failed / 954 passed", spawn=spawn,
    )
    assert received["note"] == "75 failed / 954 passed"


# --- The three prohibitions -----------------------------------------------

def test_runner_never_mints_labels_or_writes_lifecycle_state():
    import ast

    src = (REPO_ROOT / "services" / "sprint_manager" / "dispatch_runner.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in (
                "transition", "ensure_sprint_label", "create_sprint_label_strict",
                "assign_sprint",
            ), f"dispatch must not call {node.attr}"


def test_runner_does_not_sort_or_reverse_tickets():
    """Assert on execute_run's AST, not on the file's text.

    A whole-file grep for `reversed(` produces false positives — parsing the
    agent result envelope legitimately reverses *output lines*, which has
    nothing to do with ticket order. What must hold is that the ticket sequence
    itself is never reordered.
    """
    import ast

    src = (REPO_ROOT / "services" / "sprint_manager" / "dispatch_runner.py").read_text()
    tree = ast.parse(src)

    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "execute_run"
    )

    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in ("sorted", "reversed"), (
                    f"execute_run must not reorder tickets: {node.func.id}()"
                )
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in ("sort", "reverse"), (
                    f"execute_run must not reorder tickets: .{node.func.attr}()"
                )


# --- start_run does not mutate the caller's list --------------------------

def test_start_run_preserves_the_given_ticket_list(tmp_path):
    spawn = RecordingSpawn()
    tickets = [3, 1, 2]
    run = start_run(
        "sprint-1027", tickets, repo="owner/repo", repo_root=tmp_path,
        cwd=tmp_path, spawn=spawn, background=False,
    )
    assert run.tickets == [3, 1, 2]
    assert tickets == [3, 1, 2], "caller's list must not be mutated"


# --- Route registration, proved without side effects ----------------------

@pytest.mark.parametrize(
    "path",
    [
        "/api/sprints/sprint-1027/dispatch",
        "/api/sprints/dispatch/abc123/stop",
    ],
)
def test_post_only_routes_are_registered(path):
    from fastapi.testclient import TestClient

    import server as srv

    client = TestClient(srv.app)
    resp = client.get(path)
    assert resp.status_code == 405, f"{path}: expected 405, got {resp.status_code}"


def test_unknown_run_id_is_404():
    from fastapi.testclient import TestClient

    import server as srv

    client = TestClient(srv.app)
    resp = client.get("/api/sprints/dispatch/definitely-not-a-run")
    assert resp.status_code == 404
