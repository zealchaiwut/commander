"""Per-project to-do list endpoints (issue #843).

A lightweight, durable scratchpad scoped to each project, living outside the
ticket backlog. Five thin CRUD routes over :mod:`services.sprint_manager.todo_repo`,
which persists to Neon when configured and to a local JSON store otherwise.

All routes are project-scoped: a todo is only ever read, mutated, or deleted
through its own project's path, so one project's todos are never visible to
another (#843 AC6). The reserved ``promoted_issue_number`` column is never read,
written, or exposed here (#843 AC10).

New endpoints live in this router module, not ``server.py`` (COMMANDER_GATE_MONOLITH,
issue #761).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from services.sprint_manager import todo_repo

router = APIRouter(prefix="/api/projects", tags=["todos"])


# ── models ────────────────────────────────────────────────────────────────────

class TodoOut(BaseModel):
    """The public shape of a todo — deliberately not ticket-like.

    No labels, assignees, or due dates, and no ``promoted_issue_number``: a todo
    is just text, a done flag, and an ordering position (#843 AC9, AC10).
    """

    id: int
    project: str
    text: str
    done: bool
    position: int
    created_at: str
    updated_at: str


class TodoCreate(BaseModel):
    text: str


class TodoUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None
    position: Optional[int] = None


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/{project}/todos", response_model=list[TodoOut])
def list_project_todos(project: str):
    """Return all todos (active and done) for the project, ascending by position."""
    return todo_repo.list_todos(project)


@router.post("/{project}/todos", response_model=TodoOut, status_code=201)
def create_project_todo(project: str, body: TodoCreate):
    """Create a todo with the given text; ``done`` defaults to False."""
    return todo_repo.create_todo(project, body.text)


@router.patch("/{project}/todos/{todo_id}", response_model=TodoOut)
def update_project_todo(project: str, todo_id: int, body: TodoUpdate):
    """Toggle done, edit text, and/or reorder — independently or together."""
    fields = body.model_dump(exclude_unset=True)
    updated = todo_repo.update_todo(project, todo_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return updated


@router.delete("/{project}/todos/{todo_id}", status_code=204)
def delete_project_todo(project: str, todo_id: int):
    """Remove the specified todo."""
    if not todo_repo.delete_todo(project, todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return Response(status_code=204)
