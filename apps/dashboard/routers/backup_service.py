"""Service logic for the backup router (extracted from server.py, issue #761).

The gist-based config backup lives in ``services/sprint_manager/backup.py``.
This sibling module owns the availability check and the HTTP-layer error
contract so the router itself stays a thin mount.
"""
import sys as _sys
from pathlib import Path

from fastapi import HTTPException

# backup module lives in services/sprint_manager/ — add it to sys.path
_SERVICES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "services" / "sprint_manager"
if str(_SERVICES_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SERVICES_DIR))

try:
    import backup as _backup_module
    BACKUP_AVAILABLE = True
except ImportError:
    _backup_module = None  # type: ignore[assignment]
    BACKUP_AVAILABLE = False


def get_backup_status() -> dict:
    """Return the current state of the gist-based config backup.

    Response shape:
    {
      "last_backup_at": "<ISO-8601>" | null,
      "gist_id": "<id>" | null,
      "gist_url": "https://gist.github.com/..." | null,
      "file_count": <int>,
      "last_error": "<message>" | null
    }
    """
    if not BACKUP_AVAILABLE or _backup_module is None:
        raise HTTPException(status_code=503, detail="Backup module not available")
    return _backup_module.get_backup_status()
