"""Tests for issue #893: Keep NULL issue_number distinct instead of collapsing to bucket 0"""
import os
import sys
from pathlib import Path

import pytest

# Add dashboard to path so we can import services
dashboard_root = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
if str(dashboard_root) not in sys.path:
    sys.path.insert(0, str(dashboard_root))


def test_null_issue_number__skips_rows_with_none_issue(monkeypatch):
    """Verify that rows with NULL issue_number are skipped (not collapsed to 0)."""
    from routers import logs_stats_service

    # Mock _db().agent_runs_for_sprint to return rows with NULL issue_number
    class MockDB:
        def agent_runs_for_sprint(self, sprint_label):
            return [
                {"issue_number": None, "agent": "coder", "outcome": "success", "duration_seconds": 100, "total_tokens": 1000},
                {"issue_number": 1, "agent": "coder", "outcome": "success", "duration_seconds": 50, "total_tokens": 500},
                {"issue_number": None, "agent": "tester", "outcome": "success", "duration_seconds": 60, "total_tokens": 600},
                {"issue_number": 2, "agent": "tester", "outcome": "success", "duration_seconds": 40, "total_tokens": 400},
            ]

    def mock_db():
        return MockDB()

    # Patch the _db function
    monkeypatch.setattr(logs_stats_service, "_db", mock_db)

    # Call the function
    result = logs_stats_service.ticket_stats("sprint-69", project="commander")

    # Verify that NULL issue_number rows were skipped
    # Result should only have issues 1 and 2, not 0
    tickets = result["tickets"]
    issues = [t["issue_number"] for t in tickets]
    assert 0 not in issues, f"Issue 0 should not exist; got {issues}"
    assert set(issues) == {1, 2}, f"Expected issues {{1, 2}}, got {set(issues)}"

    # Verify that the coder duration for issue 1 is 50 (not 50+100)
    issue_1 = next(t for t in tickets if t["issue_number"] == 1)
    assert issue_1["coder_seconds"] == 50, f"Issue 1 coder duration should be 50 (NULL row skipped), got {issue_1['coder_seconds']}"


def test_null_issue_number__valid_rows_aggregated(monkeypatch):
    """Verify that valid (non-NULL) issue_number rows are still aggregated correctly."""
    from routers import logs_stats_service

    class MockDB:
        def agent_runs_for_sprint(self, sprint_label):
            return [
                {"issue_number": 5, "agent": "coder", "outcome": "success", "duration_seconds": 100, "total_tokens": 1000},
                {"issue_number": 5, "agent": "coder", "outcome": "success", "duration_seconds": 50, "total_tokens": 500},
                {"issue_number": 5, "agent": "tester", "outcome": "success", "duration_seconds": 60, "total_tokens": 600},
            ]

    def mock_db():
        return MockDB()

    monkeypatch.setattr(logs_stats_service, "_db", mock_db)

    result = logs_stats_service.ticket_stats("sprint-69", project="commander")

    # Should have one entry for issue 5
    tickets = result["tickets"]
    assert len(tickets) == 1
    assert tickets[0]["issue_number"] == 5

    # Coder duration should be 100+50=150, tester should be 60, tokens should be 1000+500+600=2100
    assert tickets[0]["coder_seconds"] == 150, f"Coder duration should be 150, got {tickets[0]['coder_seconds']}"
    assert tickets[0]["tester_seconds"] == 60, f"Tester duration should be 60, got {tickets[0]['tester_seconds']}"
    assert tickets[0]["total_tokens"] == 2100, f"Total tokens should be 2100, got {tickets[0]['total_tokens']}"
