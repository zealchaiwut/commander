"""
Tests for issue #2305: Sign-off must operate per sprint, not repo-wide.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

AC1  A per-sprint sign-off action closes exactly the UAT tickets carrying that
     sprint label, and no others.
AC2  It reports what it will close before acting (count + issue numbers) so
     the operator can see the scope — and takes no action while doing so.
AC3  Tickets from other sprints are provably untouched — behavioral test with
     two sprint labels open simultaneously asserts only the target sprint
     closes.
AC4  Child sprint labels are handled: signing off sprint-N must include
     sprint-N.1, .2, .3.
AC5  Sprint Executive Summary issues are closed with their sprint rather than
     left as orphans on the board.
AC6  The repo-wide approve-batch gains a dry-run step.

No live HTTP calls are made — github_client's mirror-read layer and its write
operations (approve_issue) are monkeypatched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
import github_client  # noqa: E402
from routers import signoff_service  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue(number, labels, state="open", title=None):
    return {
        "number": number,
        "title": title or f"Ticket #{number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "state": state,
        "labels": [{"name": lbl} for lbl in labels],
    }


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temp project root wired into server._project_root_path."""
    root = tmp_path / "proj"
    (root / ".commander" / "sprints").mkdir(parents=True)

    def _root(_repo):
        return root

    for mod in {server, sys.modules.get("server")}:
        if mod is not None:
            monkeypatch.setattr(mod, "_project_root_path", _root)
    return root


@pytest.fixture
def approve_calls(monkeypatch):
    """Capture github_client.approve_issue calls instead of hitting GitHub."""
    calls = []

    def _fake_approve(issue_id, repo_name=None):
        calls.append(issue_id)

    monkeypatch.setattr(github_client, "approve_issue", _fake_approve)
    monkeypatch.setattr(server.github_client, "approve_issue", _fake_approve)
    return calls


def _set_mirror(monkeypatch, issues):
    """Exercise the real list_open_uat_issues_by_sprint_label filter by
    monkeypatching only the mirror-read primitive, not the filter itself."""
    monkeypatch.setattr(github_client, "_mirror_issues", lambda repo_name: issues)


# ---------------------------------------------------------------------------
# AC1 + AC3: closes exactly the target sprint's UAT tickets, others untouched
# ---------------------------------------------------------------------------

def test_ac1_ac3_signoff_closes_only_target_sprint_tickets(project, approve_calls, monkeypatch):
    """With two sprints holding open UAT tickets simultaneously, signing off
    one closes only its tickets — the other sprint's tickets are untouched."""
    issues = [
        _issue(101, ["UAT", "sprint-10"]),
        _issue(102, ["UAT", "sprint-10"]),
        _issue(201, ["UAT", "sprint-11"]),
        _issue(301, ["SIT", "sprint-10"]),  # not UAT — must not be touched either
    ]
    _set_mirror(monkeypatch, issues)

    result = signoff_service.uat_signoff_apply("owner/repo", "sprint-10")

    assert sorted(result["approved"]) == [101, 102]
    assert sorted(approve_calls) == [101, 102]
    # The other sprint's ticket, and the non-UAT ticket, were never approved.
    assert 201 not in approve_calls
    assert 301 not in approve_calls


# ---------------------------------------------------------------------------
# AC2: preview reports count + issue numbers, and takes no action
# ---------------------------------------------------------------------------

def test_ac2_preview_reports_scope_without_mutating(project, monkeypatch):
    issues = [
        _issue(101, ["UAT", "sprint-10"]),
        _issue(102, ["UAT", "sprint-10"]),
        _issue(201, ["UAT", "sprint-11"]),
    ]
    _set_mirror(monkeypatch, issues)

    def _boom(*a, **k):
        raise AssertionError("preview must not close any tickets")

    monkeypatch.setattr(github_client, "approve_issue", _boom)
    monkeypatch.setattr(server.github_client, "approve_issue", _boom)

    result = signoff_service.uat_signoff_preview("owner/repo", "sprint-10")

    assert result["count"] == 2
    assert sorted(i["number"] for i in result["issues"]) == [101, 102]


# ---------------------------------------------------------------------------
# AC4: child sprint labels are included
# ---------------------------------------------------------------------------

def test_ac4_child_sprint_labels_included(project, approve_calls, monkeypatch):
    issues = [
        _issue(101, ["UAT", "sprint-10"]),
        _issue(102, ["UAT", "sprint-10.1"]),
        _issue(103, ["UAT", "sprint-10.2"]),
        _issue(201, ["UAT", "sprint-101"]),  # similar prefix, NOT a child — must not match
    ]
    _set_mirror(monkeypatch, issues)

    result = signoff_service.uat_signoff_apply("owner/repo", "sprint-10")

    assert sorted(result["approved"]) == [101, 102, 103]
    assert 201 not in approve_calls


