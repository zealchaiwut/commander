"""Sprint number must not be reused when a ledger row already exists (sprint-99)."""
from __future__ import annotations

import json
import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import db  # noqa: E402

if "routers" not in sys.modules:
    stub = types.ModuleType("routers")
    stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
    sys.modules["routers"] = stub

_spec = importlib.util.spec_from_file_location("startup", DASHBOARD_DIR / "startup.py")
startup = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["startup"] = startup
_spec.loader.exec_module(startup)  # type: ignore[union-attr]

from apps.dashboard.routers import sprints_service  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "reservation.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


def test_used_sprint_numbers_includes_history_and_lifecycle(fresh_db, monkeypatch):
    """AC: sprint-99 in sprint_history blocks reuse even without a GitHub label."""
    repo = "zealchaiwut/commander"
    monkeypatch.setattr(startup.github_client, "list_sprints", lambda repo_name=None: [98])
    monkeypatch.setattr(startup, "_finished_sprint_summaries", lambda repo_name=None: {})

    fresh_db.record_sprint_history(
        label="sprint-99",
        project=repo,
        lifecycle_state="failed",
        end_reason="process_lost",
        duration=60,
        tokens=0,
        issues=[],
    )

    used = startup._used_sprint_numbers(repo)
    assert 98 in used
    assert 99 in used
    assert startup._next_new_sprint_number(repo) == 100
    assert startup._sprint_number_reserved(repo, 99) is True
    assert startup._sprint_number_reserved(repo, 100) is False


def test_create_sprint_rejects_reserved_number(fresh_db, monkeypatch):
    """AC: create_sprint_verified returns 409 when number is in history."""
    repo = "zealchaiwut/commander"
    fake_srv = MagicMock()
    fake_srv.github_client.list_sprints.return_value = []
    fake_srv._finished_sprint_summaries.return_value = {}
    fake_srv._project_root_path.return_value = Path("/tmp/fake-project")
    fake_srv._gh_error.side_effect = lambda e: e
    fake_srv._sprint_number_reserved = startup._sprint_number_reserved
    fake_srv._next_new_sprint_number = startup._next_new_sprint_number
    monkeypatch.setattr(sprints_service, "_server", lambda: fake_srv)

    fresh_db.record_sprint_history(
        label="sprint-99",
        project=repo,
        lifecycle_state="failed",
        end_reason="process_lost",
        duration=60,
        tokens=0,
        issues=[],
    )

    with pytest.raises(HTTPException) as exc:
        sprints_service.create_sprint_verified(repo, sprint_number=99)
    assert exc.value.status_code == 409
    assert "99" in str(exc.value.detail)


def test_history_newest_record_wins_for_same_label(fresh_db, monkeypatch, tmp_path):
    """AC: a new lifecycle sprint-99 row replaces an older sprint_history snapshot."""
    spec = importlib.util.spec_from_file_location(
        "routers.sprint_history_service", ROUTERS_DIR / "sprint_history_service.py",
    )
    shs = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["routers.sprint_history_service"] = shs
    spec.loader.exec_module(shs)  # type: ignore[union-attr]

    repo = "zealchaiwut/commander"
    sprints_dir = tmp_path / "sprints"
    sprints_dir.mkdir()
    (sprints_dir / "sprint-99-plan.json").write_text(
        json.dumps({
            "state": "draft",
            "project": repo,
            "started_at": "2026-06-15T10:00:00+00:00",
            "tickets": [1429, 1435],
        }),
        encoding="utf-8",
    )

    fresh_db.record_sprint_history(
        label="sprint-99",
        project=repo,
        lifecycle_state="failed",
        end_reason="process_lost",
        duration=60,
        tokens=0,
        issues=[
            {"ticket_id": 1, "state": "merged"},
            {"ticket_id": 11, "state": "merged"},
        ],
        created_at="2024-01-01T00:00:00+00:00",
    )
    with fresh_db.get_conn() as conn:
        fresh_db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at, started_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "sprint-99",
                repo,
                "draft",
                "2026-06-15T10:00:00+00:00",
                "2026-06-15T10:00:00+00:00",
            ),
        )
        conn.commit()

    out = shs.get_sprint_history(project=repo, sprints_dir=sprints_dir, limit=50)
    rows = [r for r in out["sprints"] if r.get("label") == "sprint-99"]
    assert len(rows) == 1
    row = rows[0]
    issue_ids = {i.get("ticket_id") for i in row.get("issues") or []}
    assert 1 not in issue_ids
    assert 11 not in issue_ids
