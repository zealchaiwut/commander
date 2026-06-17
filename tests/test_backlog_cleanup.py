"""Tests for backlog cleanup — test tickets and stale follow-ups."""
from __future__ import annotations

from apps.dashboard.routers import backlog_cleanup_service as svc


def test_is_test_issue_title_matches_uat_scaffolding():
    assert svc.is_test_issue_title("Test Ticket from UAT")
    assert svc.is_test_issue_title("Test Ticket")
    assert svc.is_test_issue_title("Test Issue")
    assert svc.is_test_issue_title("Test")
    assert not svc.is_test_issue_title("Add test coverage for sprint nav")


def test_follow_up_severity_reads_reviewer_body():
    body = "## Context\nFrom review\n\n## Severity\nnit"
    assert svc.follow_up_severity(body) == "nit"
    assert svc.follow_up_severity("## Severity\nsuggestion") == "suggestion"
    assert svc.follow_up_severity("no severity here") is None


def test_scan_backlog_finds_test_and_follow_up_candidates(monkeypatch):
    issues = [
        {
            "number": 100,
            "title": "Test Ticket from UAT",
            "state": "open",
            "labels": [{"name": "backlog"}],
            "body": "",
        },
        {
            "number": 101,
            "title": "[follow-up] Remove dead code",
            "state": "open",
            "labels": [],
            "body": "## Severity\nnit",
        },
        {
            "number": 102,
            "title": "[follow-up] Remove dead code",
            "state": "open",
            "labels": [],
            "body": "## Severity\nsuggestion",
        },
        {
            "number": 200,
            "title": "Real feature work",
            "state": "open",
            "labels": [],
            "body": "",
        },
        {
            "number": 50,
            "title": "Sprint ticket",
            "state": "open",
            "labels": [{"name": "sprint-85"}],
            "body": "",
        },
    ]

    monkeypatch.setattr(svc.gc, "list_open_issues_with_body", lambda **_: issues)
    monkeypatch.setattr(svc.gc, "classify_issue", lambda iss: "backlog")

    result = svc.scan_backlog("owner/repo")
    nums = {c["number"] for c in result["candidates"]}

    assert 100 in nums
    assert 101 in nums  # older duplicate + nit
    assert 102 not in nums  # newer duplicate kept
    assert 200 not in nums
    assert 50 not in nums
    assert result["counts"]["test_issue"] == 1
    assert result["counts"]["follow_up_redundant"] == 1
    assert result["counts"]["follow_up_low_impact"] == 0


def test_apply_backlog_cleanup_dry_run(monkeypatch):
    preview = {
        "candidates": [{"number": 10, "title": "Test", "category": "test_issue", "reason": "x"}],
    }
    monkeypatch.setattr(svc, "scan_backlog", lambda repo: preview)
    closed = []

    def _close(num, repo_name=None, reason=None):
        closed.append(num)

    monkeypatch.setattr(svc.gc, "close_issue", _close)

    out = svc.apply_backlog_cleanup("owner/repo", [10], dry_run=True)
    assert out["dry_run"] is True
    assert out["to_close"] == [10]
    assert closed == []
