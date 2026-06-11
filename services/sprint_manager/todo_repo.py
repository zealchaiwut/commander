"""Per-project to-do list storage (issue #843).

A lightweight, durable scratchpad scoped to each project, living outside the
ticket backlog. Backed by the Neon ``project_todos`` table when configured, and
by a local JSON store when Neon is disabled (``COMMANDER_DISABLE_NEON=1`` /
``DATABASE_URL`` unset) — so the dashboard serves todos fully off local state
and never 500s, exactly like :mod:`settings_repo`.

The repo is the single source of truth for the todo *shape* exposed to the API:
``id, project, text, done, position, created_at, updated_at``. The reserved
``promoted_issue_number`` column is never read, written, or returned here (#843
AC10) — it is left NULL on insert and dropped from every returned dict.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func

from services.sprint_manager.models import ProjectTodo
from services.sprint_manager.neon_db import get_session as _neon_get_session

# Sentinel for "field not supplied" so a PATCH can update done / text / position
# independently — None is a legitimate value we must not confuse with "omitted".
_UNSET = object()

# Tests can replace this with a sessionmaker bound to a test engine.
_session_factory = None


def _open_session():
    if _session_factory is not None:
        return _session_factory()
    return _neon_get_session()


def _try_open_session():
    """Return an open session, or None when no backing DB is configured.

    When Neon is disabled, ``get_engine()`` raises; in that mode todo reads and
    writes degrade to the local JSON store rather than 500ing.
    """
    try:
        return _open_session()
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _orm_to_dict(t: ProjectTodo) -> dict:
    """Project an ORM row to the public API shape (no promoted_issue_number)."""
    return {
        "id": t.id,
        "project": t.project,
        "text": t.text,
        "done": bool(t.done),
        "position": t.position,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


# ── Local JSON fallback store (used when Neon is unavailable) ─────────────────
# One file for the whole dashboard; each record carries its own project field so
# isolation is enforced the same way the SQL ``WHERE project = ?`` does.
_fallback_lock = threading.Lock()


def _fallback_store_path() -> Path:
    # todo_repo.py lives at <repo>/services/sprint_manager/todo_repo.py
    root = Path(__file__).resolve().parent.parent.parent
    d = root / ".commander"
    d.mkdir(parents=True, exist_ok=True)
    return d / "project_todos_store.json"


def _fallback_read() -> dict:
    p = _fallback_store_path()
    if not p.exists():
        return {"todos": [], "next_id": 1}
    try:
        data = json.loads(p.read_text())
        data.setdefault("todos", [])
        data.setdefault("next_id", 1)
        return data
    except Exception:
        return {"todos": [], "next_id": 1}


def _fallback_write(data: dict) -> None:
    try:
        _fallback_store_path().write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _fallback_to_dict(rec: dict) -> dict:
    """Project a fallback record to the public API shape (drops reserved field)."""
    return {
        "id": rec["id"],
        "project": rec["project"],
        "text": rec["text"],
        "done": bool(rec["done"]),
        "position": rec["position"],
        "created_at": rec["created_at"],
        "updated_at": rec["updated_at"],
    }


def _fallback_next_position(todos: list[dict], project: str) -> int:
    positions = [t["position"] for t in todos if t["project"] == project]
    return (max(positions) + 1) if positions else 0


# ── Public API ────────────────────────────────────────────────────────────────

def list_todos(project: str) -> list[dict]:
    """All todos for *project*, ascending by position (then id for stability)."""
    cm = _try_open_session()
    if cm is None:
        with _fallback_lock:
            data = _fallback_read()
        rows = [t for t in data["todos"] if t["project"] == project]
        rows.sort(key=lambda t: (t["position"], t["id"]))
        return [_fallback_to_dict(t) for t in rows]
    with cm as session:
        rows = (
            session.query(ProjectTodo)
            .filter_by(project=project)
            .order_by(ProjectTodo.position.asc(), ProjectTodo.id.asc())
            .all()
        )
        return [_orm_to_dict(r) for r in rows]


def create_todo(project: str, text: str) -> dict:
    """Create a todo; ``done`` defaults to False and ``position`` appends to end."""
    now = _now()
    cm = _try_open_session()
    if cm is None:
        with _fallback_lock:
            data = _fallback_read()
            rec = {
                "id": data["next_id"],
                "project": project,
                "text": text,
                "done": False,
                "position": _fallback_next_position(data["todos"], project),
                "created_at": now,
                "updated_at": now,
                "promoted_issue_number": None,
            }
            data["todos"].append(rec)
            data["next_id"] += 1
            _fallback_write(data)
        return _fallback_to_dict(rec)
    with cm as session:
        max_pos = (
            session.query(func.max(ProjectTodo.position))
            .filter_by(project=project)
            .scalar()
        )
        next_pos = 0 if max_pos is None else max_pos + 1
        todo = ProjectTodo(
            project=project,
            text=text,
            done=False,
            position=next_pos,
            created_at=now,
            updated_at=now,
            promoted_issue_number=None,
        )
        session.add(todo)
        session.commit()
        session.refresh(todo)
        return _orm_to_dict(todo)


def update_todo(
    project: str,
    todo_id: int,
    *,
    text=_UNSET,
    done=_UNSET,
    position=_UNSET,
) -> Optional[dict]:
    """Update done / text / position independently or together.

    Returns the updated todo, or None when no todo with *todo_id* exists for
    *project* (the project filter also enforces cross-project isolation — you
    can never mutate another project's todo).
    """
    now = _now()
    cm = _try_open_session()
    if cm is None:
        with _fallback_lock:
            data = _fallback_read()
            rec = next(
                (t for t in data["todos"]
                 if t["id"] == todo_id and t["project"] == project),
                None,
            )
            if rec is None:
                return None
            if text is not _UNSET:
                rec["text"] = text
            if done is not _UNSET:
                rec["done"] = bool(done)
            if position is not _UNSET:
                rec["position"] = position
            rec["updated_at"] = now
            _fallback_write(data)
            return _fallback_to_dict(rec)
    with cm as session:
        todo = (
            session.query(ProjectTodo)
            .filter_by(id=todo_id, project=project)
            .first()
        )
        if todo is None:
            return None
        if text is not _UNSET:
            todo.text = text
        if done is not _UNSET:
            todo.done = bool(done)
        if position is not _UNSET:
            todo.position = position
        todo.updated_at = now
        session.commit()
        session.refresh(todo)
        return _orm_to_dict(todo)


def delete_todo(project: str, todo_id: int) -> bool:
    """Delete a todo. Returns True if removed, False if it didn't exist.

    Scoped by project so one project can never delete another's todo.
    """
    cm = _try_open_session()
    if cm is None:
        with _fallback_lock:
            data = _fallback_read()
            before = len(data["todos"])
            data["todos"] = [
                t for t in data["todos"]
                if not (t["id"] == todo_id and t["project"] == project)
            ]
            removed = len(data["todos"]) < before
            if removed:
                _fallback_write(data)
            return removed
    with cm as session:
        todo = (
            session.query(ProjectTodo)
            .filter_by(id=todo_id, project=project)
            .first()
        )
        if todo is None:
            return False
        session.delete(todo)
        session.commit()
        return True
