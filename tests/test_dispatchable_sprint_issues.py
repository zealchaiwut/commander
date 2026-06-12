"""Regression: sub-sprint runs must pick up SIT / needs-rework tickets (sprint-68.3)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager import sprint_manager as sm  # noqa: E402


def test_is_dispatchable_includes_sit_and_rework():
    assert sm._is_dispatchable({"SIT", "sprint-68.3", "size-S"})
    assert sm._is_dispatchable({"needs-rework", "sprint-68.3"})
    assert sm._is_dispatchable({"in-progress", "sprint-68.3"})
    assert not sm._is_dispatchable({"UAT", "sprint-68.3"})
    assert not sm._is_dispatchable({"UAT-approved", "sprint-68.3"})


def test_list_backlog_issues_filters_dispatchable(monkeypatch):
    fake = [
        {"number": 1, "title": "sit", "labels": [{"name": "SIT"}, {"name": "sprint-68.3"}]},
        {"number": 2, "title": "uat", "labels": [{"name": "UAT"}, {"name": "sprint-68.3"}]},
        {"number": 3, "title": "rework", "labels": [{"name": "needs-rework"}, {"name": "sprint-68.3"}]},
    ]
    monkeypatch.setattr(sm, "_list_labeled_open_issues", lambda *a, **k: fake)
    out = sm.list_backlog_issues("sprint-68.3")
    assert [i["number"] for i in out] == [1, 3]
