"""Tests for issue #689 — Replace bare except in token-usage cost loop.

AC coverage:
  AC1 — TypeError in per-row calculation is caught specifically (not bare Exception)
        and a debug log is emitted; other rows are still processed.
  AC2 — ValueError in per-row calculation is caught specifically and logged at debug.
  AC3 — DB query failure is caught and logged at debug; cost fields default to 0.
  AC4 — Valid rows are still accumulated correctly after the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"

for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _make_sprint_state(project_root: Path, sprint_label: str) -> None:
    """Create a minimal sprint state file so _compute_analytics_metrics has something to parse."""
    import json
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": sprint_label,
        "issues": [
            {
                "number": 1,
                "title": "Test issue",
                "status": "uat",
                "coder_finished_at": "2026-01-10T11:00:00Z",
                "coder_started_at": "2026-01-10T10:00:00Z",
                "tester_finished_at": "2026-01-10T12:00:00Z",
                "tester_started_at": "2026-01-10T11:00:00Z",
                "tester_attempt_count": 1,
            }
        ],
        "wall_clock_secs": 86400.0,
        "start_timestamp": "2026-01-10T10:00:00Z",
        # Cost = status-file token totals × blended price from the token_usage
        # join (see _compute_analytics_metrics). The state file is the token
        # source of truth; without these, cost is 0 by design. SprintStats
        # totals are populated per dispatch since the token-tracking fix.
        "total_tokens_in": 1_000_000,
        "total_tokens_out": 0,
    }
    # The metrics reader globs sprint-*-state.json (the SprintStats save name),
    # not -status.json — the old name here meant the state was never read and
    # every cost assertion compared against 0.
    (sprints_dir / f"{sprint_label}-state.json").write_text(
        __import__("json").dumps(state)
    )


def _call_metrics(project_root: Path, token_rows: list[dict]):
    """Call _compute_analytics_metrics with mocked DB rows."""
    import server as srv

    with (
        patch("server.db.get_token_usage_by_agent_model", return_value=token_rows),
    ):
        return srv._compute_analytics_metrics(project_root)


# ---------------------------------------------------------------------------
# AC1 — TypeError in per-row cost calculation is caught specifically and logged
# ---------------------------------------------------------------------------

class TestTypeErrorInRow:
    def test_debug_log_emitted_on_type_error(self, tmp_path):
        """AC1: TypeError in row cost calc triggers a debug log, not silent swallow."""
        _make_sprint_state(tmp_path, "sprint-1")
        bad_row = {
            "agent_role": "coder",
            "model_name": "claude-haiku-4-5",
            "total_input": "not-a-number",  # TypeError on multiply
            "total_output": 0,
        }
        import server as srv
        with (
            patch("server.db.get_token_usage_by_agent_model", return_value=[bad_row]),
            patch.object(srv.logger, "debug") as mock_debug,
        ):
            srv._compute_analytics_metrics(tmp_path)
        mock_debug.assert_called()

    def test_remaining_rows_still_processed_after_type_error(self, tmp_path):
        """AC1: bad row is skipped; subsequent valid row still contributes to cost."""
        _make_sprint_state(tmp_path, "sprint-1")
        bad_row = {
            "agent_role": "coder",
            "model_name": "claude-haiku-4-5",
            "total_input": "not-a-number",
            "total_output": 0,
        }
        good_row = {
            "agent_role": "coder",
            "model_name": "claude-haiku-4-5",
            "total_input": 1_000_000,
            "total_output": 0,
        }
        import server as srv
        with patch("server.db.get_token_usage_by_agent_model", return_value=[bad_row, good_row]):
            result = srv._compute_analytics_metrics(tmp_path)

        cost_total = result["cost"]["per_sprint"]["total"]
        assert cost_total > 0, (
            "Good row after bad row must still contribute to cost total; got 0. "
            "This means the entire loop was aborted rather than just the bad row skipped."
        )


# ---------------------------------------------------------------------------
# AC2 — ValueError in per-row calculation is caught specifically and logged
# ---------------------------------------------------------------------------

class TestValueErrorInRow:
    def test_debug_log_emitted_on_value_error(self, tmp_path):
        """AC2: ValueError in row calc triggers a debug log."""
        _make_sprint_state(tmp_path, "sprint-1")

        import server as srv

        original_map = srv.MODEL_PRICE_MAP

        # Patch MODEL_PRICE_MAP so .get() returns a tuple that raises ValueError
        # when unpacked (wrong arity).
        bad_price_map = MagicMock()
        bad_price_map.get.return_value = (1.0,)  # 1-tuple → ValueError on unpack

        with (
            patch("server.db.get_token_usage_by_agent_model", return_value=[{
                "agent_role": "coder",
                "model_name": "claude-haiku-4-5",
                "total_input": 100,
                "total_output": 50,
            }]),
            patch("server.MODEL_PRICE_MAP", bad_price_map),
            patch.object(srv.logger, "debug") as mock_debug,
        ):
            srv._compute_analytics_metrics(tmp_path)

        mock_debug.assert_called()


# ---------------------------------------------------------------------------
# AC3 — DB query failure is caught and logged at debug; cost fields default to 0
# ---------------------------------------------------------------------------

class TestDbQueryFailure:
    def test_debug_log_emitted_on_db_failure(self, tmp_path):
        """AC3: exception from get_token_usage_by_agent_model triggers debug log."""
        _make_sprint_state(tmp_path, "sprint-1")
        import server as srv
        with (
            patch("server.db.get_token_usage_by_agent_model", side_effect=RuntimeError("db down")),
            patch.object(srv.logger, "debug") as mock_debug,
        ):
            srv._compute_analytics_metrics(tmp_path)
        mock_debug.assert_called()

    def test_cost_defaults_to_zero_on_db_failure(self, tmp_path):
        """AC3: when DB fails, cost fields are 0, not an unhandled exception."""
        _make_sprint_state(tmp_path, "sprint-1")
        import server as srv
        with patch("server.db.get_token_usage_by_agent_model", side_effect=RuntimeError("db down")):
            result = srv._compute_analytics_metrics(tmp_path)

        assert result["cost"]["per_sprint"]["total"] == 0.0, (
            "cost.per_sprint.total must be 0 when DB query fails"
        )


# ---------------------------------------------------------------------------
# AC4 — Valid rows are still accumulated correctly
# ---------------------------------------------------------------------------

class TestValidRowsAccumulated:
    def test_valid_coder_row_contributes_to_cost(self, tmp_path):
        """AC4: valid coder row is accumulated into cost.per_sprint and cost.per_ticket."""
        _make_sprint_state(tmp_path, "sprint-1")
        valid_row = {
            "agent_role": "coder",
            "model_name": "claude-haiku-4-5",
            "total_input": 1_000_000,
            "total_output": 0,
        }
        import server as srv
        with patch("server.db.get_token_usage_by_agent_model", return_value=[valid_row]):
            result = srv._compute_analytics_metrics(tmp_path)

        cost = result["cost"]
        assert cost["per_sprint"]["total"] > 0, "Expected positive cost from 1M input tokens"
        assert cost["per_sprint"]["by_role"]["coder"] > 0, "Coder role cost must be positive"

    def test_valid_multiple_roles(self, tmp_path):
        """AC4: multiple valid rows with different roles are all accumulated."""
        _make_sprint_state(tmp_path, "sprint-1")
        rows = [
            {"agent_role": "coder",     "model_name": "claude-haiku-4-5", "total_input": 1_000_000, "total_output": 0},
            {"agent_role": "tester",    "model_name": "claude-haiku-4-5", "total_input": 500_000,   "total_output": 0},
            {"agent_role": "estimator", "model_name": "claude-haiku-4-5", "total_input": 200_000,   "total_output": 0},
        ]
        import server as srv
        with patch("server.db.get_token_usage_by_agent_model", return_value=rows):
            result = srv._compute_analytics_metrics(tmp_path)

        by_role = result["cost"]["per_sprint"]["by_role"]
        assert by_role["coder"] > 0
        assert by_role["tester"] > 0
        assert by_role["estimator"] > 0
