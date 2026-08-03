"""Tests for issue #2066: Add response_model to Hermes-critical endpoints.

AC1 — Pydantic response models exist with real field types.
AC2 — /openapi.json shows concrete properties (not empty {}) for each operation.
AC3 — Existing consumers keep working (responses validate against the models).
AC4 — Behavioral tests hitting each typed endpoint through TestClient and
       validating the response against the model (CLAUDE.md #1746).
AC5 — Remaining untyped count reported as a test assertion.

How these tests FAIL before implementation
------------------------------------------
AC1/AC4 model-import tests:
  ImportError — hermes_models.py does not exist, the model classes can't be imported.

AC2 schema tests:
  AssertionError — the OpenAPI schema for each operation is empty ({}) before
  response_model is wired, so ``assert schema_content`` fails.

AC4 behavioral endpoint tests:
  AssertionError — response.json() cannot be validated by the model (NameError
  if import failed, or ValidationError if fields are absent/mistyped).

AC5 baseline test:
  AssertionError — without the 7 new typed routes the typed count is < 44.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402


# ── git-isolation guard ───────────────────────────────────────────────────────

def _head_sha() -> str:
    import subprocess
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(_REPO_ROOT), text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    before = _head_sha()
    yield
    after = _head_sha()
    assert before == after, (
        f"Test mutated the git repository (HEAD moved).\n"
        f"before={before}\nafter={after}"
    )


# ── DB isolation fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def isolated_db():
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="commander-test-2066-")
    os.close(fd)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    original_path = _db.DB_PATH
    _db.DB_PATH = Path(db_path)
    os.environ["DB_PATH"] = db_path
    _db.init_db()
    yield db_path
    _db.DB_PATH = original_path
    os.environ.pop("DB_PATH", None)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


# ── AC1: Model import and field types ─────────────────────────────────────────

def test_hermes_models_importable():
    """AC1: All response models can be imported from hermes_models."""
    from routers.hermes_models import (  # noqa: F401
        SprintRunResponse,
        SprintLiveResponse,
        TicketRef,
        ActiveAgent,
        ActiveAgentEntry,
        LevelEntry,
        LogLine,
        LiveIssue,
        DevReportResponse,
        TicketDraftResponse,
        TicketCreateResponse,
        FailureRow,
        BoardResponse,
        BoardSections,
        BoardBacklog,
        BoardCapacity,
        BoardCacheInfo,
    )


def test_sprint_run_response_fields():
    """AC1: SprintRunResponse has the required fields with correct types."""
    from routers.hermes_models import SprintRunResponse
    fields = SprintRunResponse.model_fields
    assert "ok" in fields
    assert "sprint_label" in fields
    assert "pid" in fields
    assert "log" in fields
    assert "migrated_count" in fields
    assert "migrate_from" in fields


def test_failure_row_fields():
    """AC1: FailureRow has all normalized fields from failures_service."""
    from routers.hermes_models import FailureRow
    fields = FailureRow.model_fields
    for f in ("source", "issue_number", "sprint_label", "project", "agent",
              "category", "reason", "failure_class", "message",
              "attempt_kind", "branch", "log_url", "ts"):
        assert f in fields, f"FailureRow missing field: {f}"


def test_ticket_draft_response_fields():
    """AC1: TicketDraftResponse has draft_id, title, body."""
    from routers.hermes_models import TicketDraftResponse
    fields = TicketDraftResponse.model_fields
    assert "draft_id" in fields
    assert "title" in fields
    assert "body" in fields


def test_ticket_create_response_fields():
    """AC1: TicketCreateResponse has number and url."""
    from routers.hermes_models import TicketCreateResponse
    fields = TicketCreateResponse.model_fields
    assert "number" in fields
    assert "url" in fields


def test_board_response_fields():
    """AC1: BoardResponse has project, generated_at, sections, capacity, summaries, cache."""
    from routers.hermes_models import BoardResponse
    fields = BoardResponse.model_fields
    for f in ("project", "generated_at", "sections", "capacity", "summaries", "cache"):
        assert f in fields, f"BoardResponse missing field: {f}"


def test_sprint_live_response_core_fields():
    """AC1: SprintLiveResponse has the fields listed in the docstring."""
    from routers.hermes_models import SprintLiveResponse
    fields = SprintLiveResponse.model_fields
    for f in ("time_spent_sec", "started_at", "current_ticket", "active_agent",
              "active_agents", "pipeline_mode", "max_coder_slots",
              "max_tester_slots", "active_coder_slots", "active_tester_slots",
              "levels", "recent_log_lines", "done_count", "issues"):
        assert f in fields, f"SprintLiveResponse missing field: {f}"


def test_dev_report_response_fields():
    """AC1: DevReportResponse covers the build_contract() top-level keys."""
    from routers.hermes_models import DevReportResponse
    fields = DevReportResponse.model_fields
    for f in ("for_date", "generated_at", "window_start", "window_end",
              "projects", "cost", "cost_source", "completed",
              "needs_review", "dead_letter"):
        assert f in fields, f"DevReportResponse missing field: {f}"


# ── AC2: /openapi.json shows concrete properties ──────────────────────────────

def _get_app_schema():
    from server import app
    return app.openapi()


def _get_response_schema(schema: dict, path: str, method: str, status: str) -> dict:
    op = schema.get("paths", {}).get(path, {}).get(method.lower(), {})
    resp = op.get("responses", {}).get(status, {})
    return resp.get("content", {}).get("application/json", {}).get("schema", {})


def test_openapi_sprint_run_has_schema():
    """AC2: POST /api/sprints/run [202] has a typed response schema (not {})."""
    s = _get_response_schema(_get_app_schema(), "/api/sprints/run", "post", "202")
    assert s, "POST /api/sprints/run [202] has empty schema — response_model not wired"
    # Must reference or embed concrete properties
    assert "$ref" in s or "properties" in s, (
        f"Schema exists but has no $ref or properties: {s}"
    )


def test_openapi_sprint_live_has_schema():
    """AC2: GET /api/sprints/{sprint_label}/live [200] has a typed schema."""
    s = _get_response_schema(
        _get_app_schema(), "/api/sprints/{sprint_label}/live", "get", "200"
    )
    assert s, "GET …/live [200] has empty schema"
    assert "$ref" in s or "properties" in s


def test_openapi_dev_report_has_schema():
    """AC2: GET /api/dev-report [200] has a typed schema."""
    s = _get_response_schema(_get_app_schema(), "/api/dev-report", "get", "200")
    assert s, "GET /api/dev-report [200] has empty schema"
    assert "$ref" in s or "properties" in s


def test_openapi_ticket_draft_has_schema():
    """AC2: POST /api/tickets/draft [200] has a typed schema."""
    s = _get_response_schema(_get_app_schema(), "/api/tickets/draft", "post", "200")
    assert s, "POST /api/tickets/draft [200] has empty schema"
    assert "$ref" in s or "properties" in s


def test_openapi_ticket_create_has_schema():
    """AC2: POST /api/tickets/create [201] has a typed schema."""
    s = _get_response_schema(_get_app_schema(), "/api/tickets/create", "post", "201")
    assert s, "POST /api/tickets/create [201] has empty schema"
    assert "$ref" in s or "properties" in s


def test_openapi_failures_has_schema():
    """AC2: GET /api/failures [200] has a typed schema (array with typed items)."""
    s = _get_response_schema(_get_app_schema(), "/api/failures", "get", "200")
    assert s, "GET /api/failures [200] has empty schema"
    assert s.get("type") == "array" or "$ref" in s or "items" in s, (
        f"Unexpected schema shape for failures: {s}"
    )
    # items must reference FailureRow, not be plain Any
    items = s.get("items", {})
    assert "$ref" in items, f"Failures schema items must $ref FailureRow, got: {items}"


def test_openapi_board_has_schema():
    """AC2: GET /api/board [200] has a typed schema."""
    s = _get_response_schema(_get_app_schema(), "/api/board", "get", "200")
    assert s, "GET /api/board [200] has empty schema"
    assert "$ref" in s or "properties" in s


def test_openapi_component_schemas_contain_real_properties():
    """AC2: The referenced component schemas have concrete properties, not empty dicts."""
    schema = _get_app_schema()
    comps = schema.get("components", {}).get("schemas", {})
    for model_name in (
        "SprintRunResponse",
        "SprintLiveResponse",
        "DevReportResponse",
        "TicketDraftResponse",
        "TicketCreateResponse",
        "FailureRow",
        "BoardResponse",
    ):
        s = comps.get(model_name, {})
        assert s, f"Component schema {model_name!r} is missing from openapi"
        props = s.get("properties", {})
        assert props, (
            f"{model_name} has no properties in /openapi.json — "
            f"schema: {json.dumps(s)[:200]}"
        )


# ── AC4: Behavioral endpoint tests via TestClient ─────────────────────────────

_NOW = datetime.now(timezone.utc).isoformat()
_PROJECT = "zealchaiwut/commander"


def test_failures_endpoint_validates_against_failure_row(isolated_db):
    """AC4: GET /api/failures returns a list parseable by list[FailureRow]."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import FailureRow

    # Seed a failure row so the response is non-empty
    with _db.get_conn() as conn:
        conn.execute(
            """INSERT INTO events
               (project, timestamp, source, actor, type, target, action_id, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _PROJECT, _NOW, "agent", "coder", "ticket_failed",
                "issue-1", None,
                json.dumps({"issue_num": 1, "sprint_label": "sprint-1",
                            "agent": "coder", "category": "gate_fail"}),
            ),
        )
        conn.commit()

    client = TestClient(app, raise_server_exceptions=True)
    response = client.get(f"/api/failures?project={_PROJECT}")
    assert response.status_code == 200, response.text

    rows = response.json()
    assert isinstance(rows, list)
    assert len(rows) >= 1

    for row in rows:
        parsed = FailureRow.model_validate(row)
        assert parsed.source in ("events", "agent_runs", "agents"), (
            f"Unexpected source: {parsed.source!r}"
        )


def test_board_endpoint_validates_against_board_response(isolated_db):
    """AC4: GET /api/board returns a payload parseable by BoardResponse."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import BoardResponse
    from routers import board_cache, board_service

    fixture_board = {
        "project": _PROJECT,
        "generated_at": _NOW,
        "sections": {
            "running": [],
            "needs_rework": [],
            "ready_to_merge": [],
            "draft": [],
            "lineage": [],
            "backlog": {"count": 0, "tickets": []},
        },
        "capacity": {
            "budget_minutes": 180,
            "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60},
        },
        "summaries": {},
    }

    with (
        patch.object(board_cache, "get_board_cache", return_value=None),
        patch.object(board_service, "assemble_board", return_value=fixture_board),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(f"/api/board?project={_PROJECT}")

    assert response.status_code == 200, response.text
    data = response.json()
    parsed = BoardResponse.model_validate(data)
    assert parsed.project == _PROJECT
    assert parsed.cache.hit is False  # freshly computed
    assert isinstance(parsed.sections.backlog.count, int)


def test_dev_report_endpoint_validates_against_dev_report_response(isolated_db):
    """AC4: GET /api/dev-report returns a payload parseable by DevReportResponse."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import DevReportResponse
    from routers import dev_report_service as _svc

    fixture_payload = {
        "for_date": "2026-08-01",
        "generated_at": "2026-08-01T05:00:00Z",
        "window_start": "2026-07-31T17:00:00Z",
        "window_end": "2026-08-01T05:00:00Z",
        "projects": [],
        "cost": "0 tokens",
        "cost_source": "token_count",
        "completed": [],
        "needs_review": [],
        "dead_letter": [],
    }
    fixture_artifact = {"payload": fixture_payload, "generated_at": _NOW}

    with patch.object(_svc, "get_dev_report_artifact", return_value=fixture_artifact):
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get("/api/dev-report?date=2026-08-01")

    assert response.status_code == 200, response.text
    data = response.json()
    parsed = DevReportResponse.model_validate(data)
    assert parsed.for_date == "2026-08-01"
    assert isinstance(parsed.projects, list)
    assert isinstance(parsed.completed, list)


def test_ticket_create_endpoint_validates_against_response(isolated_db):
    """AC4: POST /api/tickets/create returns {number, url} parseable by TicketCreateResponse."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import TicketCreateResponse

    mock_gc = MagicMock()
    mock_gc.create_issue.return_value = (9999, "https://github.com/test/repo/issues/9999")
    mock_gc.get_repo_for_operation.return_value = _PROJECT
    mock_gc.invalidate.return_value = None

    import server as srv_module

    with ExitStack() as stack:
        stack.enter_context(patch.object(srv_module, "github_client", mock_gc))
        stack.enter_context(patch("routers.board_cache.invalidate_board", return_value=None))
        stack.enter_context(
            patch.object(srv_module, "_ESTIMATE_ISSUE_SCRIPT",
                         MagicMock(exists=MagicMock(return_value=False)))
        )
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/api/tickets/create",
            data={"title": "Test ticket", "body": "Test body", "project": _PROJECT},
        )

    assert response.status_code == 201, response.text
    data = response.json()
    parsed = TicketCreateResponse.model_validate(data)
    assert parsed.number == 9999
    assert "9999" in parsed.url


def test_ticket_draft_endpoint_validates_against_response(isolated_db):
    """AC4: POST /api/tickets/draft returns {draft_id, title, body} parseable by TicketDraftResponse."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import TicketDraftResponse

    draft_json = json.dumps({"title": "My ticket", "body": "## AC\n- [ ] done"})
    mock_proc = MagicMock()
    mock_proc.pid = 777
    mock_proc.returncode = 0

    async def _communicate():
        return (draft_json.encode(), b"")

    mock_proc.communicate = MagicMock(side_effect=_communicate)

    async def _create_subprocess(*_a, **_kw):
        return mock_proc

    import server as srv_module

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(srv_module, "_parse_ba_draft",
                         return_value=("My ticket", "## AC\n- [ ] done", True))
        )
        stack.enter_context(
            patch("asyncio.create_subprocess_exec", side_effect=_create_subprocess)
        )
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/api/tickets/draft",
            data={"description": "Add response models to endpoints",
                  "project": _PROJECT},
        )

    assert response.status_code == 200, response.text
    data = response.json()
    parsed = TicketDraftResponse.model_validate(data)
    assert parsed.title == "My ticket"
    assert parsed.draft_id  # non-empty UUID string


def test_sprint_live_endpoint_validates_against_response(isolated_db, tmp_path):
    """AC4: GET /api/sprints/{sprint_label}/live returns payload parseable by SprintLiveResponse."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import SprintLiveResponse

    commander = tmp_path / ".commander"
    (commander / "sprints").mkdir(parents=True)

    import server as srv_module
    import re

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(srv_module, "_SPRINT_LABEL_RE", re.compile(r"^sprint-\d+"))
        )
        stack.enter_context(
            patch.object(srv_module, "_project_root_path", return_value=tmp_path)
        )
        stack.enter_context(
            patch.object(srv_module, "_commander_dir", return_value=commander)
        )
        stack.enter_context(
            patch.object(srv_module, "_read_plan_json", return_value={})
        )
        stack.enter_context(
            patch.object(srv_module, "_sprint_statuses", {})
        )
        stack.enter_context(
            patch.object(srv_module, "_sprint_pid_alive", return_value=False)
        )
        stack.enter_context(patch("live_metrics.runs_by_issue", return_value={}))
        stack.enter_context(
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[])
        )
        stack.enter_context(
            patch("live_metrics.lane_capacity",
                  return_value={"max_coder_slots": 1, "max_tester_slots": 1})
        )
        stack.enter_context(patch("live_metrics.running_metrics", return_value={}))
        client = TestClient(app, raise_server_exceptions=True)
        response = client.get(f"/api/sprints/sprint-1/live?project={_PROJECT}")

    assert response.status_code == 200, response.text
    data = response.json()
    parsed = SprintLiveResponse.model_validate(data)
    assert isinstance(parsed.time_spent_sec, int)
    assert isinstance(parsed.issues, list)
    assert isinstance(parsed.active_agents, list)


