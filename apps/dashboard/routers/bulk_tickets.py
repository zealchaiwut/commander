"""Bulk ticket routes extracted from server.py (issues #1264, #1267).

GET routes:
  GET /api/tickets/bulk/{job_id}
  GET /api/tickets/bulk/{job_id}/stream

POST routes (moved from server.py in issue #1267):
  POST /api/tickets/draft
  POST /api/tickets/create
  POST /api/tickets/bulk
  POST /api/tickets/bulk/{job_id}/estimate-draft
  POST /api/tickets/bulk/{job_id}/skip
  POST /api/tickets/bulk/{job_id}/retry
  POST /api/tickets/bulk/{job_id}/redraft
  POST /api/tickets/bulk/{job_id}/post-selected
  POST /api/tickets/bulk/{job_id}/retry-with-body
  POST /api/tickets/bulk/{job_id}/retry-with-image
  POST /api/tickets/bulk/{job_id}/retry-all
  POST /api/tickets/bulk/{job_id}/size-remedy-comment
  POST /api/tickets/bulk/{job_id}/size-remedy-images
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from .hermes_models import TicketDraftResponse, TicketCreateResponse
from starlette.responses import StreamingResponse

from .board_cache import invalidate_board  # noqa: E402

router = APIRouter(tags=["bulk_tickets"])


def _server():
    """Deferred import of the monolith — avoids circular import."""
    import server  # noqa: PLC0415
    return server


def _get_bulk_job(job_id: str) -> dict | None:
    """Return job from memory, or lazy-load from disk after restart.

    If found on disk and not in memory, the job is restored to _bulk_jobs so
    subsequent calls hit memory. Returns None if not found anywhere.
    """
    srv = _server()
    job = srv._bulk_jobs.get(job_id)
    if job is not None:
        return job
    # Disk fallback — covers server reload / restart scenarios
    try:
        jobs_dir = srv._bulk_jobs_dir()
        path = jobs_dir / f"{job_id}.json"
        if path.exists():
            job = json.loads(path.read_text(encoding="utf-8"))
            # A job that was 'running' when the server restarted can never
            # resume — its in-flight BA processes are gone. Mark it cancelled
            # so the client gets accurate state instead of a perpetual spinner.
            if job.get("status") == "running":
                srv._bulk_cancel_interrupted(job)
            srv._bulk_jobs[job_id] = job
            return job
    except Exception:
        pass
    return None


@router.get("/api/tickets/bulk/{job_id}")
async def bulk_get_job(job_id: str):
    """Return the current state of a bulk job."""
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        # Try to load from disk
        try:
            path = srv._bulk_jobs_dir() / f"{job_id}.json"
            if path.exists():
                job = json.loads(path.read_text())
                srv._bulk_jobs[job_id] = job
        except Exception:
            pass
    if not job:
        raise HTTPException(404, detail="Job not found")
    # Strip internal fields from response
    tickets = [
        {k: v for k, v in t.items() if not k.startswith("_")}
        for t in job["tickets"]
    ]
    return {
        "job_id": job["job_id"],
        "repo": job.get("repo", ""),
        "status": job["status"],
        "concurrency": job["concurrency"],
        "default_labels": job.get("default_labels", []),
        "tickets": tickets,
    }


@router.get("/api/tickets/bulk/{job_id}/stream")
async def bulk_job_stream(job_id: str, request: Request):
    """SSE stream of state-change events for a bulk job."""
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        # Rehydrate from disk if the job was persisted but evicted from memory
        # (e.g. server restart). Mirrors bulk_get_job so a reconnecting client
        # doesn't get a fatal 404 for a job that still exists on disk.
        try:
            path = srv._bulk_jobs_dir() / f"{job_id}.json"
            if path.exists():
                job = json.loads(path.read_text())
                srv._bulk_jobs[job_id] = job
        except Exception:
            pass
    if not job:
        raise HTTPException(404, detail="Job not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    # Register this subscriber
    if job_id not in srv._bulk_job_queues:
        srv._bulk_job_queues[job_id] = []
    srv._bulk_job_queues[job_id].append(queue)

    async def generator():
        try:
            # On connect: send full current state first
            state_snapshot = await bulk_get_job(job_id)
            yield f"event: snapshot\ndata: {json.dumps(state_snapshot)}\n\n"

            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: update\ndata: {data}\n\n"
                    # Stop streaming if job is done
                    parsed = json.loads(data)
                    if parsed.get("type") == "job_done":
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                except Exception:
                    break
        finally:
            qlist = srv._bulk_job_queues.get(job_id, [])
            if queue in qlist:
                qlist.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Pydantic request body models (moved from server.py, issue #1267) ──────────

class CreateTicketBody(BaseModel):
    draft_id: str = ""
    title: str
    body: str = ""
    project: str = ""
    sprint_label: str = ""
    extra_labels: list[str] = []
    milestone: str = ""


class BulkSkipBody(BaseModel):
    index: int


class BulkEstimateDraftBody(BaseModel):
    index: int
    title: str | None = None
    body: str | None = None


class BulkRetryBody(BaseModel):
    index: int


class BulkRedraftBody(BaseModel):
    index: int


class BulkPostSelectedItem(BaseModel):
    index: int
    labels: list[str] = []
    title: str | None = None
    body: str | None = None


class BulkPostSelectedBody(BaseModel):
    tickets: list[BulkPostSelectedItem]
    sprint_label: str | None = None
    milestone: str | None = None


class BulkRetryWithBodyBody(BaseModel):
    index: int
    body: str


class BulkRetryAllBody(BaseModel):
    bodies: dict[str, str]


class SizeRemedyCommentBody(BaseModel):
    index: int


class SizeRemedyImagesBody(BaseModel):
    index: int


# ── POST /api/tickets/draft ───────────────────────────────────────────────────

@router.post("/api/tickets/draft", response_model=TicketDraftResponse)
async def create_ticket_draft(
    description: str = Form(default=""),
    project: str = Form(default=""),
    sprint_label: str = Form(default=""),
    files: list[UploadFile] = File(default=[]),
):
    srv = _server()
    description = description.strip()
    if not description:
        raise HTTPException(400, detail="Description is required")

    draft_id = str(uuid.uuid4())
    upload_dir = srv._DRAFT_UPLOAD_DIR / draft_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for f in files:
        if not f.filename:
            continue
        ext = Path(f.filename).suffix.lower()
        if ext not in srv._ALLOWED_UPLOAD_EXTS:
            continue
        dest = upload_dir / f.filename
        content = await f.read()
        dest.write_bytes(content)
        saved_paths.append(str(dest))

    file_list = (
        "\n".join(f"  - {p}" for p in saved_paths) if saved_paths else "  (none)"
    )
    prompt = (
        "You are a BA (Business Analyst) agent writing a GitHub issue.\n\n"
        f"User description: {description}\n\n"
        "Write a complete GitHub issue with these sections:\n"
        "  - Title (short, imperative, 5-10 words)\n"
        "  - ## What & Why (1-3 sentences)\n"
        "  - ## Acceptance Criteria (checkbox list, specific and testable)\n"
        "  - ## UAT Test Steps (numbered, each with Expected: line)\n"
        "  - ## Files to touch (optional stub — leave the heading but add no paths; "
        "the developer will fill this in)\n"
        "  - ## Out of Scope (brief list)\n\n"
        f"Reference files — read them and incorporate relevant details:\n{file_list}\n\n"
        'Output ONLY valid JSON with exactly two string fields: "title" and "body".\n'
        "The body field must be GitHub-flavored markdown. No text outside the JSON."
    )

    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    from services.sprint_manager.model_routing import apply_provider_env
    _ba_model = apply_provider_env(
        sub_env, "claude-sonnet-4-6", repo=os.environ.get("COMMANDER_PROJECT"),
        role="ba",
    )
    cmd = [
        "claude",
        "--model", _ba_model,
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=sub_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, detail="BA agent timed out after 180s")
    except FileNotFoundError:
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise HTTPException(503, detail="claude CLI not found")

    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()[:300]
        out = stdout.decode("utf-8", errors="replace").strip()[:300]
        if not err and not out:
            detail = f"exit code {proc.returncode} with no output"
        elif not err:
            detail = f"exit code {proc.returncode}, stdout: {out}"
        else:
            detail = err
        raise HTTPException(502, detail=f"BA agent failed: {detail}")

    output = stdout.decode("utf-8", errors="replace").strip()
    title, body, json_ok = srv._parse_ba_draft(output)
    if not json_ok:
        raise HTTPException(
            502,
            detail=(
                "BA returned malformed JSON — could not parse ticket fields. "
                f"Raw output starts with: {output[:120]!r}"
            ),
        )
    return {"draft_id": draft_id, "title": title, "body": body}


# ── POST /api/tickets/create ──────────────────────────────────────────────────

@router.post("/api/tickets/create", status_code=201, response_model=TicketCreateResponse)
async def create_ticket_from_draft(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Create a ticket from a draft.

    Accepts either application/json or multipart/form-data (for file uploads).
    JSON path uses CreateTicketBody; multipart path supports UploadFile attachments.
    Both paths share the same business logic and response_model.
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        # JSON path — typed Pydantic model, no file uploads
        try:
            raw = await request.json()
        except Exception:
            raise HTTPException(400, detail="Invalid JSON body")
        try:
            payload = CreateTicketBody.model_validate(raw)
        except Exception as exc:
            raise HTTPException(422, detail=str(exc))
        draft_id = payload.draft_id
        title = payload.title.strip()
        body = payload.body
        project = payload.project
        sprint_label = payload.sprint_label
        extra_labels = payload.extra_labels
        milestone = payload.milestone
        files: list[UploadFile] = []
    else:
        # Form path — multipart/form-data or application/x-www-form-urlencoded
        form = await request.form()
        draft_id = (form.get("draft_id") or "")
        title = (form.get("title") or "").strip()
        body = (form.get("body") or "")
        project = (form.get("project") or "")
        sprint_label = (form.get("sprint_label") or "")
        extra_labels = [
            v for v in form.getlist("extra_labels") if isinstance(v, str)
        ]
        milestone = (form.get("milestone") or "")
        files = [v for v in form.getlist("files") if hasattr(v, "filename")]

    srv = _server()
    if not title:
        raise HTTPException(400, detail="Title is required")

    labels: list[str] = ["backlog"]
    if sprint_label:
        labels.append(sprint_label)
    for lbl in extra_labels:
        lbl = lbl.strip()
        if lbl and lbl not in labels:
            labels.append(lbl)

    try:
        number, url = srv.github_client.create_issue(
            title=title, body=body, labels=labels, repo_name=project or None,
            milestone=(milestone or "").strip() or None)
    except Exception as e:
        raise HTTPException(502, detail=f"gh CLI failed: {str(e)[:300]}")

    valid_files = [f for f in files if f.filename]
    if valid_files:
        repo_name = srv.github_client.get_repo_for_operation(project or None)

        batch_size = 0
        file_data_raw: list[tuple[str, bytes]] = []
        for upload in valid_files:
            ext = Path(upload.filename).suffix.lower()
            if ext not in srv._ALLOWED_UPLOAD_EXTS:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' has disallowed extension '{ext}'. "
                           f"Allowed: {', '.join(sorted(srv._ALLOWED_UPLOAD_EXTS))}",
                )
            content = await upload.read()
            if len(content) > srv._MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' exceeds the 25 MB per-file limit "
                           f"({len(content) // (1024*1024)} MB).",
                )
            batch_size += len(content)
            if batch_size > srv._MAX_BATCH_SIZE_BYTES:
                raise HTTPException(422, detail="Upload batch exceeds the 50 MB total limit.")
            file_data_raw.append((upload.filename, content))

        try:
            srv._ensure_attachments_branch(repo_name)
        except Exception as e:
            import logging
            logging.warning(f"Could not ensure attachments branch: {e}")
            file_data_raw = []

        if file_data_raw:
            try:
                cache_dir = srv._init_attachment_cache(repo_name)
            except Exception as e:
                import logging
                logging.warning(f"Could not initialize attachment cache: {e}")
                cache_dir = None

            if cache_dir:
                existing = srv._list_existing_attachments(cache_dir, number)
                file_data: list[tuple[str, bytes]] = []
                used_names: set[str] = set(existing)
                for orig_name, content in file_data_raw:
                    sanitized = srv._sanitize_filename(orig_name)
                    final_name = srv._resolve_collision(sanitized, used_names)
                    used_names.add(final_name)
                    file_data.append((final_name, content))

                push_error: str | None = None
                try:
                    srv._commit_attachments_to_branch(cache_dir, number, file_data)
                except RuntimeError as e:
                    push_error = str(e)

                if push_error is None:
                    owner_repo = repo_name
                    links = "\n".join(
                        f"- [{fname}](https://raw.githubusercontent.com/{owner_repo}/"
                        f"{srv._ATTACHMENTS_BRANCH}/references/issue-{number}/{fname})"
                        for fname, _ in file_data
                    )
                    if "## Attachments" not in body:
                        updated_body = body + f"\n\n## Attachments\n\n{links}"
                    else:
                        updated_body = body
                    srv.github_client.update_issue_body(number, updated_body, repo_name=repo_name)

    if draft_id:
        upload_dir = srv._DRAFT_UPLOAD_DIR / draft_id
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)

    srv.github_client.invalidate("open_issues_body:")
    srv.github_client.invalidate("open_issues:")
    srv.github_client.invalidate("issues:")

    try:
        est_repo = srv.github_client.get_repo_for_operation(project or None)
    except Exception:
        est_repo = None
    if est_repo:
        invalidate_board(est_repo)
        if srv._ESTIMATE_ISSUE_SCRIPT.exists():
            background_tasks.add_task(srv._run_estimator_for_issue, number, est_repo)

    return {"number": number, "url": url}


# ── POST /api/tickets/bulk ────────────────────────────────────────────────────

@router.post("/api/tickets/bulk", status_code=202)
async def bulk_create_start(
    repo: str = Form(...),
    prompts: str = Form(...),
    default_labels: str = Form(default=""),
    concurrency: int = Form(default=3),
    files: list[UploadFile] = File(default=[]),
    assignments: str = Form(default=""),
    sprint_label: str = Form(default=""),
):
    srv = _server()
    srv._prune_old_bulk_jobs()

    try:
        prompts_list: list[str] = json.loads(prompts)
        if not isinstance(prompts_list, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(422, detail="'prompts' must be a JSON array of strings")

    default_labels_list: list[str] = []
    if default_labels.strip():
        try:
            default_labels_list = json.loads(default_labels)
            if not isinstance(default_labels_list, list):
                raise ValueError("not a list")
        except (json.JSONDecodeError, ValueError):
            raise HTTPException(422, detail="'default_labels' must be a JSON array of strings")

    sprint_label = (sprint_label or "").strip()
    if sprint_label and sprint_label != "NEW" and not re.match(r"^sprint-\d+$", sprint_label):
        raise HTTPException(422, detail="'sprint_label' must be empty, 'NEW', or 'sprint-N'")

    projects = srv.projects_module.load_projects()
    if not any(p["repo"] == repo for p in projects):
        raise HTTPException(422, detail=f"Repo '{repo}' is not a configured project")

    if concurrency not in srv._ALLOWED_CONCURRENCY:
        raise HTTPException(422, detail=f"Concurrency must be one of {sorted(srv._ALLOWED_CONCURRENCY)}")

    clean_prompts = [p.strip() for p in prompts_list if p.strip()]
    if not clean_prompts:
        raise HTTPException(422, detail="Batch must contain at least one non-blank prompt")
    if len(clean_prompts) > srv._MAX_BULK_PROMPTS:
        raise HTTPException(
            422,
            detail=f"Batch limit is {srv._MAX_BULK_PROMPTS} prompts (got {len(clean_prompts)})"
        )

    try:
        existing_label_names = sorted(lbl["name"] for lbl in srv.github_client.list_labels(repo_name=repo))
    except Exception:
        existing_label_names = []
    existing_label_set = set(existing_label_names)

    if default_labels_list and existing_label_set:
        bad = [lbl for lbl in default_labels_list if lbl not in existing_label_set]
        if bad:
            raise HTTPException(
                422,
                detail=f"Unknown labels (not in repo): {', '.join(bad)}"
            )

    image_assignments: list[dict] = []
    if assignments.strip():
        try:
            parsed_assignments = json.loads(assignments)
            if not isinstance(parsed_assignments, list):
                raise ValueError("not a list")
            for a in parsed_assignments:
                asgn = a.get("assignment", "all")
                image_assignments.append({
                    "filename": str(a.get("filename", "")),
                    "assignment": asgn if asgn == "all" else int(asgn),
                })
        except (json.JSONDecodeError, ValueError, TypeError):
            raise HTTPException(422, detail="'assignments' must be a JSON array")

    valid_upload_files = [f for f in files if f.filename]
    attachment_file_data: list[tuple[str, bytes]] = []
    if valid_upload_files:
        _accepted_fmt = ".png, .jpg, .jpeg, .md, .html, .htm, .pdf"
        batch_size = 0
        for upload in valid_upload_files:
            ext = Path(upload.filename).suffix.lower()
            if ext not in srv._BULK_ATTACH_EXTS:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' has unsupported extension '{ext}'. "
                           f"Accepted: {_accepted_fmt}.",
                )
            content = await upload.read()
            if len(content) > srv._MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    422,
                    detail=f"File '{upload.filename}' exceeds the 25 MB per-file limit "
                           f"({len(content) // (1024*1024)} MB).",
                )
            batch_size += len(content)
            if batch_size > srv._MAX_BATCH_SIZE_BYTES:
                raise HTTPException(422, detail="Upload batch exceeds the 50 MB total limit.")
            attachment_file_data.append((upload.filename, content))

    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    if attachment_file_data:
        attach_dir = Path(tempfile.mkdtemp(prefix=f"bc_attach_{job_id}_"))
        srv._bulk_attachment_dirs[job_id] = attach_dir
        for orig_name, content in attachment_file_data:
            (attach_dir / Path(orig_name).name).write_bytes(content)

    tickets = [
        {
            "index": i,
            "prompt": prompt,
            "state": "pending",
            "title": None,
            "body": None,
            "body_preview": None,
            "issue_num": None,
            "issue_url": None,
            "label_pills": None,
            "suggested_labels": None,
            "error": None,
            "attachment_warning": None,
            "started_at": None,
            "finished_at": None,
            "retry_count": 0,
            "last_error": None,
        }
        for i, prompt in enumerate(clean_prompts)
    ]

    job = {
        "job_id": job_id,
        "status": "running",
        "repo": repo,
        "default_labels": default_labels_list,
        "sprint_label": sprint_label,
        "allowed_labels": existing_label_names,
        "concurrency": concurrency,
        "created_at": now,
        "stop_requested": False,
        "has_attachments": len(attachment_file_data) > 0,
        "attachment_filenames": [n for n, _ in attachment_file_data],
        "attachment_error": None,
        "image_assignments": image_assignments,
        "image_url_map": None,
        "tickets": tickets,
        "_action_id": str(uuid.uuid4()),
    }
    srv._bulk_jobs[job_id] = job
    srv._bulk_job_queues[job_id] = []
    srv._persist_bulk_job(job)

    asyncio.create_task(srv._run_bulk_job(job_id))

    return {"job_id": job_id}


# ── POST /api/tickets/bulk/{job_id}/estimate-draft ────────────────────────────

@router.post("/api/tickets/bulk/{job_id}/estimate-draft")
async def bulk_estimate_draft(job_id: str, body: BulkEstimateDraftBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket.get("state") != "draft_ready":
        return {"ok": True, "estimating": False}

    if body.title is not None:
        ticket["title"] = body.title
    if body.body is not None:
        ticket["body"] = body.body

    new_hash = srv._draft_body_hash(ticket.get("title") or "", ticket.get("body") or "")
    if ticket.get("estimate_state") == "sized" and ticket.get("estimate_body_hash") == new_hash:
        return {"ok": True, "estimating": False}

    srv._persist_bulk_job(job)
    asyncio.create_task(srv._run_bulk_draft_estimator_for_ticket(job_id, body.index))
    return {"ok": True, "estimating": True}


# ── POST /api/tickets/bulk/{job_id}/skip ─────────────────────────────────────

@router.post("/api/tickets/bulk/{job_id}/skip")
async def bulk_skip_ticket(job_id: str, body: BulkSkipBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] in ("pending", "failed", "size_warning"):
        ticket["state"] = "skipped"
        ticket.pop("_default_labels", None)
        ticket.pop("_repo", None)
        srv._persist_bulk_job(job)
        await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        has_size_warnings = any(t["state"] == "size_warning" for t in job["tickets"])
        if all_done and not has_size_warnings:
            job["status"] = "done"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})
    return {"ok": True, "state": ticket["state"]}


# ── POST /api/tickets/bulk/{job_id}/retry ─────────────────────────────────────

@router.post("/api/tickets/bulk/{job_id}/retry")
async def bulk_retry_ticket(job_id: str, body: BulkRetryBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] not in ("failed", "cancelled"):
        return {"ok": True, "state": ticket["state"]}

    ticket["state"] = "pending"
    ticket["error"] = None
    ticket["started_at"] = None
    ticket["finished_at"] = None
    job["status"] = "running"
    srv._persist_bulk_job(job)
    await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _retry_task():
        await srv._run_single_ba_ticket(
            job_id, body.index, ticket["prompt"],
            job["repo"], job["default_labels"]
        )
        t = job["tickets"][body.index]
        if t.get("state") == "draft_ready":
            body_with_attachments = srv._build_body_with_images(
                t.get("body") or "", body.index, job
            )
            if len(body_with_attachments) > srv._BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_attachments
                t["body_char_count"] = len(body_with_attachments)
                t["body_over_by"] = len(body_with_attachments) - srv._BC_BODY_SIZE_THRESHOLD
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            else:
                t["body"] = body_with_attachments
                t["body_preview"] = body_with_attachments[:200]
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

        all_drafted = all(
            tt["state"] in ("draft_ready", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        if all_drafted and job.get("status") not in ("done", "stopped", "drafts_ready"):
            job["status"] = "drafts_ready"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "job_drafts_ready", "job_id": job_id})

    asyncio.create_task(_retry_task())
    return {"ok": True}


# ── POST /api/tickets/bulk/{job_id}/redraft ───────────────────────────────────

@router.post("/api/tickets/bulk/{job_id}/redraft")
async def bulk_redraft_ticket(job_id: str, body: BulkRedraftBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]

    if ticket["state"] in ("drafting", "pending"):
        return {"ok": True, "state": ticket["state"]}

    ticket["state"] = "pending"
    ticket["error"] = None
    ticket["started_at"] = None
    ticket["finished_at"] = None
    ticket["title"] = None
    ticket["body"] = None
    ticket["body_preview"] = None
    job["status"] = "running"
    srv._persist_bulk_job(job)
    await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _redraft_task():
        await srv._run_single_ba_ticket(
            job_id, body.index, ticket["prompt"],
            job["repo"], job["default_labels"]
        )
        t = job["tickets"][body.index]
        if t.get("state") == "draft_ready":
            body_with_attachments = srv._build_body_with_images(
                t.get("body") or "", body.index, job
            )
            if len(body_with_attachments) > srv._BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_attachments
                t["body_char_count"] = len(body_with_attachments)
                t["body_over_by"] = len(body_with_attachments) - srv._BC_BODY_SIZE_THRESHOLD
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            else:
                t["body"] = body_with_attachments
                t["body_preview"] = body_with_attachments[:200]
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

        all_drafted = all(
            tt["state"] in ("draft_ready", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        if all_drafted and job.get("status") not in ("done", "stopped", "drafts_ready"):
            job["status"] = "drafts_ready"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "job_drafts_ready", "job_id": job_id})

    asyncio.create_task(_redraft_task())
    return {"ok": True}


# ── POST /api/tickets/bulk/{job_id}/post-selected ─────────────────────────────

@router.post("/api/tickets/bulk/{job_id}/post-selected")
async def bulk_post_selected(job_id: str, body: BulkPostSelectedBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")

    for item in body.tickets:
        if item.index < 0 or item.index >= len(job["tickets"]):
            raise HTTPException(422, detail=f"Invalid ticket index: {item.index}")
        if job["tickets"][item.index]["state"] != "draft_ready":
            raise HTTPException(
                422,
                detail=f"Ticket {item.index} is not in draft_ready state "
                       f"(current: {job['tickets'][item.index]['state']})",
            )

    if not body.tickets:
        raise HTTPException(422, detail="No tickets selected")

    job["status"] = "running"
    srv._persist_bulk_job(job)

    async def _post_task():
        estimation_tasks: list[asyncio.Task] = []

        if job.get("has_attachments") and not job.get("image_url_map"):
            try:
                url_map = await asyncio.to_thread(
                    srv._do_pre_commit_bulk_images, job["job_id"], job["repo"]
                )
                job["image_url_map"] = url_map
                if url_map:
                    job.pop("attachment_error", None)
                else:
                    srv._apply_bulk_attachment_warning(job, srv._BULK_ATTACHMENT_WARN)
                srv._persist_bulk_job(job)
            except Exception as _pre_err:
                job["image_url_map"] = {}
                srv._apply_bulk_attachment_warning(
                    job,
                    f"{srv._BULK_ATTACHMENT_WARN} ({str(_pre_err)[:120]})",
                )
                srv._persist_bulk_job(job)

        chosen = body.sprint_label if body.sprint_label is not None else job.get("sprint_label")
        sprint_label = srv._resolve_bulk_sprint_label(chosen, job.get("repo") or None)
        if sprint_label != (job.get("sprint_label") or ""):
            job["sprint_label"] = sprint_label
            srv._persist_bulk_job(job)

        milestone = srv._resolve_bulk_milestone(body.milestone, job)
        if milestone != job.get("milestone"):
            job["milestone"] = milestone
            srv._persist_bulk_job(job)

        for item in body.tickets:
            idx = item.index
            labels = srv._compose_ticket_labels(sprint_label, item.labels)
            t = job["tickets"][idx]
            issue_repo = job.get("repo") or None

            if item.title is not None:
                stripped_title = item.title.strip()
                if stripped_title:
                    t["title"] = stripped_title
            if item.body is not None:
                t["body"] = item.body

            t["state"] = "drafting"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

            body_with_attachments = srv._build_body_with_images(t.get("body") or "", idx, job)

            if (
                job.get("has_attachments")
                and not job.get("image_url_map")
                and srv._ticket_has_attachment_assignment(job.get("image_assignments") or [], idx)
            ):
                t["attachment_warning"] = job.get("attachment_error") or srv._BULK_ATTACHMENT_WARN

            if len(body_with_attachments) > srv._BC_BODY_SIZE_THRESHOLD:
                t["state"] = "size_warning"
                t["body"] = body_with_attachments
                t["body_char_count"] = len(body_with_attachments)
                t["body_over_by"] = len(body_with_attachments) - srv._BC_BODY_SIZE_THRESHOLD
                t["_default_labels"] = labels
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
                srv._persist_bulk_job(job)
                await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
                continue

            if not (t.get("title") or "").strip():
                t["state"] = "failed"
                t["error"] = "Refusing to post a ticket with no title."
                t["finished_at"] = datetime.now(timezone.utc).isoformat()
                srv._persist_bulk_job(job)
                await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})
                continue

            created_issue_number: int | None = None
            pre_estimate = t.get("estimate") if t.get("estimate_state") == "sized" else None
            try:
                number, url = srv.github_client.create_issue(
                    title=t["title"], body=body_with_attachments,
                    labels=labels, repo_name=issue_repo, milestone=job.get("milestone"))
                t["state"] = "created"
                t["issue_num"] = number
                t["issue_url"] = url
                t["body"] = body_with_attachments
                t["body_preview"] = body_with_attachments[:200]
                t["label_pills"] = labels
                created_issue_number = number
            except Exception as e:
                t["state"] = "failed"
                t["error"] = f"GitHub issue creation failed: {str(e)[:200]}"

            t["finished_at"] = datetime.now(timezone.utc).isoformat()
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(t)})

            if created_issue_number is not None:
                try:
                    resolved_repo = issue_repo or srv.github_client.get_repo_for_operation(None)
                except Exception:
                    resolved_repo = None
                if resolved_repo:
                    if pre_estimate:
                        est_task = asyncio.create_task(
                            srv._materialise_bulk_estimate(job_id, idx, created_issue_number, resolved_repo, pre_estimate)
                        )
                        estimation_tasks.append(est_task)
                    elif srv._ESTIMATE_ISSUE_SCRIPT.exists():
                        est_task = asyncio.create_task(
                            srv._run_bulk_estimator_for_ticket(job_id, idx, created_issue_number, resolved_repo)
                        )
                        estimation_tasks.append(est_task)

        if estimation_tasks:
            await asyncio.gather(*estimation_tasks, return_exceptions=True)

        no_active = all(
            t["state"] not in ("pending", "drafting") for t in job["tickets"]
        )
        if no_active and job.get("status") not in ("done", "stopped"):
            job["status"] = "done"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})
            _created_ids = [
                f"#{t['issue_num']}" for t in job["tickets"]
                if t.get("state") == "created" and t.get("issue_num") is not None
            ]
            srv._emit_dashboard_event(
                project=job.get("repo") or "dashboard",
                type="bulk_created",
                target=",".join(_created_ids),
                detail={"ticket_ids": _created_ids},
                action_id=job.get("_action_id") or str(uuid.uuid4()),
            )
            if _created_ids and job.get("repo"):
                invalidate_board(job["repo"])

    asyncio.create_task(_post_task())
    return {"ok": True}


# ── POST /api/tickets/bulk/{job_id}/retry-with-body ───────────────────────────

@router.post("/api/tickets/bulk/{job_id}/retry-with-body")
async def bulk_retry_ticket_with_body(job_id: str, body: BulkRetryWithBodyBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] not in ("failed", "skipped"):
        return {"ok": True, "state": ticket["state"]}

    job["status"] = "running"
    srv._persist_bulk_job(job)

    asyncio.create_task(srv._post_ticket_body_to_github(job_id, body.index, body.body))
    return {"ok": True}


# ── POST /api/tickets/bulk/{job_id}/retry-with-image ─────────────────────────

@router.post("/api/tickets/bulk/{job_id}/retry-with-image")
async def bulk_retry_with_image(
    job_id: str,
    index: int = Form(...),
    body_text: str = Form(default=""),
    file: UploadFile = File(default=None),
):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if index < 0 or index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[index]
    if ticket["state"] not in ("failed", "skipped"):
        return {"ok": True, "state": ticket["state"]}

    final_body = body_text or ticket.get("body") or ""

    if file and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in srv._BULK_ATTACH_EXTS:
            raise HTTPException(
                422,
                detail=f"Unsupported file type '{ext}'. Accepted: .png, .jpg, .jpeg, .md, .html, .htm, .pdf.",
            )
        content = await file.read()
        if len(content) > srv._MAX_FILE_SIZE_BYTES:
            raise HTTPException(422, detail="File exceeds the 25 MB per-file limit.")

        repo = job["repo"]
        sanitized = srv._sanitize_filename(file.filename)

        def _commit_single_image() -> str:
            srv._ensure_attachments_branch(repo)
            cache_dir = srv._init_attachment_cache(repo)
            existing = srv._list_existing_assets(cache_dir)
            final_name = srv._resolve_collision(sanitized, existing)
            srv._commit_assets_to_branch(cache_dir, [(final_name, content)])
            return (
                f"https://raw.githubusercontent.com/{repo}/"
                f"{srv._ATTACHMENTS_BRANCH}/references/issue-assets/{final_name}"
            )

        try:
            img_url = await asyncio.to_thread(_commit_single_image)
            final_body = final_body + f"\n\n![{file.filename}]({img_url})"
        except Exception as e:
            raise HTTPException(500, detail=f"Image commit failed: {str(e)[:200]}")

    if len(final_body) > 65536:
        final_body = final_body[:65536]

    job["status"] = "running"
    srv._persist_bulk_job(job)

    asyncio.create_task(srv._post_ticket_body_to_github(job_id, index, final_body))
    return {"ok": True}


# ── POST /api/tickets/bulk/{job_id}/retry-all ─────────────────────────────────

@router.post("/api/tickets/bulk/{job_id}/retry-all")
async def bulk_retry_all_failed(job_id: str, body: BulkRetryAllBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]

    retried = 0
    for idx_str, body_text in body.bodies.items():
        try:
            idx = int(idx_str)
        except ValueError:
            continue
        if idx < 0 or idx >= len(tickets):
            continue
        t = tickets[idx]
        if t["state"] not in ("failed", "cancelled"):
            continue
        job["status"] = "running"
        asyncio.create_task(srv._post_ticket_body_to_github(job_id, idx, body_text))
        retried += 1

    if retried > 0:
        srv._persist_bulk_job(job)

    return {"ok": True, "retried": retried}


# ── POST /api/tickets/bulk/{job_id}/size-remedy-comment ───────────────────────

@router.post("/api/tickets/bulk/{job_id}/size-remedy-comment")
async def bulk_size_remedy_comment(job_id: str, body: SizeRemedyCommentBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] != "size_warning":
        return {"ok": True, "state": ticket["state"]}

    full_body = ticket.get("body") or ""
    title = ticket.get("title") or "Untitled ticket"
    issue_repo = ticket.get("_repo") or job.get("repo") or None
    labels = ticket.get("_default_labels") or (["backlog"] + job.get("default_labels", []))

    overflow_note = "\n\n---\n*Body exceeded size limit — continued in first comment.*"
    max_trimmed = srv._BC_BODY_SIZE_THRESHOLD - len(overflow_note)
    trimmed_body = full_body[:max_trimmed] + overflow_note
    overflow_content = full_body[max_trimmed:]

    ticket["state"] = "drafting"
    ticket["started_at"] = datetime.now(timezone.utc).isoformat()
    ticket["error"] = None
    srv._persist_bulk_job(job)
    job["status"] = "running"
    await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _remedy_task():
        try:
            number, url = await asyncio.to_thread(
                srv.github_client.create_issue,
                title=title,
                body=trimmed_body,
                labels=labels,
                repo_name=issue_repo,
            )
            comment_body = f"*Continued from issue body (overflow content):*\n\n{overflow_content}"
            await asyncio.to_thread(
                srv.github_client.add_comment,
                issue_id=number,
                body=comment_body,
                repo_name=issue_repo,
            )
            ticket["state"] = "created"
            ticket["issue_num"] = number
            ticket["issue_url"] = url
            ticket["body"] = trimmed_body
            ticket["body_preview"] = trimmed_body[:200]
            ticket["label_pills"] = labels
            ticket.pop("_default_labels", None)
            ticket.pop("_repo", None)
            ticket.pop("body_char_count", None)
            ticket.pop("body_over_by", None)
        except Exception as e:
            ticket["state"] = "failed"
            ticket["error"] = f"Size remedy failed: {str(e)[:200]}"
            ticket["last_error"] = ticket["error"]
            ticket["retry_count"] = (ticket.get("retry_count") or 0) + 1

        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        srv._persist_bulk_job(job)
        await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        has_size_warnings = any(t["state"] == "size_warning" for t in job["tickets"])
        if all_done and not has_size_warnings:
            job["status"] = "done"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    asyncio.create_task(_remedy_task())
    return {"ok": True}


# ── POST /api/tickets/bulk/{job_id}/size-remedy-images ────────────────────────

@router.post("/api/tickets/bulk/{job_id}/size-remedy-images")
async def bulk_size_remedy_images(job_id: str, body: SizeRemedyImagesBody):
    srv = _server()
    job = _get_bulk_job(job_id)
    if not job:
        raise HTTPException(404, detail="Job not found")
    tickets = job["tickets"]
    if body.index < 0 or body.index >= len(tickets):
        raise HTTPException(422, detail="Invalid ticket index")
    ticket = tickets[body.index]
    if ticket["state"] != "size_warning":
        return {"ok": True, "state": ticket["state"]}

    full_body = ticket.get("body") or ""
    title = ticket.get("title") or "Untitled ticket"
    issue_repo = ticket.get("_repo") or job.get("repo") or None
    labels = ticket.get("_default_labels") or (["backlog"] + job.get("default_labels", []))
    repo = job.get("repo") or ""

    ticket["state"] = "drafting"
    ticket["started_at"] = datetime.now(timezone.utc).isoformat()
    ticket["error"] = None
    srv._persist_bulk_job(job)
    job["status"] = "running"
    await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

    async def _image_remedy_task():
        try:
            updated_body, img_count = await asyncio.to_thread(
                srv._extract_and_replace_base64_images, full_body, repo
            )
        except Exception as e:
            ticket["state"] = "size_warning"
            ticket["body"] = full_body
            ticket["body_char_count"] = len(full_body)
            ticket["body_over_by"] = len(full_body) - srv._BC_BODY_SIZE_THRESHOLD
            ticket["_default_labels"] = labels
            ticket["_repo"] = issue_repo
            ticket["error"] = f"Image upload failed: {str(e)[:200]}"
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return

        if img_count == 0:
            ticket["state"] = "size_warning"
            ticket["body"] = full_body
            ticket["body_char_count"] = len(full_body)
            ticket["body_over_by"] = len(full_body) - srv._BC_BODY_SIZE_THRESHOLD
            ticket["_default_labels"] = labels
            ticket["_repo"] = issue_repo
            ticket["error"] = "No inlined base64 images found to convert"
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return

        if len(updated_body) > srv._BC_BODY_SIZE_THRESHOLD:
            ticket["state"] = "size_warning"
            ticket["body"] = updated_body
            ticket["body_char_count"] = len(updated_body)
            ticket["body_over_by"] = len(updated_body) - srv._BC_BODY_SIZE_THRESHOLD
            ticket["_default_labels"] = labels
            ticket["_repo"] = issue_repo
            ticket["error"] = None
            ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})
            return

        try:
            number, url = await asyncio.to_thread(
                srv.github_client.create_issue,
                title=title,
                body=updated_body,
                labels=labels,
                repo_name=issue_repo,
            )
            ticket["state"] = "created"
            ticket["issue_num"] = number
            ticket["issue_url"] = url
            ticket["body"] = updated_body
            ticket["body_preview"] = updated_body[:200]
            ticket["label_pills"] = labels
            ticket.pop("_default_labels", None)
            ticket.pop("_repo", None)
            ticket.pop("body_char_count", None)
            ticket.pop("body_over_by", None)
            ticket["error"] = None
        except Exception as e:
            ticket["state"] = "failed"
            ticket["error"] = f"GitHub issue creation failed: {str(e)[:200]}"
            ticket["last_error"] = ticket["error"]
            ticket["retry_count"] = (ticket.get("retry_count") or 0) + 1

        ticket["finished_at"] = datetime.now(timezone.utc).isoformat()
        srv._persist_bulk_job(job)
        await srv._broadcast_bulk_event(job_id, {"type": "ticket_update", "ticket": dict(ticket)})

        all_done = all(
            tt["state"] in ("created", "failed", "skipped", "size_warning")
            for tt in job["tickets"]
        )
        has_size_warnings = any(t["state"] == "size_warning" for t in job["tickets"])
        if all_done and not has_size_warnings:
            job["status"] = "done"
            srv._persist_bulk_job(job)
            await srv._broadcast_bulk_event(job_id, {"type": "job_done", "job_id": job_id})

    asyncio.create_task(_image_remedy_task())
    return {"ok": True}
