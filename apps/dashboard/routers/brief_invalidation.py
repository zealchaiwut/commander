"""Fire-and-forget home brief cache invalidation (issue #1854).

Called from sprint lifecycle handlers (sprint start, sprint finish, ticket
label transitions). Failure must never break the calling lifecycle action.
"""
from __future__ import annotations


def _today() -> str:
    """Current UTC date as YYYY-MM-DD. Seam — patched in tests for determinism."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).date().isoformat()


def invalidate_home_brief_today() -> None:
    """Delete today's home brief artifact so the next GET regenerates it.

    Best-effort: any failure is swallowed so it never breaks a lifecycle
    action. Called on sprint start, sprint finish, and ticket label
    transitions (→SIT, →UAT, →done).
    """
    try:
        import db  # noqa: PLC0415
        db.delete_brief_artifact("home", "", _today())
    except Exception:
        pass
