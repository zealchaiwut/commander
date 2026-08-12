"""Tests for issue #629: Instrument dashboard routes to emit causal action_id events.

AC-1: bulk_created event emitted when bulk job posts tickets, with target=list of ticket IDs
AC-2: ticket_moved event emitted when ticket moved via assign endpoints, with from_sprint/to_sprint
AC-3: ticket_moved action_id threads to label_added/label_removed events
AC-4: sprint_created event emitted with sprint_name in payload
AC-5: sprint_run event emitted with sprint_id in payload
AC-6: sprint_cancelled event emitted with sprint_id in payload
AC-7: label_added/label_removed events emitted alongside ticket_moved with same action_id
AC-8: All events include source="dashboard" and actor field
AC-9: No more than one top-level event per dashboard action
AC-10: action_id is non-empty unique string (UUID) generated at dashboard action point
"""
from __future__ import annotations

import ast
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

_SERVER_SRC = SERVER_PY.read_text()


# ── helpers ───────────────────────────────────────────────────────────────────

def make_capture():
    """Return (calls_list, capture_fn) for monkeypatching db.record_event."""
    calls = []

    def capture(project, source, actor, type, target, detail, action_id=None):
        calls.append({
            "project": project,
            "source": source,
            "actor": actor,
            "type": type,
            "target": target,
            "detail": detail,
            "action_id": action_id,
        })

    return calls, capture


def _find_emit_calls(src: str, func_name: str) -> list[dict]:
    """AST-parse server source; return info about func_name(...) calls."""
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match bare function calls like _emit_dashboard_event(...)
        if isinstance(func, ast.Name) and func.id == func_name:
            pass
        # Also match attribute calls like db.record_event(...)
        elif isinstance(func, ast.Attribute) and func.attr == func_name:
            pass
        else:
            continue
        record: dict = {"lineno": node.lineno, "kwargs": {}, "args": []}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            val_node = kw.value
            if isinstance(val_node, ast.Constant):
                record["kwargs"][kw.arg] = val_node.value
            else:
                record["kwargs"][kw.arg] = "__dynamic__"
        for arg in node.args:
            if isinstance(arg, ast.Constant):
                record["args"].append(arg.value)
            else:
                record["args"].append("__dynamic__")
        calls.append(record)
    return calls


_ALL_EMIT_CALLS = _find_emit_calls(_SERVER_SRC, "_emit_dashboard_event")

# Aggregate emit calls across server.py + all router modules.
# Event emissions migrated from server.py into routers in issue #1267+;
# scanning only server.py misses them.
_ROUTERS_DIR = DASHBOARD_DIR / "routers"
_ALL_DASHBOARD_EMIT_CALLS: list[dict] = list(_ALL_EMIT_CALLS)
_DASHBOARD_COMBINED_SRC = _SERVER_SRC
for _py in sorted(_ROUTERS_DIR.glob("*.py")):
    _src = _py.read_text()
    _ALL_DASHBOARD_EMIT_CALLS.extend(_find_emit_calls(_src, "_emit_dashboard_event"))
    _DASHBOARD_COMBINED_SRC += "\n" + _src


def _record_event_types() -> set[str]:
    """Set of event type strings statically passed to _emit_dashboard_event() anywhere in the dashboard."""
    types = set()
    for call in _ALL_DASHBOARD_EMIT_CALLS:
        t = call["kwargs"].get("type") or (call["args"][1] if len(call["args"]) > 1 else None)
        if t and t != "__dynamic__":
            types.add(t)
    return types


# ── AC-static: record_event called for each required event type ───────────────