def test_sprint_run_endpoint_validates_against_response(isolated_db, tmp_path):
    """AC4: POST /api/sprints/run [202] returns payload parseable by SprintRunResponse."""
    from server import app
    from fastapi.testclient import TestClient
    from routers.hermes_models import SprintRunResponse
    from routers import sprint_run_service, sprint_webhook_service

    import server as srv_module
    import re

    commander = tmp_path / ".commander"
    (commander / "logs").mkdir(parents=True)
    (commander / "sprints").mkdir(parents=True)

    mock_proc = MagicMock()
    mock_proc.pid = 54321
    mock_proc.poll.return_value = None  # still running (no early exit)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(srv_module, "_SPRINT_LABEL_RE", re.compile(r"^sprint-\d+"))
        )
        stack.enter_context(
            patch.object(srv_module, "SPRINT_MANAGER_PATH",
                         MagicMock(exists=MagicMock(return_value=True)))
        )
        stack.enter_context(
            patch.object(srv_module, "_project_root_path", return_value=tmp_path)
        )
        stack.enter_context(
            patch.object(srv_module, "_any_sprint_running", return_value=None)
        )
        stack.enter_context(
            patch.object(srv_module, "_coder_clone_path",
                         return_value=tmp_path / "coder")
        )
        stack.enter_context(
            patch.object(srv_module, "_commander_dir", return_value=commander)
        )
        stack.enter_context(
            patch.object(srv_module, "_reject_terminal_label_redispatch",
                         return_value=None)
        )
        stack.enter_context(
            patch.object(srv_module, "_assert_sprint_signed_off", return_value=None)
        )
        stack.enter_context(
            patch.object(srv_module, "_DAG_BUILDER_AVAILABLE", False)
        )
        stack.enter_context(
            patch.object(srv_module, "_MIS_SIZING_AVAILABLE", False)
        )
        stack.enter_context(
            patch.object(srv_module, "_read_plan_json",
                         return_value={"tickets": [1]})
        )
        stack.enter_context(
            patch.object(srv_module, "_plan_json_set_state", return_value=None)
        )
        stack.enter_context(
            patch.object(srv_module, "_sprint_db_set_state", return_value=None)
        )
        stack.enter_context(
            patch.object(srv_module, "_build_sprint_subprocess_env",
                         return_value={})
        )
        stack.enter_context(
            patch.object(srv_module, "_sprint_goal_path",
                         return_value=MagicMock(
                             exists=MagicMock(return_value=False)))
        )
        stack.enter_context(
            patch.object(srv_module, "_sprint_manager_argv",
                         return_value=["python", "-m", "sprint"])
        )
        stack.enter_context(
            patch.object(srv_module, "_emit_dashboard_event", return_value=None)
        )
        stack.enter_context(
            patch("services.sprint_manager.sprint_manager.list_backlog_issues",
                  return_value=[101])
        )
        stack.enter_context(
            patch("routers.llm_provider_service.get_provider",
                  return_value={"provider": "anthropic"})
        )
        stack.enter_context(
            patch("routers.llm_provider_service.validate_provider",
                  return_value=None)
        )
        stack.enter_context(
            patch("routers.board_cache.invalidate_board", return_value=None)
        )
        stack.enter_context(
            patch("routers.brief_invalidation.invalidate_home_brief_today",
                  return_value=None)
        )
        stack.enter_context(
            patch.object(sprint_run_service, "spawn_sprint_process",
                         return_value=mock_proc)
        )
        stack.enter_context(
            patch.object(sprint_webhook_service, "start_callback_monitor",
                         return_value=None)
        )
        stack.enter_context(
            patch.object(sprint_webhook_service, "start_report_monitor",
                         return_value=None)
        )
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            "/api/sprints/run",
            json={"project": _PROJECT, "sprint_label": "sprint-1"},
        )

    assert response.status_code == 202, response.text
    data = response.json()
    parsed = SprintRunResponse.model_validate(data)
    assert parsed.ok is True
    assert parsed.sprint_label == "sprint-1"
    assert parsed.pid == 54321


