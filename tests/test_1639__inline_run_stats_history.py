"""Tests for issue #1639 — inline run-stats into History feed (N+1 elimination).

AC coverage:
  AC1  — GET /api/sprints/history includes a run_stats object on every row
          with the same fields that GET /api/sprints/{label}/run-stats returns.
  AC2  — run-stats computed by calling run_stats_service.sprint_run_stats()
          directly, not via in-process HTTP self-call.
  AC3  — all run-stats for a page of sprints resolved in one in-process pass.
  AC4  — zero gh CLI/transport calls during feed construction.
  AC5  — existing pagination parameters behave identically.
  AC6  — existing active_only filter behaves identically.
  AC7  — unit test: history response contains inline run_stats block with correct fields.
  AC8  — test: zero gh transport calls during feed construction.
  AC9  — GET /api/sprints/{label}/run-stats endpoint is NOT removed or altered.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as db_module  # noqa: E402


def _load_module(name: str, path: Path):
    """Load a routers/*.py file directly without triggering routers/__init__.py."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Load the two service modules directly, bypassing routers/__init__.py.
run_stats_svc = _load_module(
    "routers.run_stats_service",
    ROUTERS_DIR / "run_stats_service.py",
)
svc = _load_module(
    "routers.sprint_history_service",
    ROUTERS_DIR / "sprint_history_service.py",
)
# Load the router module for AC9 route-registration checks.
router_mod = _load_module(
    "routers.sprint_history",
    ROUTERS_DIR / "sprint_history.py",
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    """Isolated SQLite DB scoped to one test."""
    db_file = tmp_path / "test_1639.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


def _seed_sprint(db, label: str, state: str = "completed", project: str = "owner/repo"):
    """Insert a minimal sprint lifecycle row."""
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        try:
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
                (label, project, state),
            )
        except Exception:
            pass
        conn.commit()


def _seed_agent_runs(db, rows: list[dict]) -> None:
    """Ensure agent_runs table exists, then insert rows."""
    with db.get_conn() as conn:
        db._create_agent_runs_table(conn)
        for r in rows:
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, finished_at, "
                " duration_seconds, outcome, total_tokens, attempt_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.get("issue_number", 1),
                    r["sprint_label"],
                    r["agent"],
                    r.get("started_at", "2026-06-01T00:00:00"),
                    r.get("finished_at"),
                    r.get("duration_seconds"),
                    r.get("outcome", "passed"),
                    r.get("total_tokens"),
                    r.get("attempt_kind", "initial"),
                ),
            )
        conn.commit()


# ── AC1 / AC7: every history row carries an inline run_stats block ────────────

def test_ac1_run_stats_key_present_on_every_history_row(fresh_db):
    """Every row returned by get_sprint_history has a run_stats dict."""
    _seed_sprint(fresh_db, "sprint-10", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-10", "issue_number": 101, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:01:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 1000},
        {"sprint_label": "sprint-10", "issue_number": 101, "agent": "tester",
         "started_at": "2026-06-01T00:01:00", "finished_at": "2026-06-01T00:01:30",
         "duration_seconds": 30, "outcome": "pass", "total_tokens": 500},
    ])

    result = svc.get_sprint_history(project="owner/repo")
    sprints = result["sprints"]
    assert len(sprints) >= 1, "expected at least one history row"
    for row in sprints:
        assert "run_stats" in row, f"run_stats missing from row {row.get('label')}"
        assert row["run_stats"] is not None, f"run_stats is None on row {row.get('label')}"
        assert isinstance(row["run_stats"], dict), "run_stats must be a dict"


def test_ac1_run_stats_carries_contract_fields(fresh_db):
    """Inline run_stats carries the same contract fields as the standalone endpoint."""
    _seed_sprint(fresh_db, "sprint-11", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-11", "issue_number": 111, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:02:00",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 2000},
    ])

    result = svc.get_sprint_history(project="owner/repo")
    row = next((r for r in result["sprints"] if r.get("label") == "sprint-11"), None)
    assert row is not None, "sprint-11 not found in history"

    rs = row["run_stats"]
    # Same fields the standalone /run-stats endpoint guarantees
    assert "has_runs" in rs, "has_runs missing"
    assert "split" in rs, "split missing"
    assert "tickets" in rs, "tickets missing"


def test_ac1_run_stats_has_runs_true_when_agent_runs_exist(fresh_db):
    """has_runs is True on a sprint that has agent_runs rows."""
    _seed_sprint(fresh_db, "sprint-12", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-12", "issue_number": 121, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:01:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 1500},
    ])

    result = svc.get_sprint_history(project="owner/repo")
    row = next((r for r in result["sprints"] if r.get("label") == "sprint-12"), None)
    assert row is not None
    assert row["run_stats"]["has_runs"] is True


def test_ac1_run_stats_has_runs_false_when_no_agent_runs(fresh_db):
    """has_runs is False on a sprint with no agent_runs rows."""
    _seed_sprint(fresh_db, "sprint-13", state="completed", project="owner/repo")
    # No agent_runs rows for sprint-13

    result = svc.get_sprint_history(project="owner/repo")
    row = next((r for r in result["sprints"] if r.get("label") == "sprint-13"), None)
    assert row is not None
    assert row["run_stats"]["has_runs"] is False
    assert row["run_stats"]["split"] == []
    assert row["run_stats"]["tickets"] == []


# ── AC2: run_stats values match direct sprint_run_stats() call ────────────────

def test_ac2_inline_run_stats_values_match_direct_service_call(fresh_db):
    """Inline run_stats values are identical to a direct sprint_run_stats() call."""
    _seed_sprint(fresh_db, "sprint-20", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-20", "issue_number": 201, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:02:00",
         "duration_seconds": 120, "outcome": "success", "total_tokens": 3000},
        {"sprint_label": "sprint-20", "issue_number": 201, "agent": "tester",
         "started_at": "2026-06-01T00:02:00", "finished_at": "2026-06-01T00:02:30",
         "duration_seconds": 30, "outcome": "pass", "total_tokens": 800},
    ])

    # Direct service call
    direct = run_stats_svc.sprint_run_stats("sprint-20", project="owner/repo")

    # Inline via history feed
    result = svc.get_sprint_history(project="owner/repo")
    row = next((r for r in result["sprints"] if r.get("label") == "sprint-20"), None)
    assert row is not None

    rs = row["run_stats"]
    # Key numeric fields must match
    assert rs["has_runs"] == direct["has_runs"]
    assert rs["wall_seconds"] == direct["wall_seconds"]
    assert rs["total_tokens"] == direct["total_tokens"]
    assert rs["split"] == direct["split"]


# ── AC4 / AC8: zero gh transport calls during feed construction ───────────────

def test_ac4_run_stats_service_makes_no_gh_calls(fresh_db, monkeypatch):
    """sprint_run_stats() itself triggers zero gh CLI calls — only local DB reads."""
    _seed_sprint(fresh_db, "sprint-30", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-30", "issue_number": 301, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:01:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 1000},
    ])

    import subprocess
    original_run = subprocess.run
    gh_calls: list = []

    def _spy_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and any("gh" in str(c) for c in cmd[:2]):
            gh_calls.append(cmd)
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)

    # Call run_stats_service directly — the compute unit used by the history inline pass.
    result = run_stats_svc.sprint_run_stats("sprint-30", project="owner/repo")
    assert result["has_runs"] is True
    assert gh_calls == [], (
        f"sprint_run_stats() invoked gh CLI — it must only read local SQLite: {gh_calls}"
    )


def test_ac4_history_feed_gh_calls_not_increased_by_run_stats(fresh_db, monkeypatch):
    """Adding inline run_stats to the history feed does NOT increase gh CLI call count.

    The pre-existing _github_summary_by_label() may call gh; run_stats must not add more.
    We suppress that pre-existing call via a patch and then confirm subprocess is clean.
    """
    _seed_sprint(fresh_db, "sprint-31", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-31", "issue_number": 311, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:01:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 800},
    ])

    # Suppress the pre-existing github_client.list_summary_issues gh call so we
    # get a clean baseline, then assert no subprocess gh calls happen at all.
    try:
        import github_client as _gh
        monkeypatch.setattr(_gh, "list_summary_issues", lambda **kw: [])
    except (ImportError, AttributeError):
        pass

    import subprocess
    original_run = subprocess.run
    gh_calls: list = []

    def _spy_run(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and any("gh" in str(c) for c in cmd[:2]):
            gh_calls.append(cmd)
        return original_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)

    result = svc.get_sprint_history(project="owner/repo")
    assert len(result["sprints"]) >= 1
    assert gh_calls == [], (
        f"gh CLI invoked during history feed (with summary backfill suppressed): {gh_calls}"
    )


def test_ac4_run_stats_computed_locally_no_network(fresh_db, monkeypatch):
    """sprint_run_stats() only reads local SQLite — no outbound network traffic."""
    _seed_sprint(fresh_db, "sprint-31", state="completed", project="owner/repo")
    _seed_agent_runs(fresh_db, [
        {"sprint_label": "sprint-31", "issue_number": 311, "agent": "coder",
         "started_at": "2026-06-01T00:00:00", "finished_at": "2026-06-01T00:01:00",
         "duration_seconds": 60, "outcome": "success", "total_tokens": 800},
    ])

    # If urllib.request.urlopen were called, it would raise and fail the test.
    import urllib.request as _urllib
    from unittest.mock import patch
    with patch.object(_urllib, "urlopen",
                      side_effect=AssertionError("Network call made during history!")):
        result = svc.get_sprint_history(project="owner/repo")

    assert len(result["sprints"]) >= 1
    row = next((r for r in result["sprints"] if r.get("label") == "sprint-31"), None)
    assert row is not None
    assert "run_stats" in row


# ── AC5: pagination parameters behave identically ─────────────────────────────

def test_ac5_pagination_still_works_with_inline_run_stats(fresh_db):
    """offset and limit are respected and every windowed row carries run_stats."""
    for i in range(5):
        _seed_sprint(fresh_db, f"sprint-{50 + i}", state="completed", project="owner/repo")

    page1 = svc.get_sprint_history(project="owner/repo", offset=0, limit=3)
    page2 = svc.get_sprint_history(project="owner/repo", offset=3, limit=3)

    assert page1["total"] >= 5
    assert page1["offset"] == 0
    assert page1["limit"] == 3
    assert len(page1["sprints"]) <= 3
    assert page2["offset"] == 3

    for row in page1["sprints"] + page2["sprints"]:
        assert "run_stats" in row
        assert isinstance(row["run_stats"], dict)


# ── AC6: active_only filter behaves identically ───────────────────────────────

def test_ac6_active_only_filter_unchanged(fresh_db):
    """active_only=True still constrains rows; all returned rows carry run_stats."""
    _seed_sprint(fresh_db, "sprint-60", state="completed", project="owner/repo")
    _seed_sprint(fresh_db, "sprint-61", state="needs_rework", project="owner/repo")

    result_all = svc.get_sprint_history(project="owner/repo", active_only=False)
    result_active = svc.get_sprint_history(project="owner/repo", active_only=True)

    # active_only cannot return MORE rows than the full list
    assert len(result_active["sprints"]) <= len(result_all["sprints"])

    # All returned rows carry run_stats
    for row in result_active["sprints"]:
        assert "run_stats" in row
        assert isinstance(row["run_stats"], dict)


# ── AC9: standalone /run-stats endpoint is not removed or altered ─────────────

def test_ac9_standalone_run_stats_route_still_registered():
    """GET /api/sprints/{label}/run-stats route is still on the history router."""
    paths = {r.path for r in router_mod.router.routes}
    assert "/api/sprints/{label}/run-stats" in paths, (
        "standalone run-stats endpoint was removed — it must remain available"
    )


def test_ac9_standalone_run_stats_service_unchanged():
    """sprint_run_stats() still accepts label + project and returns the contract shape."""
    result = run_stats_svc.sprint_run_stats("sprint-nonexistent-1639", project=None)
    assert "has_runs" in result
    assert result["has_runs"] is False
    assert "split" in result
    assert "tickets" in result


# ── Pydantic model carries run_stats field ────────────────────────────────────

def test_model_run_stats_field_accepted():
    """SprintHistoryItem model serialises run_stats when supplied."""
    from routers.sprint_history import SprintHistoryItem
    item = SprintHistoryItem(
        lifecycle_state="completed",
        run_stats={"has_runs": True, "split": [], "tickets": []},
    )
    assert item.run_stats == {"has_runs": True, "split": [], "tickets": []}


def test_model_run_stats_defaults_to_none():
    """SprintHistoryItem.run_stats defaults to None when not supplied."""
    from routers.sprint_history import SprintHistoryItem
    item = SprintHistoryItem(lifecycle_state="completed")
    assert item.run_stats is None