class TestStaticEventTypesPresent:
    """Verify _emit_dashboard_event() calls exist in the dashboard codebase for each required event type.

    Note: AC-5 (sprint_run) and AC-6 (sprint_cancelled) removed in issue #2250 —
    those routes were deleted along with sprint_run.py.
    """

    def test_sprint_created_event_type_present(self):
        assert "sprint_created" in _record_event_types(), (
            "dashboard missing _emit_dashboard_event call with type='sprint_created'"
        )

    def test_ticket_moved_event_type_present(self):
        assert "ticket_moved" in _record_event_types(), (
            "dashboard missing _emit_dashboard_event call with type='ticket_moved'"
        )

    def test_bulk_created_event_type_present(self):
        assert "bulk_created" in _record_event_types(), (
            "dashboard missing _emit_dashboard_event call with type='bulk_created'"
        )

    def test_label_added_event_type_present(self):
        assert "label_added" in _record_event_types(), (
            "dashboard missing _emit_dashboard_event call with type='label_added'"
        )

    def test_label_removed_event_type_present(self):
        assert "label_removed" in _record_event_types(), (
            "dashboard missing _emit_dashboard_event call with type='label_removed'"
        )

    def test_source_dashboard_appears_in_calls(self):
        """_emit_dashboard_event is called somewhere in the dashboard codebase."""
        assert _ALL_DASHBOARD_EMIT_CALLS, (
            "_emit_dashboard_event() is never called in server.py or any router"
        )
        assert (
            "source=\"dashboard\"" in _DASHBOARD_COMBINED_SRC
            or "source='dashboard'" in _DASHBOARD_COMBINED_SRC
        ), "No source='dashboard' literal found in dashboard codebase"

    def test_action_id_uses_uuid4(self):
        """Server source contains uuid.uuid4() for action_id generation."""
        assert "uuid.uuid4()" in _SERVER_SRC, (
            "Expected uuid.uuid4() usage for action_id generation in server.py"
        )

    def test_bulk_created_call_near_post_task(self):
        """bulk_created _emit_dashboard_event call exists near bulk_post_selected in bulk_tickets.py."""
        bulk_tickets_py = _ROUTERS_DIR / "bulk_tickets.py"
        assert bulk_tickets_py.exists(), "routers/bulk_tickets.py not found"
        bulk_src = bulk_tickets_py.read_text()
        tree = ast.parse(bulk_src)
        bulk_post_lineno = None
        bulk_post_end = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
                if node.name == "bulk_post_selected":
                    bulk_post_lineno = node.lineno
                    bulk_post_end = getattr(node, "end_lineno", node.lineno + 200)
                    break

        assert bulk_post_lineno is not None, "bulk_post_selected function not found in bulk_tickets.py"

        bulk_emit_calls = _find_emit_calls(bulk_src, "_emit_dashboard_event")
        bulk_created_lines = [
            c["lineno"] for c in bulk_emit_calls
            if c["kwargs"].get("type") == "bulk_created"
        ]
        assert bulk_created_lines, "No _emit_dashboard_event(type='bulk_created') call found in bulk_tickets.py"

        in_range = any(bulk_post_lineno <= ln <= bulk_post_end + 50 for ln in bulk_created_lines)
        assert in_range, (
            f"bulk_created event call at line(s) {bulk_created_lines} is not inside "
            f"bulk_post_selected (lines {bulk_post_lineno}–{bulk_post_end})"
        )


# ── Runtime tests: fresh server import + TestClient + mocks ──────────────────

