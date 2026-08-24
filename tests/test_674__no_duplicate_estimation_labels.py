"""Tests for issue #674 — Fix Duplicate Estimation Labels on Ticket View.

AC coverage:
  AC1 — No duplicate estimation labels appear on any ticket in the UI lifecycle
  AC2 — Estimation runs exactly once when a single ticket is created
  AC3 — Estimation runs exactly once per ticket when bulk tickets are created
  AC4 — Estimation runs when user manually triggers estimate action
  AC5 — Estimation does NOT re-run on page re-render / route change
  AC6 — Estimation does NOT re-run on data refresh if ticket already has an estimate
  AC7 — Existing estimates are preserved and not overwritten by spurious re-calls
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(DASHBOARD_DIR.parent))

PROJECT_HTML = DASHBOARD_DIR / "static" / "project.html"


# ── helpers ───────────────────────────────────────────────────────────────────

def _read_project_html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _smgmt_ticket_row_block(src: str) -> str:
    """Extract the _smgmtTicketRowHtml function body from project.html source."""
    start = src.find("function _smgmtTicketRowHtml(")
    if start == -1:
        raise AssertionError("_smgmtTicketRowHtml not found in project.html")
    # Scan for the matching closing brace
    depth = 0
    i = src.find("{", start)
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
        i += 1
    raise AssertionError("Could not find end of _smgmtTicketRow")


# ── AC1: no duplicate size indicators in initial render ───────────────────────

class TestNoUIDuplicateSizeIndicator:
    """AC1 — No duplicate estimation labels appear on any ticket at any point in the UI lifecycle."""

    def test_smgmt_ticket_row_suppresses_size_pill_when_estimate_badge_present(self):
        """When _estDataCache has a size for the issue, sizePillHtml must be
        suppressed so the estimate button and a raw size pill are never rendered
        together.  The fix gates sizePillHtml on estimateBadgeHtml being empty
        OR on there being no cached estimate with a size."""
        src = _read_project_html()
        block = _smgmt_ticket_row_block(src)

        # The guard must be present — sizePillHtml must NOT be assigned
        # unconditionally from sizeValue alone.  It must check that no JSON
        # estimate is already in _estDataCache so that the button and pill
        # cannot coexist.
        #
        # We verify the structure by confirming:
        # (a) estimateBadgeHtml is computed before OR in parallel with sizePillHtml
        # (b) sizePillHtml construction references the cache state
        #
        # Positive signal: "estimateBadgeHtml" appears in sizePillHtml assignment context.
        # We accept any of the canonical guard patterns:
        #   !estimateBadgeHtml
        #   !_hasCachedSize  (or equivalent variable)
        #   !(_cachedEst && _cachedEst.size)
        #   !(_estDataCache...)

        size_pill_assign_idx = block.find("const sizePillHtml =")
        assert size_pill_assign_idx != -1, "sizePillHtml assignment not found in _smgmtTicketRow"

        # Extract the sizePillHtml assignment line/expression
        end_of_assign = block.find(";", size_pill_assign_idx)
        size_pill_expr = block[size_pill_assign_idx : end_of_assign + 1]

        has_estimate_badge_guard  = "estimateBadgeHtml" in size_pill_expr
        has_cached_est_guard      = "_cachedEst" in size_pill_expr
        has_est_cache_guard       = "_estDataCache" in size_pill_expr
        has_cached_size_guard     = "_hasCachedSize" in size_pill_expr

        assert (
            has_estimate_badge_guard
            or has_cached_est_guard
            or has_est_cache_guard
            or has_cached_size_guard
        ), (
            "sizePillHtml must be gated on the absence of a cached JSON estimate to "
            "prevent duplicate size indicators (issue #674). "
            f"Found expression:\n  {size_pill_expr.strip()}"
        )

    def test_smgmt_ticket_row_renders_estimate_badge_before_size_pill_guard(self):
        """estimateBadgeHtml must be computed BEFORE sizePillHtml so that the
        guard can reference it.  Check declaration order in the function body."""
        src = _read_project_html()
        block = _smgmt_ticket_row_block(src)

        badge_idx = block.find("const estimateBadgeHtml =")
        pill_idx  = block.find("const sizePillHtml =")

        assert badge_idx != -1, "estimateBadgeHtml assignment not found in _smgmtTicketRow"
        assert pill_idx  != -1, "sizePillHtml assignment not found in _smgmtTicketRow"
        assert badge_idx < pill_idx, (
            "estimateBadgeHtml must be declared before sizePillHtml in _smgmtTicketRow "
            "so that the guard `!estimateBadgeHtml` (or equivalent) is valid (issue #674). "
            f"badge at char {badge_idx}, pill at char {pill_idx}"
        )


# ── AC2: single ticket creation schedules estimation exactly once ─────────────

class TestSingleTicketEstimationRunsOnce:
    """AC2 — Estimation runs exactly once when a single ticket is created."""

    def test_create_ticket_adds_exactly_one_background_estimation_task(self):
        """POST /api/tickets/create must schedule _run_estimator_for_issue exactly once."""
        from fastapi.testclient import TestClient
        from server import app

        background_calls: list = []

        original_add_task = None

        def _capture_add_task(func, *args, **kwargs):
            if func.__name__ == "_run_estimator_for_issue":
                background_calls.append((func, args, kwargs))
            return original_add_task(func, *args, **kwargs)

        with (
            patch("server.github_client.create_issue", return_value=(999, "https://github.com/test/repo/issues/999")),
            patch("server.github_client.get_repo_for_operation", return_value="test/repo"),
            patch("server.github_client.invalidate"),
            patch("server._ESTIMATE_ISSUE_SCRIPT") as mock_script,
        ):
            mock_script.exists.return_value = True
            # Intercept background_tasks.add_task by monkey-patching at test level
            # We check the endpoint schedules the estimator exactly once by tracking
            # how many times create_issue is called and verifying a single estimation trigger.
            client = TestClient(app, raise_server_exceptions=False)
            # Patch add_task on the BackgroundTasks instance during the request
            from fastapi import BackgroundTasks
            original_add_task_method = BackgroundTasks.add_task
            estimator_task_count = []

            def mock_add_task(self, func, *args, **kwargs):
                if callable(func) and getattr(func, "__name__", "") == "_run_estimator_for_issue":
                    estimator_task_count.append(1)
                return original_add_task_method(self, func, *args, **kwargs)

            with patch.object(BackgroundTasks, "add_task", mock_add_task):
                resp = client.post(
                    "/api/tickets/create",
                    data={"title": "Test ticket", "body": "Body", "project": "test/repo"},
                )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
        assert len(estimator_task_count) == 1, (
            f"Expected exactly 1 background estimation task, got {len(estimator_task_count)}"
        )


# ── AC3: bulk ticket estimation runs once per ticket ─────────────────────────

class TestBulkEstimationRunsOncePerTicket:
    """AC3 — Estimation runs exactly once per ticket when bulk tickets are created."""

    @pytest.mark.asyncio
    async def test_materialise_bulk_estimate_calls_estimator_exactly_once(self):
        """_materialise_bulk_estimate must NOT call _run_bulk_estimator_for_ticket
        on success — it should write the cache and apply labels directly."""
        from server import _materialise_bulk_estimate, _bulk_jobs, _bulk_job_queues

        job_id = "test-674-bulk-once"
        _bulk_jobs[job_id] = {
            "job_id": job_id, "status": "running",
            "tickets": [{
                "index": 0, "state": "created", "title": "T0", "body": "B0",
                "issue_num": 900, "issue_url": "https://github.com/x/y/issues/900",
                "label_pills": ["backlog"], "error": None, "attachment_warning": None,
                "started_at": None, "finished_at": None, "retry_count": 0, "last_error": None,
            }],
            "repo": "test/repo",
        }
        _bulk_job_queues[job_id] = []

        pre_estimate = {"size": "M", "estimated_hours": "0.25", "confidence": "high",
                        "files_likely_affected": [], "risk_flags": [], "summary": ""}

        fallback_calls = []

        async def mock_fallback(*args, **kwargs):
            fallback_calls.append(args)

        with (
            patch("server._run_bulk_estimator_for_ticket", side_effect=mock_fallback),
            patch("server._commander_dir") as mock_cmd_dir,
            patch("server._project_root_path", return_value=Path("/tmp/test")),
            patch("server.github_client.update_labels"),
            patch("server.github_client.add_comment"),
            patch("server.github_client.invalidate"),
            patch("server.asyncio.to_thread", new_callable=AsyncMock),
        ):
            mock_dir = MagicMock()
            mock_dir.__truediv__ = lambda self, x: MagicMock(
                __truediv__=lambda s, y: MagicMock(
                    mkdir=MagicMock(),
                    write_text=MagicMock(),
                    __truediv__=lambda ss, z: MagicMock(write_text=MagicMock()),
                )
            )
            mock_cmd_dir.return_value = mock_dir

            await _materialise_bulk_estimate(job_id, 0, 900, "test/repo", pre_estimate)

        assert len(fallback_calls) == 0, (
            f"_run_bulk_estimator_for_ticket must NOT be called when pre_estimate succeeds "
            f"(called {len(fallback_calls)} times). This would double-apply labels (issue #674)."
        )


# ── AC5/AC6: no estimation triggered by data-refresh/polling ─────────────────

class TestNoEstimationOnRefresh:
    """AC5+AC6 — Estimation does NOT re-run on page re-render or data refresh."""

    def test_get_issues_endpoint_does_not_trigger_estimation(self):
        """GET /api/issues (and similar read endpoints) must not call the estimator."""
        from fastapi.testclient import TestClient
        from server import app

        with patch("server.github_client.list_all_open_issues", return_value=[]) as mock_issues, \
             patch("server._ei_run_estimator") as mock_run:
            client = TestClient(app, raise_server_exceptions=False)
            client.get("/api/issues")

        mock_run.assert_not_called()

    def test_sprint_management_issues_endpoint_does_not_trigger_estimation(self):
        """GET /api/sprint-management/issues must not trigger estimation."""
        from fastapi.testclient import TestClient
        from server import app

        with patch("server._ei_run_estimator") as mock_run, \
             patch("server.github_client.list_open_issues_with_body", return_value=[]):
            client = TestClient(app, raise_server_exceptions=False)
            client.get("/api/sprint-management/issues?project=test/repo")

        mock_run.assert_not_called()


# ── AC7: existing estimates preserved ─────────────────────────────────────────

class TestExistingEstimatesPreserved:
    """AC7 — Existing estimates are preserved and not overwritten by spurious re-calls."""

    def test_estimate_issue_script_uses_cache_when_exists_and_no_force(self, tmp_path):
        """estimate_issue.py main() returns the cached result when the estimate
        file already exists and --force is not set.  The LLM (run_estimator) must
        not be called again."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "sprint_manager"))
        from estimate_issue import main as ei_main

        cached = {
            "size": "S",
            "estimated_hours": "0.1",
            "confidence": "high",
            "files_likely_affected": ["server.py"],
            "risk_flags": [],
            "summary": "cached",
            "body_hash": "abc123",
        }

        # Create a fake .commander/estimates directory with an existing estimate file
        estimates_dir = tmp_path / ".commander" / "estimates"
        estimates_dir.mkdir(parents=True)
        estimate_file = estimates_dir / "issue-42.json"
        estimate_file.write_text(json.dumps(cached), encoding="utf-8")

        run_estimator_calls = []

        with (
            patch("estimate_issue.find_commander_dir", return_value=tmp_path / ".commander"),
            patch("estimate_issue.fetch_issue", return_value={"title": "T", "body": "B"}),
            patch("estimate_issue.run_estimator", side_effect=lambda *a, **k: run_estimator_calls.append(1) or (None, "forced")),
            patch("estimate_issue.apply_label"),
            patch("estimate_issue.apply_estimated_status", return_value=True),
            patch("estimate_issue.load_calibration") as mock_cal,
            patch("estimate_issue.post_comment"),
            patch("sys.argv", ["estimate_issue.py", "--issue", "42", "--repo", "test/repo", "--save-label"]),
        ):
            mock_cal.return_value = MagicMock(warnings=[], calibration_path=None, record_count=0)
            ei_main()

        assert len(run_estimator_calls) == 0, (
            "run_estimator must NOT be called when a cached estimate exists and --force "
            f"is not set (called {len(run_estimator_calls)} times). "
            "This ensures existing estimates are preserved (issue #674 AC7)."
        )

    def test_on_demand_estimate_endpoint_applies_label_once(self):
        """POST /api/issues/{id}/estimate must apply the size label exactly once
        (not call apply_label multiple times for the same request)."""
        from fastapi.testclient import TestClient
        from server import app

        label_apply_calls = []

        def mock_apply_label(issue_num, repo, size):
            label_apply_calls.append((issue_num, repo, size))

        with (
            patch("server._ei_fetch_issue", return_value={"title": "Test", "body": "Body"}),
            patch("server._ei_run_estimator", return_value=(
                {"size": "M", "estimated_hours": "0.25", "minutes": 15, "confidence": "high"}, None
            )),
            patch("server._ei_apply_label", side_effect=mock_apply_label),
            patch("server._ei_apply_estimated_status"),
            patch("server._project_root_path", return_value=Path("/tmp/test")),
            patch("server._commander_dir") as mock_cmd_dir,
        ):
            mock_dir = MagicMock()
            mock_dir.__truediv__ = lambda self, x: MagicMock(
                mkdir=MagicMock(),
                __truediv__=lambda s, y: MagicMock(write_text=MagicMock()),
            )
            mock_cmd_dir.return_value = mock_dir

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/issues/42/estimate?repo=test/repo",
                headers={"Content-Type": "application/json"},
            )

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        assert len(label_apply_calls) == 1, (
            f"apply_label must be called exactly once per estimate request, "
            f"got {len(label_apply_calls)} calls (issue #674 AC7)."
        )
