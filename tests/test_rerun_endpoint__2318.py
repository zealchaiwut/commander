"""AC tests for the restored rerun endpoint (issue #2318).

The three prohibitions carried from #2311 are asserted directly, because they
are the reason the old rerun was deleted:

  * no child sprint labels minted
  * no ticket reordering
  * no sprint lifecycle writes

No live HTTP: a fake github_client is injected, and the route's existence is
proved with an in-process GET (405 = registered POST-only, 404 = gone) rather
than by POSTing to anything real.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.ticket_retry import (  # noqa: E402
    issues_for_label,
    reset_sprint,
    reset_ticket,
)


class FakeGH:
    """Records every call so prohibited operations can be asserted absent."""

    def __init__(self, issues):
        self._issues = issues
        self.update_labels_calls = []
        self.other_calls = []

    def list_issues(self, sprint, repo_name=None):
        self.other_calls.append(("list_issues", sprint))
        return self._issues

    def list_all_open_issues(self, repo_name=None, limit=200):
        self.other_calls.append(("list_all_open_issues", limit))
        return self._issues

    def update_labels(self, issue, add=None, remove=None, repo_name=None):
        self.update_labels_calls.append(
            {"issue": issue, "add": list(add or []), "remove": list(remove or [])}
        )

    # Prohibited operations — calling any of these is a test failure.
    def ensure_sprint_label(self, *a, **k):
        raise AssertionError("must not mint a sprint label")

    def create_sprint_label_strict(self, *a, **k):
        raise AssertionError("must not mint a sprint label")

    def assign_sprint(self, *a, **k):
        raise AssertionError("must not reassign / reorder sprints")


def _issue(num, labels):
    return {"number": num, "labels": [{"name": n} for n in labels]}


# --- AC: resets failed tickets, reports what changed ----------------------

def test_reset_sprint_resets_failed_tickets(tmp_path):
    gh = FakeGH([
        _issue(1, ["sprint-1027", "needs-rework"]),
        _issue(2, ["sprint-1027", "needs-rework"]),
        _issue(3, ["sprint-1027", "UAT"]),
    ])
    result = reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)

    assert result.reset == [1, 2]
    assert [s["issue"] for s in result.skipped] == [3]
    assert "#1" in result.summary() and "#2" in result.summary()


# --- AC: never mints child sprint labels ----------------------------------

def test_no_child_sprint_label_is_minted(tmp_path):
    gh = FakeGH([_issue(1, ["sprint-1027", "needs-rework"])])
    reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)

    for call in gh.update_labels_calls:
        for name in call["add"]:
            assert not name.startswith("sprint-"), f"minted a sprint label: {name}"
    # The FakeGH label-creating methods raise, so reaching here proves none ran.


# --- AC: never reorders tickets -------------------------------------------

def test_tickets_are_visited_in_the_order_given(tmp_path):
    gh = FakeGH([
        _issue(30, ["sprint-1027", "needs-rework"]),
        _issue(10, ["sprint-1027", "needs-rework"]),
        _issue(20, ["sprint-1027", "needs-rework"]),
    ])
    result = reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)

    # Order is preserved exactly as returned — not sorted, not reversed.
    assert result.reset == [30, 10, 20]
    assert [c["issue"] for c in gh.update_labels_calls] == [30, 10, 20]


# --- AC: never writes sprint lifecycle state ------------------------------

def test_no_lifecycle_transition_is_called(tmp_path, monkeypatch):
    import services.sprint_manager.state_machine as sm

    def _boom(*a, **k):
        raise AssertionError("must not write sprint lifecycle state")

    monkeypatch.setattr(sm, "transition", _boom, raising=False)

    gh = FakeGH([_issue(1, ["sprint-1027", "needs-rework"])])
    reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)


def test_source_never_imports_state_machine():
    """Assert on the AST, not the text — the docstring names it deliberately."""
    import ast

    src = (REPO_ROOT / "services" / "sprint_manager" / "ticket_retry.py").read_text()
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "state_machine" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            assert "state_machine" not in (node.module or "")
            for alias in node.names:
                assert "state_machine" not in alias.name
        elif isinstance(node, ast.Attribute):
            assert node.attr != "transition", "must not call a lifecycle transition"


# --- AC: idempotent -------------------------------------------------------

def test_calling_twice_is_harmless(tmp_path):
    gh = FakeGH([_issue(1, ["sprint-1027", "needs-rework"])])
    first = reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)
    assert first.reset == [1]

    # Second call sees the ticket already dispatchable.
    gh2 = FakeGH([_issue(1, ["sprint-1027", "backlog"])])
    second = reset_sprint("sprint-1027", github_client=gh2, repo_root=tmp_path)
    assert second.reset == []
    assert second.is_noop is True
    assert gh2.update_labels_calls == []


# --- AC: a sprint with nothing failed is a clear no-op, not an error ------

def test_nothing_failed_is_a_noop_not_an_error(tmp_path):
    gh = FakeGH([_issue(1, ["sprint-1027", "UAT"]), _issue(2, ["sprint-1027", "backlog"])])
    result = reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)

    assert result.is_noop is True
    assert result.reset == []
    assert "nothing to reset" in result.summary()
    assert result.to_dict()["noop"] is True


# --- AC: reports what it changed and what it skipped ----------------------

def test_result_reports_skips_with_reasons(tmp_path):
    gh = FakeGH([_issue(5, ["sprint-1027", "SIT"])])
    result = reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path)

    assert result.skipped == [{"issue": 5, "reason": "not in needs-rework"}]


# --- AC: does not dispatch ------------------------------------------------

def test_reset_does_not_dispatch():
    """Resetting and running are separate calls by design."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "ticket_retry.py").read_text()
    for forbidden in ("subprocess", "dispatch(", "Popen", "claude -p"):
        assert forbidden not in src, f"reset path must not dispatch: found {forbidden!r}"


