"""Local attachment storage for per-project to-do items.

Screenshots and images are stored on disk under
``apps/dashboard/runtime/todo-attachments/<project>/<todo_id>/`` with metadata
in ``.commander/todo_attachments.json``. No Neon migration — attachments are
local-first, matching the JSON fallback store for todos.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_lock = threading.Lock()

_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per image


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _attach_root() -> Path:
    d = _repo_root() / "apps" / "dashboard" / "runtime" / "todo-attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    d = _repo_root() / ".commander"
    d.mkdir(parents=True, exist_ok=True)
    return d / "todo_attachments.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(project: str, todo_id: int) -> str:
    return f"{project}:{todo_id}"


def _read_manifest() -> dict:
    p = _manifest_path()
    if not p.exists():
        return {"attachments": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("attachments", {})
        return data
    except Exception:
        return {"attachments": {}}


def _write_manifest(data: dict) -> None:
    _manifest_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _sanitize_filename(name: str) -> str:
    base = Path(name).name
    stem = Path(base).stem
    suffix = Path(base).suffix.lower()
    if suffix not in _ALLOWED_EXTS:
        suffix = ".png"
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", stem)[:80] or "screenshot"
    return safe + suffix


def _todo_dir(project: str, todo_id: int) -> Path:
    safe_project = re.sub(r"[^a-zA-Z0-9._-]", "_", project)
    d = _attach_root() / safe_project / str(todo_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_for_todo(project: str, todo_id: int) -> list[dict]:
    with _lock:
        data = _read_manifest()
        rows = data["attachments"].get(_key(project, todo_id), [])
    return [dict(r) for r in rows]


def list_for_project(project: str) -> dict[int, list[dict]]:
    """Map todo_id -> attachment rows for one project."""
    out: dict[int, list[dict]] = {}
    prefix = f"{project}:"
    with _lock:
        data = _read_manifest()
        for k, rows in data["attachments"].items():
            if not k.startswith(prefix):
                continue
            try:
                tid = int(k.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            out[tid] = [dict(r) for r in rows]
    return out


def resolve_file(project: str, todo_id: int, name: str) -> Optional[Path]:
    safe = _sanitize_filename(name)
    path = _todo_dir(project, todo_id) / safe
    return path if path.is_file() else None


def add_attachment(
    project: str,
    todo_id: int,
    filename: str,
    content: bytes,
    mime_type: str = "",
) -> dict:
    if len(content) > _MAX_BYTES:
        raise ValueError(f"Attachment exceeds {_MAX_BYTES // (1024 * 1024)} MB limit")
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTS:
        raise ValueError(f"Unsupported file type {ext!r} — use PNG, JPG, GIF, or WebP")

    safe_name = _sanitize_filename(filename)
    dest = _todo_dir(project, todo_id)

    with _lock:
        data = _read_manifest()
        k = _key(project, todo_id)
        rows = list(data["attachments"].get(k, []))
        existing = {r["name"] for r in rows}
        if safe_name in existing:
            stem, suffix = Path(safe_name).stem, Path(safe_name).suffix
            n = 2
            while f"{stem}-{n}{suffix}" in existing:
                n += 1
            safe_name = f"{stem}-{n}{suffix}"

        (dest / safe_name).write_bytes(content)
        row = {
            "name": safe_name,
            "mime_type": mime_type or "image/png",
            "size": len(content),
            "created_at": _now(),
        }
        rows.append(row)
        data["attachments"][k] = rows
        _write_manifest(data)
    return row


def delete_attachment(project: str, todo_id: int, name: str) -> bool:
    safe = _sanitize_filename(name)
    with _lock:
        data = _read_manifest()
        k = _key(project, todo_id)
        rows = list(data["attachments"].get(k, []))
        kept = [r for r in rows if r["name"] != safe]
        if len(kept) == len(rows):
            return False
        data["attachments"][k] = kept
        if not kept:
            data["attachments"].pop(k, None)
        _write_manifest(data)
    path = _todo_dir(project, todo_id) / safe
    if path.is_file():
        path.unlink()
    return True


def delete_all_for_todo(project: str, todo_id: int) -> None:
    with _lock:
        data = _read_manifest()
        data["attachments"].pop(_key(project, todo_id), None)
        _write_manifest(data)
    d = _todo_dir(project, todo_id)
    if d.is_dir():
        for f in d.iterdir():
            if f.is_file():
                f.unlink()
