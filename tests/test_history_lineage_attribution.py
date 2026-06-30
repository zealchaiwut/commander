"""History lineage issue ownership (sprint-97.1 / sprint-97.2 sibling model).

Rerun children are flat siblings (sprint-N.M), not nested sprint-N.M.K under
sprint-N.M. Attribution must use _label_base + sub-index, and ran_by_label must
be built from the full feed so off-window siblings still suppress duplicates.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from routers import sprint_history_service as h  # noqa: E402


def _fake_db(runs: dict[str, list[dict]]):
    class _D:
        def agent_runs_for_sprint(self, label, project=None):
            return runs.get(label, [])

    return lambda: _D()


def test_later_sibling_uses_flat_lineage_not_nested_prefix():
    """sprint-97.2 is a sibling of sprint-97.1, not a child of sprint-97.1."""
    later = h._ran_in_later_lineage_siblings(
        "sprint-97.1",
        {
            "sprint-97.1": {867},
            "sprint-97.2": {867, 872},
            "sprint-97.1.1": {999},  # wrong nested artifact — must not match
        },
    )
    assert later == {867, 872}
    assert 999 not in later


def test_attribute_drops_ticket_rerun_in_later_sibling(monkeypatch):
    monkeypatch.setattr(
        h,
        "_db",
        _fake_db({
            "sprint-97.1": [{"issue_number": 820}, {"issue_number": 867}],
            "sprint-97.2": [{"issue_number": 867}, {"issue_number": 872}],
        }),
    )
    recs = [
        {
            "label": "sprint-97.1",
            "project": "zealchaiwut/commander",
            "lifecycle_state": "completed",
            "issues": [{"ticket_id": 820}, {"ticket_id": 867}],
        },
        {
            "label": "sprint-97.2",
            "project": "zealchaiwut/commander",
            "lifecycle_state": "needs_rework",
            "issues": [{"ticket_id": 820}, {"ticket_id": 867}, {"ticket_id": 872}],
        },
    ]
    ran = h._build_ran_by_label(recs)
    h._attribute_issues_to_runs(recs, ran)
    assert [i["ticket_id"] for i in recs[0]["issues"]] == [820]
    assert sorted(i["ticket_id"] for i in recs[1]["issues"]) == [867, 872]


def test_full_feed_ran_map_suppresses_off_window_rerun(monkeypatch):
    """Window row for 97.1 must see #867 in sprint-97.3 even when 97.3 is off-page."""
    monkeypatch.setattr(
        h,
        "_db",
        _fake_db({
            "sprint-97.1": [{"issue_number": 867}],
            "sprint-97.3": [{"issue_number": 867}],
        }),
    )
    full = [
        {
            "label": "sprint-97.1",
            "project": "p",
            "lifecycle_state": "completed",
            "issues": [{"ticket_id": 867}],
        },
        {
            "label": "sprint-97.3",
            "project": "p",
            "lifecycle_state": "running",
            "issues": [{"ticket_id": 867}],
        },
    ]
    window = [full[0]]
    ran = h._build_ran_by_label(full)
    h._attribute_issues_to_runs(window, ran)
    assert window[0]["issues"] == []