# --- Sidecar --------------------------------------------------------------

def test_sidecar_is_cleared(tmp_path):
    sidecar = tmp_path / ".commander" / "runtime" / "last-failure-1.json"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("{}", encoding="utf-8")

    gh = FakeGH([_issue(1, ["sprint-1027", "needs-rework"])])
    result = reset_ticket(
        1, github_client=gh, repo_root=tmp_path, labels=["sprint-1027", "needs-rework"]
    )

    assert result.sidecar_cleared is True
    assert not sidecar.exists()


# --- dry-run changes nothing ----------------------------------------------

def test_dry_run_makes_no_calls(tmp_path):
    gh = FakeGH([_issue(1, ["sprint-1027", "needs-rework"])])
    result = reset_sprint("sprint-1027", github_client=gh, repo_root=tmp_path, dry_run=True)

    assert result.reset == [1]
    assert gh.update_labels_calls == [], "dry-run must not edit labels"
    assert "Would reset" in result.summary()


# --- Non-numeric sprint labels --------------------------------------------

def test_child_label_falls_back_instead_of_raising(tmp_path):
    """sprint-1.1 is not int-parseable; historical child labels still exist."""
    gh = FakeGH([_issue(1, ["sprint-1.1", "needs-rework"])])
    found = issues_for_label(gh, "sprint-1.1")
    assert [i["number"] for i in found] == [1]
    assert ("list_all_open_issues", 200) in gh.other_calls


# --- Route registration, proved without side effects ----------------------

def test_rerun_route_is_registered_post_only():
    """GET a POST-only route: 405 means registered, 404 means gone."""
    from fastapi.testclient import TestClient

    import server as srv

    # Constructed without a `with` block on purpose — entering the lifespan hangs.
    client = TestClient(srv.app)
    resp = client.get("/api/sprints/sprint-1027/rerun")
    assert resp.status_code == 405, f"expected 405 (registered POST-only), got {resp.status_code}"
