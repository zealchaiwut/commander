"""Tests for summary-issue create retry (#79 board-stuck fix).

A single transient `gh` failure during sprint finish dropped sprint 79's
Executive Summary issue — and that issue is the only cross-machine "finished"
signal, so the sprint stayed on the board on both UAT and PRD. The create path
now retries transient failures before giving up.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import services.sprint_manager.sprint_manager as sm  # noqa: E402

REPO = "owner/repo"


def _mock_gc(create_side_effect):
    gc = MagicMock()
    gc.search_issues_by_title.return_value = []  # no existing dup
    gc.create_issue.side_effect = create_side_effect
    return gc


def test_create_retries_then_succeeds(monkeypatch):
    """Two transient failures then success -> issue created, 3 attempts."""
    gc = _mock_gc([RuntimeError("gh flaked"), RuntimeError("gh flaked again"),
                   (1219, "https://github.com/o/r/issues/1219")])
    monkeypatch.setattr(sm, "github_client", gc)
    monkeypatch.setattr(sm, "_ensure_github_labels", lambda *a, **k: None)
    monkeypatch.setattr(sm.subprocess, "run", MagicMock())

    with patch.object(sm.time, "sleep") as sleep:
        num, url = sm.create_summary_github_issue(
            content="# summary", sprint_number=79, sprint_label="sprint-79",
            repo_name=REPO,
        )

    assert num == 1219
    assert url.endswith("/1219")
    assert gc.create_issue.call_count == 3
    assert sleep.call_count == 2  # slept before each retry


def test_create_succeeds_first_try_no_sleep(monkeypatch):
    gc = _mock_gc([(1219, "https://github.com/o/r/issues/1219")])
    monkeypatch.setattr(sm, "github_client", gc)
    monkeypatch.setattr(sm, "_ensure_github_labels", lambda *a, **k: None)
    monkeypatch.setattr(sm.subprocess, "run", MagicMock())

    with patch.object(sm.time, "sleep") as sleep:
        num, _ = sm.create_summary_github_issue(
            content="# summary", sprint_number=79, sprint_label="sprint-79",
            repo_name=REPO,
        )

    assert num == 1219
    assert gc.create_issue.call_count == 1
    sleep.assert_not_called()


def test_create_returns_none_after_exhausting_retries(monkeypatch):
    """All attempts fail -> (None, None), exactly 3 tries."""
    gc = _mock_gc(RuntimeError("gh down"))
    monkeypatch.setattr(sm, "github_client", gc)
    monkeypatch.setattr(sm, "_ensure_github_labels", lambda *a, **k: None)
    monkeypatch.setattr(sm.subprocess, "run", MagicMock())

    with patch.object(sm.time, "sleep"):
        num, url = sm.create_summary_github_issue(
            content="# summary", sprint_number=79, sprint_label="sprint-79",
            repo_name=REPO,
        )

    assert num is None and url is None
    assert gc.create_issue.call_count == 3