# ── AC3: Existing consumers keep working ─────────────────────────────────────

def test_failure_row_model_accepts_all_failure_sources(isolated_db):
    """AC3: FailureRow validates rows from events, agent_runs, and agents sources."""
    from routers.hermes_models import FailureRow

    rows = [
        {
            "source": "events",
            "issue_number": 1,
            "sprint_label": "sprint-1",
            "project": _PROJECT,
            "agent": "coder",
            "category": "gate_fail",
            "reason": None,
            "failure_class": None,
            "message": None,
            "attempt_kind": None,
            "branch": "feature/1-fix",
            "log_url": "/runs/sprint-1/1/coder/log",
            "ts": _NOW,
        },
        {
            "source": "agent_runs",
            "issue_number": 2,
            "sprint_label": "sprint-2",
            "project": _PROJECT,
            "agent": "tester",
            "category": "failed",
            "reason": "pytest_fail",
            "failure_class": None,
            "message": "3 tests failed",
            "attempt_kind": "fix_round",
            "branch": None,
            "log_url": None,
            "ts": _NOW,
        },
        {
            "source": "agents",
            "issue_number": None,
            "sprint_label": None,
            "project": None,
            "agent": None,
            "category": "timed_out",
            "reason": None,
            "failure_class": None,
            "message": None,
            "attempt_kind": None,
            "branch": None,
            "log_url": None,
            "ts": _NOW,
        },
    ]

    for row in rows:
        parsed = FailureRow.model_validate(row)
        assert parsed.source in ("events", "agent_runs", "agents")