@pytest.fixture
def server_module(tmp_path, monkeypatch):
    """Import server freshly; patch DB + GitHub so no real I/O happens."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    # Patch db.record_event so we can capture calls
    calls, capture = make_capture()
    monkeypatch.setattr(srv.db, "record_event", capture)

    # Silence structured logger
    monkeypatch.setattr(srv._slog, "event", lambda *a, **kw: None)

    return srv, calls, tmp_path


# ── AC-4: sprint_created ──────────────────────────────────────────────────────

def _sprint_create_patches(srv, project_root):
    """Common patches for sprint create tests (disables Neon, mocks GitHub)."""
    return (
        patch.object(srv.github_client, "list_sprints", return_value=[]),
        patch.object(srv.github_client, "create_sprint_label_strict", return_value=None),
        patch.object(srv.github_client, "sprint_label_exists", return_value=True),
        patch.object(srv.github_client, "invalidate", return_value=None),
        patch("server._project_root_path", return_value=project_root),
        patch("server._plan_json_set_state", return_value=None),
    )


class TestSprintCreatedEvent:
    """AC-4: POST /api/sprints/create emits sprint_created event."""

    def test_sprint_created_event_emitted(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        proj = "owner/testrepo"
        project_root = tmp_path / "testrepo"
        project_root.mkdir(parents=True)

        with (
            patch.object(srv.github_client, "list_sprints", return_value=[]),
            # Verified create (issue #857) uses the strict label create + an
            # existence check; let the real plan write run so step-3 verification
            # passes. Assertions below are unchanged.
            patch.object(srv.github_client, "create_sprint_label_strict", return_value=None),
            patch.object(srv.github_client, "sprint_label_exists", return_value=True),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch("server._project_root_path", return_value=project_root),
            patch("server._plan_json_set_state", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post("/api/sprints/create", json={"project": proj})

        assert resp.status_code == 200, resp.text
        sprint_created = [c for c in calls if c["type"] == "sprint_created"]
        assert len(sprint_created) == 1, f"Expected 1 sprint_created event, got {len(sprint_created)}"

    def test_sprint_created_has_sprint_name_in_detail(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        proj = "owner/testrepo"
        project_root = tmp_path / "testrepo"
        project_root.mkdir(parents=True)

        with (
            patch.object(srv.github_client, "list_sprints", return_value=[]),
            # Verified create (issue #857) uses the strict label create + an
            # existence check; let the real plan write run so step-3 verification
            # passes. Assertions below are unchanged.
            patch.object(srv.github_client, "create_sprint_label_strict", return_value=None),
            patch.object(srv.github_client, "sprint_label_exists", return_value=True),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch("server._project_root_path", return_value=project_root),
            patch("server._plan_json_set_state", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post("/api/sprints/create", json={"project": proj})

        assert resp.status_code == 200, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_created")
        assert "sprint_name" in ev["detail"], "sprint_created event missing sprint_name in detail"
        assert ev["detail"]["sprint_name"].startswith("sprint-")

    def test_sprint_created_source_is_dashboard(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        proj = "owner/testrepo"
        project_root = tmp_path / "testrepo"
        project_root.mkdir(parents=True)

        with (
            patch.object(srv.github_client, "list_sprints", return_value=[]),
            # Verified create (issue #857) uses the strict label create + an
            # existence check; let the real plan write run so step-3 verification
            # passes. Assertions below are unchanged.
            patch.object(srv.github_client, "create_sprint_label_strict", return_value=None),
            patch.object(srv.github_client, "sprint_label_exists", return_value=True),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch("server._project_root_path", return_value=project_root),
            patch("server._plan_json_set_state", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post("/api/sprints/create", json={"project": proj})

        assert resp.status_code == 200, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_created")
        assert ev["source"] == "dashboard"
        assert ev["actor"] is not None and ev["actor"] != ""

    def test_sprint_created_action_id_is_uuid(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        proj = "owner/testrepo"
        project_root = tmp_path / "testrepo"
        project_root.mkdir(parents=True)

        with (
            patch.object(srv.github_client, "list_sprints", return_value=[]),
            # Verified create (issue #857) uses the strict label create + an
            # existence check; let the real plan write run so step-3 verification
            # passes. Assertions below are unchanged.
            patch.object(srv.github_client, "create_sprint_label_strict", return_value=None),
            patch.object(srv.github_client, "sprint_label_exists", return_value=True),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch("server._project_root_path", return_value=project_root),
            patch("server._plan_json_set_state", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post("/api/sprints/create", json={"project": proj})

        assert resp.status_code == 200, resp.text
        ev = next(c for c in calls if c["type"] == "sprint_created")
        action_id = ev["action_id"]
        assert action_id and action_id != ""
        uuid.UUID(action_id)

    def test_sprint_created_only_one_top_level_event(self, server_module):
        """AC-9: exactly one top-level event per sprint create."""
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        proj = "owner/testrepo"
        project_root = tmp_path / "testrepo"
        project_root.mkdir(parents=True)

        with (
            patch.object(srv.github_client, "list_sprints", return_value=[]),
            # Verified create (issue #857) uses the strict label create + an
            # existence check; let the real plan write run so step-3 verification
            # passes. Assertions below are unchanged.
            patch.object(srv.github_client, "create_sprint_label_strict", return_value=None),
            patch.object(srv.github_client, "sprint_label_exists", return_value=True),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch("server._project_root_path", return_value=project_root),
            patch("server._plan_json_set_state", return_value=None),
        ):
            client = TestClient(srv.app)
            client.post("/api/sprints/create", json={"project": proj})

        assert len(calls) == 1, f"Expected 1 event for sprint create, got {len(calls)}: {[c['type'] for c in calls]}"
        assert calls[0]["type"] == "sprint_created"


# ── AC-2 / AC-3 / AC-7: ticket_moved + label events ─────────────────────────
# Note: AC-5 (TestSprintRunEvent) and AC-6 (TestSprintCancelledEvent) removed
# in issue #2250 — they tested POST /api/sprints/run and DELETE /api/sprints/run/{label}
# which were deleted with sprint_run.py.

class TestTicketMovedEvent:
    """AC-2: ticket_moved event when ticket moved via /api/sprint-planning/assign."""

    def _fake_issue(self, sprint_label="sprint-3"):
        return {
            "number": 42,
            "title": "Test issue",
            "labels": [{"name": sprint_label}, {"name": "enhancement"}],
        }

    def test_ticket_moved_emitted_on_assign(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue()),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "assign_sprint", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprint-planning/assign",
                json={"issue": 42, "sprint_label": "sprint-4"},
            )

        assert resp.status_code == 200
        moved = [c for c in calls if c["type"] == "ticket_moved"]
        assert len(moved) == 1

    def test_ticket_moved_has_from_sprint_and_to_sprint(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue("sprint-3")),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "assign_sprint", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprint-planning/assign",
                json={"issue": 42, "sprint_label": "sprint-4"},
            )

        assert resp.status_code == 200
        ev = next(c for c in calls if c["type"] == "ticket_moved")
        assert "from_sprint" in ev["detail"]
        assert "to_sprint" in ev["detail"]
        assert ev["detail"]["to_sprint"] == "sprint-4"

    def test_ticket_moved_action_id_is_uuid(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue()),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "assign_sprint", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprint-planning/assign",
                json={"issue": 42, "sprint_label": "sprint-4"},
            )

        assert resp.status_code == 200
        ev = next(c for c in calls if c["type"] == "ticket_moved")
        assert ev["action_id"] is not None
        uuid.UUID(ev["action_id"])

    def test_label_events_share_action_id_with_ticket_moved(self, server_module):
        """AC-3: label_added/label_removed share action_id with ticket_moved."""
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue("sprint-3")),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "assign_sprint", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprint-planning/assign",
                json={"issue": 42, "sprint_label": "sprint-4"},
            )

        assert resp.status_code == 200
        moved = next(c for c in calls if c["type"] == "ticket_moved")
        label_events = [c for c in calls if c["type"] in ("label_added", "label_removed")]
        assert label_events, "Expected label_added or label_removed events alongside ticket_moved"
        for lev in label_events:
            assert lev["action_id"] == moved["action_id"], (
                f"label event action_id {lev['action_id']!r} != ticket_moved action_id {moved['action_id']!r}"
            )

    def test_ticket_moved_source_is_dashboard(self, server_module):
        """AC-8: ticket_moved event has source='dashboard' and actor."""
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue()),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "assign_sprint", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprint-planning/assign",
                json={"issue": 42, "sprint_label": "sprint-4"},
            )

        assert resp.status_code == 200
        ev = next(c for c in calls if c["type"] == "ticket_moved")
        assert ev["source"] == "dashboard"
        assert ev["actor"] is not None and ev["actor"] != ""

    def test_ticket_moved_to_backlog(self, server_module):
        """ticket_moved event when moving to backlog (sprint_label=null)."""
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue("sprint-3")),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "assign_sprint", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprint-planning/assign",
                json={"issue": 42, "sprint_label": None, "sprint": None},
            )

        assert resp.status_code == 200
        moved = [c for c in calls if c["type"] == "ticket_moved"]
        assert len(moved) == 1
        assert moved[0]["detail"]["to_sprint"] in (None, "backlog", "")


# ── AC-5 via sprint-label endpoint ────────────────────────────────────────────

class TestAddSprintLabelEvent:
    """AC-2: POST /api/issues/{issue_id}/sprint-label also emits ticket_moved."""

    def _fake_issue(self, sprint="sprint-2"):
        return {
            "number": 55,
            "title": "Test",
            "labels": [{"name": sprint}],
        }

    def test_sprint_label_endpoint_emits_ticket_moved(self, server_module):
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue()),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/issues/55/sprint-label",
                json={"sprint_label": "sprint-7"},
            )

        assert resp.status_code == 200
        moved = [c for c in calls if c["type"] == "ticket_moved"]
        assert len(moved) == 1
        assert moved[0]["detail"]["to_sprint"] == "sprint-7"

    def test_sprint_label_endpoint_label_events_share_action_id(self, server_module):
        """AC-3 / AC-7: label_added/label_removed share action_id with ticket_moved."""
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue("sprint-2")),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/issues/55/sprint-label",
                json={"sprint_label": "sprint-7"},
            )

        assert resp.status_code == 200
        moved = next(c for c in calls if c["type"] == "ticket_moved")
        label_evs = [c for c in calls if c["type"] in ("label_added", "label_removed")]
        assert label_evs, "Expected label events alongside ticket_moved"
        for lev in label_evs:
            assert lev["action_id"] == moved["action_id"]

    def test_sprint_label_endpoint_source_is_dashboard(self, server_module):
        """AC-8: source='dashboard' on ticket_moved."""
        srv, calls, tmp_path = server_module
        from fastapi.testclient import TestClient

        with (
            patch.object(srv.github_client, "get_issue", return_value=self._fake_issue()),
            patch.object(srv.github_client, "assign_sprint_by_label", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/issues/55/sprint-label",
                json={"sprint_label": "sprint-7"},
            )

        assert resp.status_code == 200
        ev = next(c for c in calls if c["type"] == "ticket_moved")
        assert ev["source"] == "dashboard"
        assert ev["actor"]


# AC-10: TestActionIdUniqueness removed in issue #2250 — it tested action_id
# uniqueness across sprint runs, which was in the deleted sprint_run.py.
