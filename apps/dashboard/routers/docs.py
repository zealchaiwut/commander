"""Read-only docs endpoints (issue #1859).

Routes:
  GET /api/projects/{slug}/docs           — list allowed .md files
  GET /api/projects/{slug}/docs/{path}    — fetch file content as JSON
"""
from fastapi import APIRouter

from . import docs_service

router = APIRouter(tags=["docs"])


@router.get("/api/projects/{slug}/docs")
def list_project_docs(slug: str):
    """Return [{path, size, mtime}] for all .md docs in the project's clone root."""
    clone_root = docs_service.resolve_clone_root(slug)
    return docs_service.list_docs(clone_root)


@router.get("/api/projects/{slug}/docs/{path:path}")
def get_project_doc(slug: str, path: str):
    """Return {path, content} for an allowed .md file in the project's clone root."""
    clone_root = docs_service.resolve_clone_root(slug)
    return docs_service.get_doc(clone_root, path)