def test_ac4_preview_includes_child_labels_flag(project, monkeypatch):
    _set_mirror(monkeypatch, [_issue(101, ["UAT", "sprint-10.1"])])
    result = signoff_service.uat_signoff_preview("owner/repo", "sprint-10")
    assert result["child_labels_included"] is True
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# AC5: Sprint Executive Summary issues are closed with their sprint
# ---------------------------------------------------------------------------

def test_ac5_executive_summary_issue_closes_with_its_sprint(project, approve_calls, monkeypatch):
    """AC5: 'Sprint Executive Summary issues are closed with their sprint
    rather than left as orphans on the board.'

    startup.py's own `_finished_sprint_summaries` docstring documents that a
    real Executive Summary issue is labeled `sprint-summary`/`docs`, NOT the
    sprint-N label — the sprint number lives only in the issue title, which
    is exactly why that function parses titles instead of labels. This test
    models a real summary issue that way and expects it to close alongside
    its sprint's tickets.
    """
    summary_issue = _issue(
        999, ["sprint-summary", "docs", "UAT"], title="Sprint 10 Executive Summary"
    )
    ticket_issue = _issue(101, ["UAT", "sprint-10"])
    _set_mirror(monkeypatch, [summary_issue, ticket_issue])

    result = signoff_service.uat_signoff_apply("owner/repo", "sprint-10")

    assert 101 in result["approved"]
    assert 999 in result["approved"], (
        "Executive Summary issue was not closed with its sprint — it carries "
        "sprint-summary/docs labels rather than the sprint-N label (per "
        "startup.py:_finished_sprint_summaries), so "
        "list_open_uat_issues_by_sprint_label's label-only filter never "
        "matches it and it is left orphaned on the board."
    )


# ---------------------------------------------------------------------------
# AC6: repo-wide approve-batch gains a dry-run step
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    # TestClient's request.client.host is "testclient", not a real loopback
    # address, so it doesn't hit the auth middleware's 127.0.0.1 exemption.
    # Auth is orthogonal to this ticket's behavior — disable the gate for it.
    monkeypatch.delenv("COMMANDER_API_TOKEN", raising=False)
    return TestClient(server.app)


def test_ac6_approve_batch_dry_run_previews_without_closing(client, monkeypatch):
    issues = [
        {"number": 1, "title": "One"},
        {"number": 2, "title": "Two"},
    ]
    monkeypatch.setattr(
        server.github_client, "list_open_uat_issues",
        lambda repo_name=None: issues,
    )

    def _boom(*a, **k):
        raise AssertionError("dry_run must not close any tickets")

    monkeypatch.setattr(server.github_client, "approve_issue", _boom)

    resp = client.post("/api/projects/owner/repo/approve-batch?dry_run=true")

    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True
    assert body["count"] == 2
    assert sorted(i["number"] for i in body["would_approve"]) == [1, 2]


def test_ac6_approve_batch_without_dry_run_still_closes(client, monkeypatch):
    """Regression guard: the default (no dry_run) behavior is unchanged."""
    issues = [{"number": 1, "title": "One"}]
    monkeypatch.setattr(
        server.github_client, "list_open_uat_issues",
        lambda repo_name=None: issues,
    )
    approved = []
    monkeypatch.setattr(
        server.github_client, "approve_issue",
        lambda number, repo_name=None: approved.append(number),
    )

    resp = client.post("/api/projects/owner/repo/approve-batch")

    assert resp.status_code == 200
    assert approved == [1]


# ---------------------------------------------------------------------------
# Endpoint-level wiring: uat-preview / uat-signoff routes exist and respond
# ---------------------------------------------------------------------------

def test_uat_preview_endpoint_returns_scope(client, project, monkeypatch):
    _set_mirror(monkeypatch, [_issue(101, ["UAT", "sprint-10"])])
    resp = client.get("/api/sprints/sprint-10/uat-preview?project=owner/repo")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["issues"][0]["number"] == 101


def test_uat_signoff_endpoint_closes_scope(client, project, approve_calls, monkeypatch):
    _set_mirror(monkeypatch, [_issue(101, ["UAT", "sprint-10"])])
    resp = client.post(
        "/api/sprints/sprint-10/uat-signoff",
        json={"project": "owner/repo"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approved"] == [101]
    assert approve_calls == [101]


def test_uat_signoff_rejects_invalid_sprint_label(client, project):
    resp = client.post(
        "/api/sprints/not-a-sprint-label/uat-signoff",
        json={"project": "owner/repo"},
    )
    assert resp.status_code == 400


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