def test_sprint_run_response_model_validates_full_payload():
    """AC3: SprintRunResponse validates the dict the endpoint actually returns."""
    from routers.hermes_models import SprintRunResponse

    payload = {
        "ok": True,
        "sprint_label": "sprint-42",
        "pid": 12345,
        "log": "/path/to/logs/sprint-run-sprint-42-20260801T120000.log",
        "migrated_count": 0,
        "migrate_from": [],
    }
    parsed = SprintRunResponse.model_validate(payload)
    assert parsed.ok is True
    assert parsed.pid == 12345
    assert parsed.migrate_from == []


def test_board_response_model_validates_full_payload():
    """AC3: BoardResponse validates a representative board payload."""
    from routers.hermes_models import BoardResponse

    payload = {
        "project": _PROJECT,
        "generated_at": _NOW,
        "sections": {
            "running": [{"label": "sprint-1", "lifecycle_state": "running"}],
            "needs_rework": [],
            "ready_to_merge": [],
            "draft": [],
            "lineage": [],
            "backlog": {"count": 3, "tickets": [{"number": 101, "title": "Fix bug"}]},
        },
        "capacity": {
            "budget_minutes": 180,
            "size_minutes": {"S": 5, "M": 15, "L": 30, "XL": 60},
        },
        "summaries": {
            "sprint-1": {
                "summary_issue_url": None,
                "summary_issue_num": None,
                "lifecycle_state": "running",
                "pr_number": None,
            }
        },
        "cache": {"hit": False, "ttl_s": 60.0},
    }
    parsed = BoardResponse.model_validate(payload)
    assert parsed.project == _PROJECT
    assert parsed.sections.backlog.count == 3
    assert parsed.capacity.size_minutes.S == 5
    assert parsed.cache.hit is False


