"""Activity log: colorful entity chips + ticket_failed error surfacing."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text()


class TestActivityLogUiMarkers:
    """project.html renders colorful chips and error panels in the Activity tab."""

    def test_error_badge_css(self):
        assert ".lr-badge.error" in PROJECT_HTML
        assert "#ef4444" in PROJECT_HTML or "var(--red)" in PROJECT_HTML

    def test_entity_chip_classes(self):
        for cls in ("evl-chip--issue", "evl-chip--sprint", "evl-chip--branch", "evl-chip--agent"):
            assert cls in PROJECT_HTML, f"missing {cls}"

    def test_error_row_and_panel(self):
        assert "evl-row-error" in PROJECT_HTML
        assert "evl-error-panel" in PROJECT_HTML
        assert "ticket_failed" in PROJECT_HTML

    def test_sprint_lifecycle_renderers(self):
        for t in ("sprint_started", "sprint_finished", "sprint_rerun", "ticket_dispatched"):
            assert t in PROJECT_HTML

    def test_error_action_buttons(self):
        assert "evlOpenIssueLog" in PROJECT_HTML
        assert "Re-dispatch coder" in PROJECT_HTML

    def test_agent_sprint_combined_filter(self):
        assert "_evlMatchesFilter" in PROJECT_HTML
        assert "sprint_" in PROJECT_HTML


class TestTicketFailedEmission:
    """sprint_manager emits ticket_failed with investigation-friendly detail."""

    def test_failure_event_detail_reads_sidecar(self, tmp_path):
        import services.sprint_manager.sprint_manager as sm

        runtime = tmp_path / ".commander" / "runtime"
        runtime.mkdir(parents=True)
        sc = runtime / "last-failure-821.json"
        sc.write_text(json.dumps({
            "failure_class": "gate_failed",
            "gate": "pytest",
            "failures": [{
                "type": "AssertionError",
                "location": "tests/test_x.py:64",
                "issue": "expected 100, got 96",
            }],
        }), encoding="utf-8")

        cfg = sm.SprintConfig()
        cfg.worktree_coder = tmp_path / "work-coder"
        cfg.worktree_coder.mkdir(parents=True, exist_ok=True)

        with patch.object(sm, "_find_feature_branch", return_value="feat/history-stats-0612"):
            detail = sm._failure_event_detail(
                821, "tester", "gate failed: pytest", "gate_failed",
                cfg=cfg, gate=True, sprint_label="sprint-68.5",
            )

        assert detail["issue_num"] == 821
        assert detail["branch"] == "feat/history-stats-0612"
        assert detail["error_type"] == "AssertionError"
        assert detail["location"] == "tests/test_x.py:64"
        assert detail["gate_name"] == "pytest"
        assert detail["sprint_label"] == "sprint-68.5"

    def test_emit_ticket_failed_records_event(self):
        import services.sprint_manager.sprint_manager as sm

        mock_record = MagicMock()
        with patch.object(sm, "_db_record_event", mock_record):
            with patch.object(sm, "_RECORD_EVENT_AVAILABLE", True):
                sm._emit_ticket_failed(
                    821, "tester", "pytest exit 1", "gate_failed",
                    project="owner/repo", action_id="run-1", gate=True,
                    sprint_label="sprint-68.5",
                )

        mock_record.assert_called_once()
        kwargs = mock_record.call_args.kwargs
        assert kwargs["type"] == "ticket_failed"
        assert kwargs["target"] == "#821"
        assert kwargs["source"] == "agent"
        assert kwargs["detail"]["agent"] == "TESTER"
        assert kwargs["detail"]["issue_num"] == 821
