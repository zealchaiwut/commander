"""Global notes store (issue #717).

Notes are a single, project-agnostic markdown/text body persisted to
``.commander/notes.json``. This mirrors the local JSON-fallback half of
``settings_repo`` (a single ``.commander/*.json`` file guarded by a lock,
degrading gracefully on read errors) so notes survive reloads and server
restarts and work fully when Neon is disabled — notes never touch Neon.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

# Tests can redirect the store to a temp file by setting this.
_store_path_override = None

_lock = threading.Lock()


def _notes_store_path() -> Path:
    # notes_repo.py lives at <repo>/services/sprint_manager/notes_repo.py
    if _store_path_override is not None:
        return Path(_store_path_override)
    root = Path(__file__).resolve().parent.parent.parent
    return root / ".commander" / "notes.json"


def get_notes() -> str:
    """Return the stored notes body, or an empty string if none exists."""
    p = _notes_store_path()
    if not p.exists():
        return ""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return ""
    body = data.get("body", "") if isinstance(data, dict) else ""
    return body if isinstance(body, str) else ""


def set_notes(body: str) -> None:
    """Persist the full notes body, creating ``.commander/notes.json`` if absent."""
    p = _notes_store_path()
    with _lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"body": body}, indent=2), encoding="utf-8")
