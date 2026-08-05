"""Tests for issue #2205: _build_outcome_inline must classify a ticket with a
recorded failure_reason as "failed", even if it eventually merged.

Bug: the per-issue classification checked state=="merged"/agent_status in
("completed","done") BEFORE checking failure_reason, so a ticket that merged
only after exhausting its fix-loop (state="merged" AND failure_reason set --
perf-coach sprint-121's #1420/#1525) was always bucketed "done", never
"failed". This made /api/board's own payload internally inconsistent:
outcome.counts.failed read 0 while the sibling finish_card.failed_count
(computed independently in _build_finish_card_inline, which already treats
failure_reason as authoritative) read 2 for the exact same sprint.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_DASHBOARD_ROOT), str(_DASHBOARD_ROOT / "routers")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_LABEL = "sprint-121"
_PROJECT = "owner/perf-coach"

_ROW = {
    "label": _LABEL,
    "project": _PROJECT,
    "state": "needs_rework",
    "run_ingested_at": "2026-07-22T11:47:00Z",
    "end_reason": "ticket-failures",
    "issues_json": json.dumps([
        {"ticket_id": 849, "state": "merged", "agent_status": "completed", "failure_reason": None},
        {"ticket_id": 901, "state": "merged", "agent_status": "completed", "failure_reason": None},
        {"ticket_id": 1420, "state": "merged", "agent_status": "completed",
         "failure_reason": "Fix-loop exhausted after 2 attempt(s) (attempt 1: LINT_FAIL; attempt 2: LINT_FAIL)"},
        {"ticket_id": 1525, "state": "merged", "agent_status": "completed",
         "failure_reason": "Fix-loop exhausted after 2 attempt(s) (attempt 1: LINT_FAIL; attempt 2: LINT_FAIL)"},
    ]),
    "wall_clock_secs": 6432,
    "summary_issue_url": None,
    "summary_issue_num": None,
    "pr_number": 1591,
}


def _import_board_service():
    if "board_service" in sys.modules:
        return sys.modules["board_service"]
    spec = importlib.util.find_spec("board_service")
    bs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bs)
    sys.modules["board_service"] = bs
    return bs


def test_outcome_inline_counts_failed_tickets_that_later_merged(monkeypatch):
    bs = _import_board_service()
    mock_db = MagicMock()
    mock_db.get_sprint.return_value = dict(_ROW)
    mock_db.canonical_lifecycle.side_effect = lambda raw: (
        __import__("db").canonical_lifecycle(raw)
    )
    monkeypatch.setattr(bs, "db", mock_db)

    outcome = bs._build_outcome_inline(_LABEL, _PROJECT, "needs_rework")

    assert outcome is not None
    assert outcome["counts"]["failed"] == 2, (
        f"outcome.counts.failed must count tickets with a recorded "
        f"failure_reason regardless of eventual merge state, got {outcome['counts']!r}"
    )
    assert outcome["counts"]["done"] == 2, f"got {outcome['counts']!r}"

    failed_numbers = {i["number"] for i in outcome["issues"] if i["outcome"] == "failed"}
    assert failed_numbers == {1420, 1525}, (
        f"Expected #1420/#1525 classified as failed, got {failed_numbers!r} "
        f"from issues={outcome['issues']!r}"
    )
