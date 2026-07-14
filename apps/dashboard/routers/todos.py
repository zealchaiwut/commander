"""Per-project to-do list endpoints (issue #843).

A lightweight, durable scratchpad scoped to each project, living outside the
ticket backlog. CRUD routes over :mod:`services.sprint_manager.todo_repo`,
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

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from services.sprint_manager import todo_attachment_repo, todo_repo

router = APIRouter(prefix="/api/projects", tags=["todos"])


# ── models ────────────────────────────────────────────────────────────────────

class TodoAttachmentOut(BaseModel):
    name: str
    mime_type: str
    size: int
    created_at: str
    url: str


class TodoOut(BaseModel):
    """The public shape of a todo — deliberately not ticket-like.

    No labels, assignees, or due dates, and no ``promoted_issue_number``: a todo
    is just text, a done flag, ordering position, and optional image attachments.
    """

    id: int
    project: str
    text: str
    done: bool
    position: int
    created_at: str
    updated_at: str
    attachments: list[TodoAttachmentOut] = []


class TodoCreate(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be empty or whitespace-only")
        return v


class TodoUpdate(BaseModel):
    text: Optional[str] = None
    done: Optional[bool] = None
    position: Optional[int] = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("text must not be empty or whitespace-only")
        return v


def _attach_url(project: str, todo_id: int, name: str) -> str:
    return (
        f"/api/projects/{project}/todos/{todo_id}/attachments/"
        f"{name}"
    )


def _enrich_attachments(project: str, todo_id: int, rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        out.append({
            **row,
            "url": _attach_url(project, todo_id, row["name"]),
        })
    return out


def _todo_out(raw: dict) -> dict:
    tid = raw["id"]
    project = raw["project"]
    raw = dict(raw)
    raw["attachments"] = _enrich_attachments(
        project, tid, raw.get("attachments") or [],
    )
    return raw


# ── routes ────────────────────────────────────────────────────────────────────

@router.get("/{project}/todos", response_model=list[TodoOut])
def list_project_todos(project: str):
    """Return all todos (active and done) for the project, ascending by position."""
    return [_todo_out(t) for t in todo_repo.list_todos(project)]


@router.post("/{project}/todos", response_model=TodoOut, status_code=201)
def create_project_todo(project: str, body: TodoCreate):
    """Create a todo with the given text; ``done`` defaults to False."""
    return _todo_out(todo_repo.create_todo(project, body.text))


@router.delete("/{project}/todos/done")
def clear_done_project_todos(project: str):
    """Remove every completed todo for the project."""
    deleted = todo_repo.clear_done_todos(project)
    return {"deleted": deleted}


@router.patch("/{project}/todos/{todo_id}", response_model=TodoOut)
def update_project_todo(project: str, todo_id: int, body: TodoUpdate):
    """Toggle done, edit text, and/or reorder — independently or together."""
    fields = body.model_dump(exclude_unset=True)
    updated = todo_repo.update_todo(project, todo_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    return _todo_out(updated)


@router.delete("/{project}/todos/{todo_id}", status_code=204)
def delete_project_todo(project: str, todo_id: int):
    """Remove the specified todo."""
    if not todo_repo.delete_todo(project, todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return Response(status_code=204)


@router.post("/{project}/todos/{todo_id}/attachments", response_model=TodoAttachmentOut)
async def upload_todo_attachment(
    project: str,
    todo_id: int,
    file: UploadFile = File(...),
):
    """Attach a screenshot or image to a to-do item (local disk storage)."""
    todos = todo_repo.list_todos(project)
    if not any(t["id"] == todo_id for t in todos):
        raise HTTPException(status_code=404, detail="Todo not found")

    content = await file.read()
    filename = file.filename or "screenshot.png"
    mime = file.content_type or "image/png"
    try:
        row = todo_attachment_repo.add_attachment(
            project, todo_id, filename, content, mime_type=mime,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        **row,
        "url": _attach_url(project, todo_id, row["name"]),
    }


@router.get("/{project}/todos/{todo_id}/attachments/{filename}")
def get_todo_attachment(project: str, todo_id: int, filename: str):
    """Serve an attached image for a to-do item."""
    path = todo_attachment_repo.resolve_file(project, todo_id, filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    return FileResponse(path)


@router.delete("/{project}/todos/{todo_id}/attachments/{filename}", status_code=204)
def delete_todo_attachment(project: str, todo_id: int, filename: str):
    """Remove one attachment from a to-do item."""
    if not todo_attachment_repo.delete_attachment(project, todo_id, filename):
        raise HTTPException(status_code=404, detail="Attachment not found")
    return Response(status_code=204)


# ── batch endpoint (issue #1778 AC3) ─────────────────────────────────────────
# Separate router so the full path /api/todos is declared without inheriting
# the /api/projects prefix from the per-project router above.

MAX_BATCH_SLUGS = 50

batch_router = APIRouter(tags=["todos-batch"])


@batch_router.get("/api/todos", response_model=dict[str, list[TodoOut]])
def batch_list_todos(projects: str = "") -> dict:
    """Return todos for multiple project slugs in one request (issue #1778 AC3).

    ``projects`` is a comma-separated list of slugs. Unknown slugs are included
    with an empty list so callers don't need to distinguish "not fetched" from
    "has no todos". Duplicate slugs are silently collapsed; more than
    MAX_BATCH_SLUGS unique slugs returns 400 (issue #1797).
    """
    raw = [s.strip() for s in projects.split(",") if s.strip()]
    slugs = list(dict.fromkeys(raw))
    if len(slugs) > MAX_BATCH_SLUGS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many slugs: {len(slugs)} requested, max is {MAX_BATCH_SLUGS}.",
        )
    result: dict[str, list] = {}
    for slug in slugs:
        result[slug] = [_todo_out(t) for t in todo_repo.list_todos(slug)]
    return result
