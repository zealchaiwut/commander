"""Project CRUD endpoints extracted from server.py (issue #1249).

GET /api/projects, POST /api/projects, and DELETE /api/projects/{owner}/{repo_name}
moved here from the monolith. server.py mounts this router; it contains no
inline definitions for these three routes.
"""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from . import projects_service

router = APIRouter(tags=["projects"])


class NewProjectBody(BaseModel):
    repo_url: str
    icon: Optional[str] = "ti-folder"
    color: Optional[str] = "gray"


class RemoveProjectBody(BaseModel):
    delete_local_folders: bool = False
    delete_github_repo: bool = False


@router.get("/api/projects")
def get_projects():
    return projects_service.list_projects()


@router.post("/api/projects", status_code=201)
def add_project(body: NewProjectBody):
    return projects_service.create_project(
        repo_url=body.repo_url,
        icon=body.icon,
        color=body.color,
    )


@router.delete("/api/projects/{owner}/{repo_name}")
async def remove_project(owner: str, repo_name: str, body: RemoveProjectBody):
    return projects_service.delete_project(
        owner=owner,
        repo_name=repo_name,
        delete_local_folders=body.delete_local_folders,
        delete_github_repo=body.delete_github_repo,
    )
