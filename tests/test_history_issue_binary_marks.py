"""History hist-irow marks: terminal sprints show check/cross only (issue #1041)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
HISTORY_JS = DASHBOARD_DIR / "static/src/sprint-board/history.js"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from routers import sprint_history_service as shs  # noqa: E402


def _fn_body(name: str) -> str:
    import re
    text = HISTORY_JS.read_text(encoding="utf-8")
    m = re.search(rf"function {re.escape(name)}\([^)]*\)\s*\{{", text)
    assert m, f"{name} not found"
    start = m.start()
    depth = 0
    for i, ch in enumerate(text[m.end() - 1 :], start=m.end() - 1):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError(f"unclosed {name}")


def test_hist_issue_chip_treats_agent_completed_as_merged():
    chip = _fn_body("_histIssueChip")
    assert "agent === 'completed'" in chip or "agent === \"completed\"" in chip
    assert "opts.binary" in chip


def test_hist_irow_uses_binary_icons_on_terminal_sprints():
    icon = _fn_body("_histIssueIcon")
    assert "_histSprintShowsBinaryIssues" in icon
    row = _fn_body("_histDoneIssueRowHtml")
    assert "_histIssueIcon(iss, s)" in row


def test_reconcile_promotes_agent_run_merged_over_open_uat():
    records = [{
        "label": "sprint-97.5",
        "project": "zealchaiwut/commander",
        "issues": [{
            "ticket_id": 818,
            "state": "open",
            "time_spent": 120,
        }],
    }]
    with patch.object(
        shs,
        "_issues_from_agent_runs",
        return_value=[{"ticket_id": 818, "state": "merged", "time_spent": None}],
    ):
        shs._reconcile_issue_outcomes_with_agent_runs(records)
    assert records[0]["issues"][0]["state"] == "merged"