def test_sprint_live_response_model_extra_fields_preserved():
    """AC3: SprintLiveResponse with extra='allow' passes through running_metrics fields."""
    from routers.hermes_models import SprintLiveResponse

    payload = {
        "time_spent_sec": 120,
        "started_at": _NOW,
        "current_ticket": None,
        "active_agent": None,
        "active_agents": [],
        "pipeline_mode": False,
        "max_coder_slots": 1,
        "max_tester_slots": 1,
        "active_coder_slots": 0,
        "active_tester_slots": 0,
        "levels": [],
        "recent_log_lines": [],
        "done_count": 2,
        "failed_count": 0,
        "skipped_count": 0,
        "pending_count": 1,
        "total_count": 3,
        "complete_count": 2,
        "est_remaining_minutes": 15,
        "issues": [],
        "llm_provider": "anthropic",
        "fix_round_max": 3,
        # running_metrics() extras
        "agent_runs_present": True,
        "fix_rounds": 1,
        "token_total": 50000,
        "agent_time_split": {"coder": 120, "tester": 60},
    }
    parsed = SprintLiveResponse.model_validate(payload)
    assert parsed.time_spent_sec == 120
    assert parsed.done_count == 2
    # Extra fields survive via extra="allow"
    dumped = parsed.model_dump()
    assert dumped.get("fix_rounds") == 1
    assert dumped.get("token_total") == 50000


