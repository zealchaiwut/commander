"""Tests for issue #1797 — cap and dedup slug list in batch todos endpoint.

Coverage:
  AC1 (dedup): duplicate slugs in the projects param are deduplicated so
       todo_repo.list_todos is called only once per unique slug.
  AC2 (400 on over-limit): a slug list exceeding the max returns HTTP 400.
  AC3 (at-limit ok): a slug list exactly at the max still returns HTTP 200.
  AC4 (dedup response shape): deduplicated slugs produce a single key in the
       response (not one key per occurrence).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import services.sprint_manager.todo_repo as todo_repo  # noqa: E402
from services.sprint_manager.models import ProjectTodo  # noqa: E402
from routers import todos as todos_router_mod  # noqa: E402

MAX_BATCH_SLUGS = todos_router_mod.MAX_BATCH_SLUGS


def _make_engine(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}")
    ProjectTodo.__table__.create(bind=engine, checkfirst=True)
    return engine


@pytest.fixture()
def db_file(tmp_path):
    return tmp_path / "todos.db"


@pytest.fixture()
def bound_repo(db_file, tmp_path, monkeypatch):
    fallback = tmp_path / "project_todos_store.json"
    monkeypatch.setattr(todo_repo, "_fallback_store_path", lambda: fallback)
    engine = _make_engine(db_file)
    monkeypatch.setattr(todo_repo, "_session_factory", sessionmaker(bind=engine))
    yield engine


@pytest.fixture()
def client(bound_repo):
    app = FastAPI()
    app.include_router(todos_router_mod.batch_router)
    return TestClient(app, raise_server_exceptions=False)


# ── AC1: dedup — list_todos called once per unique slug ───────────────────────

def test_duplicate_slugs_deduplicated_calls_repo_once(client):
    """Passing the same slug twice must not call list_todos twice for it."""
    call_count = []

    original = todo_repo.list_todos

    def counting_list_todos(slug):
        call_count.append(slug)
        return original(slug)

    with patch.object(todo_repo, "list_todos", side_effect=counting_list_todos):
        r = client.get("/api/todos?projects=alpha,alpha,alpha")

    assert r.status_code == 200
    assert call_count.count("alpha") == 1, (
        f"list_todos called {call_count.count('alpha')} times for 'alpha', expected 1"
    )


def test_duplicate_slugs_single_key_in_response(client):
    """Duplicate slugs must collapse to a single key in the JSON response."""
    todo_repo.create_todo("beta", "only once")
    r = client.get("/api/todos?projects=beta,beta")
    assert r.status_code == 200
    data = r.json()
    assert list(data.keys()) == ["beta"], (
        f"expected single 'beta' key, got: {list(data.keys())}"
    )
    assert len(data["beta"]) == 1


def test_mixed_duplicates_and_uniques_deduped(client):
    """Mixed repeated and unique slugs deduplicate correctly."""
    call_args = []

    original = todo_repo.list_todos

    def recording(slug):
        call_args.append(slug)
        return original(slug)

    with patch.object(todo_repo, "list_todos", side_effect=recording):
        r = client.get("/api/todos?projects=x,y,x,z,y")

    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"x", "y", "z"}
    assert call_args.count("x") == 1
    assert call_args.count("y") == 1
    assert call_args.count("z") == 1


# ── AC2: 400 on over-limit ────────────────────────────────────────────────────

def test_over_limit_returns_400(client):
    """Requesting more than MAX_BATCH_SLUGS unique slugs returns HTTP 400."""
    slugs = ",".join(f"slug-{i}" for i in range(MAX_BATCH_SLUGS + 1))
    r = client.get(f"/api/todos?projects={slugs}")
    assert r.status_code == 400


def test_over_limit_error_message_mentions_max(client):
    """The 400 error body must mention the cap so callers know the limit."""
    slugs = ",".join(f"slug-{i}" for i in range(MAX_BATCH_SLUGS + 1))
    r = client.get(f"/api/todos?projects={slugs}")
    assert r.status_code == 400
    body = r.json()
    assert str(MAX_BATCH_SLUGS) in str(body), (
        f"Expected cap ({MAX_BATCH_SLUGS}) in 400 response body: {body}"
    )


# ── AC3: at-limit is accepted ─────────────────────────────────────────────────

def test_at_limit_returns_200(client):
    """Exactly MAX_BATCH_SLUGS unique slugs must be accepted (200, not 400)."""
    slugs = ",".join(f"slug-{i}" for i in range(MAX_BATCH_SLUGS))
    r = client.get(f"/api/todos?projects={slugs}")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == MAX_BATCH_SLUGS


def test_at_limit_with_duplicates_deduped_within_limit(client):
    """Duplicates that reduce to <= MAX_BATCH_SLUGS unique slugs must be accepted."""
    unique_slugs = [f"slug-{i}" for i in range(MAX_BATCH_SLUGS)]
    # Add one repeated slug — still MAX_BATCH_SLUGS unique after dedup
    repeated = unique_slugs[0]
    slugs_param = ",".join(unique_slugs + [repeated])
    r = client.get(f"/api/todos?projects={slugs_param}")
    assert r.status_code == 200
    assert len(r.json()) == MAX_BATCH_SLUGS
