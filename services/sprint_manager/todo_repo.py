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
import logging
import os
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import func

from services.sprint_manager.commander_paths import discover_commander_dir
from services.sprint_manager.models import ProjectTodo
from services.sprint_manager.neon_db import get_session as _neon_get_session
from services.sprint_manager import todo_attachment_repo as _attach_repo

logger = logging.getLogger("commander.todo_repo")

# Sentinel for "field not supplied" so a PATCH can update done / text / position
# independently — None is a legitimate value we must not confuse with "omitted".
_UNSET = object()

# Tests can replace this with a sessionmaker bound to a test engine.
_session_factory = None


def _open_session():
    if _session_factory is not None:
        return _session_factory()
    return _neon_get_session()


def _neon_enabled() -> bool:
    """Neon is optional — UAT runs with COMMANDER_DISABLE_NEON=1 even when DATABASE_URL is set."""
    flag = os.environ.get("COMMANDER_DISABLE_NEON", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return False
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _try_open_session():
    """Return an open session, or None when Neon is disabled / unavailable.

    When Neon is disabled, todo reads and writes use the local JSON store so
    todos never split across Neon and JSON (which made lists appear empty).
    """
    if not _neon_enabled():
        return None
    try:
        return _open_session()
    except Exception:
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _orm_to_dict(t: ProjectTodo, attachments: list[dict] | None = None) -> dict:
    """Project an ORM row to the public API shape (no promoted_issue_number)."""
    return {
        "id": t.id,
        "project": t.project,
        "text": t.text,
        "done": bool(t.done),
        "position": t.position,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "attachments": attachments or [],
    }


# ── Local JSON fallback store (used when Neon is unavailable) ─────────────────
# One file for the whole dashboard at the project .commander dir (nested-layout
# aware via discover_commander_dir). Each record carries its own project field.
_fallback_lock = threading.Lock()
_CLONE_ROOT = Path(__file__).resolve().parent.parent.parent


def _fallback_store_path() -> Path:
    d = discover_commander_dir(Path(__file__).resolve())
    d.mkdir(parents=True, exist_ok=True)
    return d / "project_todos_store.json"


def _empty_fallback_data() -> dict:
    return {"todos": [], "next_id": 1}


def _normalize_fallback_data(data: dict) -> dict:
    data.setdefault("todos", [])
    data.setdefault("next_id", 1)
    if data["todos"]:
        max_id = max(int(t.get("id") or 0) for t in data["todos"])
        if int(data["next_id"]) <= max_id:
            data["next_id"] = max_id + 1
    return data


def _maybe_migrate_legacy_clone_store(canonical: Path) -> None:
    """Merge todos from clone-local ``.commander/`` into the project store once."""
    legacy = _CLONE_ROOT / ".commander" / "project_todos_store.json"
    if not legacy.is_file() or legacy.resolve() == canonical.resolve():
        return
    try:
        leg = _normalize_fallback_data(json.loads(legacy.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("legacy todo store unreadable at %s: %s", legacy, exc)
        return
    if not leg["todos"]:
        return
    if canonical.is_file():
        try:
            can = _normalize_fallback_data(
                json.loads(canonical.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, OSError):
            can = _empty_fallback_data()
    else:
        can = _empty_fallback_data()
    seen = {(t["project"], t["id"]) for t in can["todos"]}
    for rec in leg["todos"]:
        key = (rec.get("project"), rec.get("id"))
        if key not in seen:
            can["todos"].append(rec)
            seen.add(key)
    can["next_id"] = max(can["next_id"], leg["next_id"])
    _fallback_write_atomic(canonical, can)
    try:
        migrated = legacy.with_suffix(".json.migrated")
        legacy.rename(migrated)
        logger.info("merged legacy todos from %s into %s", legacy, canonical)
    except OSError as exc:
        logger.warning("could not rename legacy todo store %s: %s", legacy, exc)


def _fallback_read() -> dict:
    p = _fallback_store_path()
    _maybe_migrate_legacy_clone_store(p)
    if not p.is_file():
        return _empty_fallback_data()

    for candidate in (p, p.with_suffix(".json.bak")):
        if not candidate.is_file():
            continue
        try:
            return _normalize_fallback_data(
                json.loads(candidate.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("todo store unreadable at %s: %s", candidate, exc)

    logger.error(
        "todo store corrupt at %s — refusing writes that would wipe data",
        p,
    )
    return {**_empty_fallback_data(), "_read_only": True}


def _fallback_write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {k: v for k, v in data.items() if k != "_read_only"}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        shutil.copy2(path, path.with_suffix(".json.bak"))
    except OSError as exc:
        logger.warning("todo store backup failed: %s", exc)


def _fallback_write(data: dict) -> None:
    if data.get("_read_only"):
        logger.error("todo store write skipped — last read failed (data would be lost)")
        return
    try:
        _fallback_write_atomic(_fallback_store_path(), data)
    except OSError as exc:
        logger.error("todo store write failed: %s", exc)


def _fallback_to_dict(rec: dict, attachments: list[dict] | None = None) -> dict:
    """Project a fallback record to the public API shape (drops reserved field)."""
    return {
        "id": rec["id"],
        "project": rec["project"],
        "text": rec["text"],
        "done": bool(rec["done"]),
        "position": rec["position"],
        "created_at": rec["created_at"],
        "updated_at": rec["updated_at"],
        "attachments": attachments or [],
    }


def _attach_map(project: str) -> dict[int, list[dict]]:
    return _attach_repo.list_for_project(project)


def _fallback_next_position(todos: list[dict], project: str) -> int:
    positions = [t["position"] for t in todos if t["project"] == project]
    return (max(positions) + 1) if positions else 0


# ── Public API ────────────────────────────────────────────────────────────────

def list_todos(project: str) -> list[dict]:
    """All todos for *project*, ascending by position (then id for stability)."""
    attach_by_id = _attach_map(project)
    cm = _try_open_session()
    if cm is None:
        with _fallback_lock:
            data = _fallback_read()
        rows = [t for t in data["todos"] if t["project"] == project]
        rows.sort(key=lambda t: (t["position"], t["id"]))
        return [_fallback_to_dict(t, attach_by_id.get(t["id"], [])) for t in rows]
    with cm as session:
        rows = (
            session.query(ProjectTodo)
            .filter_by(project=project)
            .order_by(ProjectTodo.position.asc(), ProjectTodo.id.asc())
            .all()
        )
        return [_orm_to_dict(r, attach_by_id.get(r.id, [])) for r in rows]


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
        return _fallback_to_dict(rec, [])
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
        return _orm_to_dict(todo, [])


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
            return _fallback_to_dict(rec, _attach_repo.list_for_todo(project, todo_id))
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
        return _orm_to_dict(todo, _attach_repo.list_for_todo(project, todo_id))


def clear_done_todos(project: str) -> int:
    """Delete every done todo for *project*. Returns the number removed."""
    cm = _try_open_session()
    if cm is None:
        with _fallback_lock:
            data = _fallback_read()
            before = len(data["todos"])
            removed_ids = [
                t["id"] for t in data["todos"]
                if t["project"] == project and t["done"]
            ]
            data["todos"] = [
                t for t in data["todos"]
                if not (t["project"] == project and t["done"])
            ]
            removed = before - len(data["todos"])
            if removed:
                _fallback_write(data)
                for tid in removed_ids:
                    _attach_repo.delete_all_for_todo(project, tid)
            return removed
    with cm as session:
        removed = (
            session.query(ProjectTodo)
            .filter_by(project=project, done=True)
            .delete(synchronize_session=False)
        )
        session.commit()
        return removed


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
                _attach_repo.delete_all_for_todo(project, todo_id)
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
        _attach_repo.delete_all_for_todo(project, todo_id)
        return True