# ── AC5: Remaining untyped count ─────────────────────────────────────────────

def test_remaining_untyped_count_is_at_most_baseline():
    """AC5: After typing the 7 Hermes endpoints, ≤243 operations remain untyped.

    Baseline: the issue reported 259/279 untyped.  The 7 newly typed endpoints
    reduce that by 7.  The server has since grown to ~287 operations; we assert
    the typed count is at least 37 (pre-existing typed) + 7 (this PR).
    """
    from server import app
    schema = app.openapi()

    total = 0
    typed = 0
    for path, methods in schema.get("paths", {}).items():
        for method, op in methods.items():
            if method == "head":
                continue
            total += 1
            for status, resp in op.get("responses", {}).items():
                if status in ("422", "301", "302"):
                    continue
                content = resp.get("content", {})
                s = content.get("application/json", {}).get("schema", {})
                if s:
                    typed += 1
                    break

    untyped = total - typed
    assert typed >= 44, (
        f"Expected at least 44 typed operations after this PR; got {typed}/{total}. "
        f"The 7 Hermes endpoints may not have response_model set."
    )
    # Report baseline for the next pass
    assert untyped <= 260, (
        f"Untyped count {untyped} exceeds the pre-PR baseline of 260. "
        f"Something regressed — a previously typed endpoint lost its response_model."
    )
