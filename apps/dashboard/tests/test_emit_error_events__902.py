"""Tests for issue #902: Emit error events and durations into activity stream.

Tests are unit-level — they exercise activity_service.get_project_events()
directly via monkeypatching, so no running server is required.

AC under test:
- Dispatch lifecycle events are teed into events-<date>.jsonl via log.event()
  centrally in services/logging.py (no new call sites in sprint_manager.py).
- Each teed event carries a ``category`` per the mapping.
- The Activity API unions JSONL events with the DB events (deduped, timestamp-ordered).
- The Activity endpoint accepts a ``category`` query param and returns only matching events.
- dispatch_failed events carry stderr_tail and sidecar fields.
- Serving the feed makes zero GitHub calls.
- No event is written twice (one writer per event; reader merges).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Resolve imports without a running server.
# db.py calls sys.exit(1) if DB_PATH is unset, so we must set a dummy value
# before any import chain that pulls in db.py.
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DASHBOARD_ROOT = REPO_ROOT / "apps" / "dashboard"
SERVICES_ROOT = REPO_ROOT / "services"

# Provide a dummy DB_PATH so db.py doesn't sys.exit at import time.
# Use a safe tmp path — the dashboard conftest (apps/dashboard/tests/conftest.py)
# already forces DB_PATH to /tmp/commander-pytest.db at import time, so this
# branch should be unreachable.  It is kept as a belt-and-braces fallback
# that uses a safe path rather than the production commander.db (issue #2047).
if not os.environ.get("DB_PATH"):
    os.environ["DB_PATH"] = "/tmp/commander-pytest.db"

for p in (str(DASHBOARD_ROOT), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Import the service module under test directly (not via the package __init__,
# which pulls in every router including ones that need sqlalchemy/neon).
import importlib.util as _ilu

_svc_path = DASHBOARD_ROOT / "routers" / "activity_service.py"
_spec = _ilu.spec_from_file_location("activity_service", _svc_path)
svc = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(svc)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl_event(
    name: str,
    category: str,
    timestamp: str = "2026-06-13T10:00:00+00:00",
    **fields: Any,
) -> dict:
    """Return a dict matching the JSONL format written by services/logging.py."""
    rec: dict = {
        "timestamp": timestamp,
        "name": name,
        "category": category,
    }
    rec.update(fields)
    return rec


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


# ---------------------------------------------------------------------------
# AC: category mapping — dispatch lifecycle events carry correct category
# ---------------------------------------------------------------------------

class TestCategoryMapping:
    """Verify the mapping table defined in services/logging.py."""

    def test_dispatch_start_maps_to_agent(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["dispatch_start"] == "agent"

    def test_dispatch_finished_maps_to_agent(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["dispatch_finished"] == "agent"

    def test_dispatch_failed_maps_to_error(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["dispatch_failed"] == "error"

    def test_dispatch_killed_maps_to_error(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["dispatch_killed"] == "error"

    def test_gate_failed_maps_to_error(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["gate_failed"] == "error"

    def test_sprint_crashed_maps_to_error(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["sprint_crashed"] == "error"

    def test_gate_passed_maps_to_gate(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["gate_passed"] == "gate"

    def test_sidecar_written_maps_to_sprint(self):
        from services.logging import _LIFECYCLE_EVENT_CATEGORY
        assert _LIFECYCLE_EVENT_CATEGORY["sidecar_written"] == "sprint"


# ---------------------------------------------------------------------------
# AC: _emit() tees lifecycle events into events-<date>.jsonl
# ---------------------------------------------------------------------------

class TestLoggingTee:
    """Verify that _emit() writes lifecycle events to the JSONL file."""

    def test_dispatch_failed_teed_to_jsonl(self, tmp_path):
        """dispatch_failed via structured_log.error() should appear in events JSONL."""
        from services.logging import _Logger

        logger = _Logger()
        with patch.object(logger, "_events_log_path", return_value=tmp_path / "events-test.jsonl"):
            with patch.object(logger, "_structured_log_path", return_value=tmp_path / "structured-test.log"):
                logger.error(
                    "dispatch_failed",
                    "coder #42 exited 1",
                    issue_num=42,
                    agent_role="coder",
                    sprint_label="sprint-1",
                    attempt=1,
                    exit_code=1,
                    duration_s=30.5,
                    stderr_tail="Error: something failed",
                )

        jsonl_path = tmp_path / "events-test.jsonl"
        assert jsonl_path.exists(), "events JSONL file must be created"
        lines = [l.strip() for l in jsonl_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1, f"Expected exactly 1 JSONL event, got {len(lines)}"

        ev = json.loads(lines[0])
        assert ev["name"] == "dispatch_failed"
        assert ev["category"] == "error"
        assert ev["issue_num"] == 42
        assert ev["agent_role"] == "coder"
        assert ev["exit_code"] == 1
        assert ev["duration_s"] == 30.5
        assert ev["stderr_tail"] == "Error: something failed"

    def test_non_lifecycle_event_not_teed(self, tmp_path):
        """Non-lifecycle events (like 'claude_cli_not_found') must NOT be teed."""
        from services.logging import _Logger

        logger = _Logger()
        with patch.object(logger, "_events_log_path", return_value=tmp_path / "events-test.jsonl"):
            with patch.object(logger, "_structured_log_path", return_value=tmp_path / "structured-test.log"):
                logger.error(
                    "claude_cli_not_found",
                    "claude CLI not found",
                    issue_num=42,
                )

        jsonl_path = tmp_path / "events-test.jsonl"
        # Should NOT exist (or be empty if created by something else)
        if jsonl_path.exists():
            lines = [l.strip() for l in jsonl_path.read_text().splitlines() if l.strip()]
            assert len(lines) == 0, "Non-lifecycle event must not be teed into events JSONL"

    def test_dispatch_finished_teed_with_duration(self, tmp_path):
        """dispatch_finished must preserve duration_s field."""
        from services.logging import _Logger

        logger = _Logger()
        with patch.object(logger, "_events_log_path", return_value=tmp_path / "events-test.jsonl"):
            with patch.object(logger, "_structured_log_path", return_value=tmp_path / "structured-test.log"):
                logger.info(
                    "dispatch_finished",
                    "coder #7 finished",
                    issue_num=7,
                    agent_role="coder",
                    sprint_label="sprint-5",
                    attempt=1,
                    exit_code=0,
                    duration_s=120.3,
                )

        jsonl_path = tmp_path / "events-test.jsonl"
        assert jsonl_path.exists()
        ev = json.loads(jsonl_path.read_text().strip())
        assert ev["category"] == "agent"
        assert ev["duration_s"] == 120.3
        assert ev["agent_role"] == "coder"


# ---------------------------------------------------------------------------
# AC: Activity API unions JSONL events with DB events
# ---------------------------------------------------------------------------

def _fake_projects(slug: str) -> list[dict]:
    return [{"repo": f"testowner/{slug}", "name": slug.title()}]


class TestActivityUnion:
    """Verify get_project_events unions JSONL and SQLite events."""

    def _setup_jsonl(self, tmp_path: Path, events: list[dict]) -> None:
        jsonl_path = tmp_path / "events-2026-06-13.jsonl"
        _write_jsonl(jsonl_path, events)

    def test_dispatch_failed_returned_with_category_error(self, tmp_path):
        """Force a dispatch_failed event; Activity API must return it with category=error,
        stderr_tail, and sidecar populated (core UAT test from AC)."""
        slug = "myproject"
        project_key = f"testowner/{slug}"

        dispatch_failed_event = _make_jsonl_event(
            name="dispatch_failed",
            category="error",
            timestamp="2026-06-13T10:00:00+00:00",
            issue_num=55,
            agent_role="coder",
            sprint_label="sprint-3",
            run_id="sprint-20260613T100000-ab12cd34",
            attempt=1,
            exit_code=1,
            duration_s=45.2,
            stderr_tail="AssertionError: test_foo failed",
            sidecar=".commander/runtime/last-failure-55.json",
            project=project_key,
        )
        self._setup_jsonl(tmp_path, [dispatch_failed_event])

        with patch.object(svc, "_project_logs_dir", return_value=tmp_path):
            with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
                with patch.object(svc.db, "get_conn") as mock_conn:
                    mock_ctx = MagicMock()
                    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
                    mock_ctx.__exit__ = MagicMock(return_value=False)
                    mock_ctx.execute.return_value.fetchall.return_value = []
                    mock_conn.return_value = mock_ctx

                    result = svc.get_project_events(slug, category="error")

        assert len(result) == 1, f"Expected 1 error event, got {len(result)}: {result}"
        ev = result[0]
        assert ev["category"] == "error", f"Expected category=error, got {ev.get('category')}"
        assert ev["type"] == "dispatch_failed"
        assert ev["stderr_tail"] == "AssertionError: test_foo failed"
        assert ev["sidecar"] == ".commander/runtime/last-failure-55.json"
        assert ev["issue_num"] == 55

    def test_category_filter_returns_only_matching(self, tmp_path):
        """category=error must exclude gate and agent events."""
        slug = "myproject"
        project_key = f"testowner/{slug}"

        events = [
            _make_jsonl_event("dispatch_failed", "error", "2026-06-13T10:01:00+00:00",
                               issue_num=1, project=project_key, stderr_tail="err"),
            _make_jsonl_event("dispatch_finished", "agent", "2026-06-13T10:02:00+00:00",
                               issue_num=2, project=project_key, duration_s=10.0),
            _make_jsonl_event("gate_passed", "gate", "2026-06-13T10:03:00+00:00",
                               issue_num=3, project=project_key, gate="pytest"),
        ]
        self._setup_jsonl(tmp_path, events)

        with patch.object(svc, "_project_logs_dir", return_value=tmp_path):
            with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
                result = svc.get_project_events(slug, category="error")

        assert len(result) == 1
        assert result[0]["type"] == "dispatch_failed"

    def test_no_category_unions_db_and_jsonl(self, tmp_path):
        """Without category filter, DB events and JSONL events are merged."""
        slug = "myproject"
        project_key = f"testowner/{slug}"

        jsonl_ev = _make_jsonl_event(
            "gate_passed", "gate", "2026-06-13T09:00:00+00:00",
            issue_num=10, project=project_key, gate="pytest",
        )
        self._setup_jsonl(tmp_path, [jsonl_ev])

        db_row = {
            "timestamp": "2026-06-13T08:00:00+00:00",
            "source": "dashboard",
            "actor": "dashboard",
            "type": "agent_started",
            "target": "10",
            "action_id": None,
            "detail": json.dumps({"issue_num": 10}),
        }

        with patch.object(svc, "_project_logs_dir", return_value=tmp_path):
            with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
                with patch.object(svc.db, "get_conn") as mock_conn:
                    mock_ctx = MagicMock()
                    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
                    mock_ctx.__exit__ = MagicMock(return_value=False)
                    mock_ctx.execute.return_value.fetchall.return_value = [db_row]
                    mock_conn.return_value = mock_ctx

                    result = svc.get_project_events(slug)

        types = [e["type"] for e in result]
        assert "gate_passed" in types, "JSONL lifecycle event must appear in merged result"
        assert "agent_started" in types, "DB event must appear in merged result"

    def test_no_event_written_twice(self, tmp_path):
        """The same (timestamp, type) key must not appear twice in the merged output."""
        slug = "myproject"
        project_key = f"testowner/{slug}"

        # Write a JSONL event that matches a DB event by (timestamp, type)
        ts = "2026-06-13T10:00:00+00:00"
        jsonl_ev = _make_jsonl_event(
            "dispatch_failed", "error", ts,
            issue_num=5, project=project_key, stderr_tail="err",
        )
        self._setup_jsonl(tmp_path, [jsonl_ev])

        db_row = {
            "timestamp": ts,
            "source": "agent",
            "actor": "sprint_manager",
            "type": "dispatch_failed",
            "target": "5",
            "action_id": None,
            "detail": "{}",
        }

        with patch.object(svc, "_project_logs_dir", return_value=tmp_path):
            with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
                with patch.object(svc.db, "get_conn") as mock_conn:
                    mock_ctx = MagicMock()
                    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
                    mock_ctx.__exit__ = MagicMock(return_value=False)
                    mock_ctx.execute.return_value.fetchall.return_value = [db_row]
                    mock_conn.return_value = mock_ctx

                    result = svc.get_project_events(slug)

        matching = [e for e in result if e["type"] == "dispatch_failed" and e["timestamp"] == ts]
        assert len(matching) == 1, f"Event must not be written twice; found {len(matching)} copies"

    def test_results_ordered_newest_first(self, tmp_path):
        """Merged results must be ordered newest timestamp first."""
        slug = "myproject"
        project_key = f"testowner/{slug}"

        events = [
            _make_jsonl_event("gate_passed", "gate", "2026-06-13T08:00:00+00:00",
                               project=project_key),
            _make_jsonl_event("dispatch_failed", "error", "2026-06-13T10:00:00+00:00",
                               project=project_key, stderr_tail="x"),
        ]
        self._setup_jsonl(tmp_path, events)

        with patch.object(svc, "_project_logs_dir", return_value=tmp_path):
            with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
                with patch.object(svc.db, "get_conn") as mock_conn:
                    mock_ctx = MagicMock()
                    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
                    mock_ctx.__exit__ = MagicMock(return_value=False)
                    mock_ctx.execute.return_value.fetchall.return_value = []
                    mock_conn.return_value = mock_ctx

                    result = svc.get_project_events(slug)

        timestamps = [e["timestamp"] for e in result]
        assert timestamps == sorted(timestamps, reverse=True), (
            f"Events must be newest-first; got {timestamps}"
        )

    def test_invalid_category_returns_400(self, tmp_path):
        """An unknown category value must raise HTTP 400."""
        from fastapi import HTTPException

        slug = "myproject"
        with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
            with pytest.raises(HTTPException) as exc_info:
                svc.get_project_events(slug, category="unknown-cat")
        assert exc_info.value.status_code == 400

    def test_zero_github_calls(self, tmp_path):
        """Serving the feed must make zero GitHub calls (no github_client imports)."""
        import importlib
        # Re-inspect the module source for github_client references
        src_path = Path(svc.__file__)
        src = src_path.read_text()
        assert "github_client" not in src, (
            "activity_service.py must not import github_client — serving the feed "
            "must make zero GitHub calls"
        )

    def test_dispatch_finished_carries_duration_s(self, tmp_path):
        """dispatch_finished events must carry duration_s at the top level."""
        slug = "myproject"
        project_key = f"testowner/{slug}"

        ev = _make_jsonl_event(
            "dispatch_finished", "agent", "2026-06-13T11:00:00+00:00",
            issue_num=20, agent_role="tester", sprint_label="sprint-6",
            duration_s=88.5, project=project_key,
        )
        self._setup_jsonl(tmp_path, [ev])

        with patch.object(svc, "_project_logs_dir", return_value=tmp_path):
            with patch.object(svc.projects_module, "load_projects", return_value=_fake_projects(slug)):
                result = svc.get_project_events(slug, category="agent")

        assert len(result) == 1
        assert result[0]["duration_s"] == 88.5
        assert result[0]["agent_role"] == "tester"
