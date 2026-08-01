"""log_source.py — thin abstraction for reading on-disk sprint/ticket log files.

All endpoint log reads go through ``read_log``; no endpoint reads files directly.
This keeps the log-reading logic swappable for database queries in phase 2
(post-Neon migration).

Public API
----------
read_log(kind, project_root, label=None, issue_num=None, tail_lines=200)
    → {"found": bool, "path": str, "tail": str, "mtime": str}
      | {"found": False, "searched_dir": str, "tail": ""}          (kind="dispatch")
      | {"found": False, "candidate_paths": [str, ...], "tail": ""}(kind="issue")
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _safe_tail(path: Path, tail_lines: int) -> str:
    """Read the last ``tail_lines`` lines from *path* and return them joined."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return "\n".join(lines[-tail_lines:])


def _mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _assert_within(path: Path, allowed_dir: Path) -> None:
    """Raise ValueError if *path* escapes *allowed_dir* (path-traversal guard)."""
    try:
        path.resolve().relative_to(allowed_dir.resolve())
    except ValueError:
        raise ValueError(
            f"Path {path!r} escapes the allowed directory {allowed_dir!r}"
        )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def read_log(
    kind: str,
    project_root: Path,
    *,
    label: str | None = None,
    issue_num: int | None = None,
    tail_lines: int = 200,
) -> dict:
    """Resolve the correct log file and return its tail.

    Parameters
    ----------
    kind:
        ``"dispatch"`` — most-recent ``sprint-run-{label}-*.log``
        ``"issue"``    — ``sprint-issue-{issue_num}.log``
    project_root:
        Absolute path to the project root (one level above ``.commander/``).
    label:
        Sprint label, e.g. ``"sprint-21"``.  Required for both kinds.
    issue_num:
        Issue number.  Required for ``kind="issue"``.
    tail_lines:
        Number of lines to return.  Clamped to [1, 2000].

    Returns
    -------
    dict with at minimum ``found`` (bool) and ``tail`` (str).
    When found:  ``path`` (str) and ``mtime`` (str, ISO-8601).
    When not found (dispatch): ``searched_dir`` (str).
    When not found (issue):    ``candidate_paths`` (list[str]).
    """
    tail_lines = max(1, min(tail_lines, 2000))

    commander_logs = project_root / ".commander" / "logs"

    if kind == "dispatch":
        return _read_dispatch_log(
            project_root=project_root,
            label=label,
            tail_lines=tail_lines,
            log_dir=commander_logs,
        )
    elif kind == "issue":
        return _read_issue_log(
            project_root=project_root,
            label=label,
            issue_num=issue_num,
            tail_lines=tail_lines,
            default_log_dir=commander_logs,
        )
    else:
        raise ValueError(f"Unknown log kind: {kind!r}")


# ---------------------------------------------------------------------------
# Kind-specific helpers
# ---------------------------------------------------------------------------

def _read_dispatch_log(
    *,
    project_root: Path,
    label: str | None,
    tail_lines: int,
    log_dir: Path,
) -> dict:
    """Return the tail of the most-recent sprint-run-{label}-*.log."""
    not_found: dict = {"found": False, "searched_dir": str(log_dir), "tail": ""}

    if not log_dir.exists():
        return not_found

    pattern = f"sprint-run-{label}-*.log"
    candidates = sorted(
        log_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return not_found

    log_path = candidates[0]

    # Path-traversal guard
    try:
        _assert_within(log_path, log_dir)
    except ValueError:
        return not_found

    try:
        tail = _safe_tail(log_path, tail_lines)
    except OSError as exc:
        return {**not_found, "error": str(exc)}

    return {
        "found": True,
        "path": str(log_path),
        "tail": tail,
        "mtime": _mtime_iso(log_path),
    }


def _read_issue_log(
    *,
    project_root: Path,
    label: str | None,
    issue_num: int | None,
    tail_lines: int,
    default_log_dir: Path,
) -> dict:
    """Return the tail of sprint-issue-{N}.log.

    Tries the logs_dir from sprint.yaml first, then falls back to
    ``<project_root>/.commander/logs/``.
    """
    candidates_checked: list[str] = []

    # Attempt to read logs_dir from sprint.yaml
    cfg_logs_dir: Path | None = None
    try:
        import yaml as _yaml  # type: ignore[import]
        yaml_path = project_root / ".commander" / "sprint.yaml"
        if yaml_path.exists():
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            raw = (data.get("paths") or {}).get("logs_dir", "").strip()
            if raw:
                p = Path(raw)
                cfg_logs_dir = p if p.is_absolute() else yaml_path.parent / p
    except Exception:
        pass

    log_path: Path | None = None
    filename = f"sprint-issue-{issue_num}.log"

    for candidate_dir in filter(None, [cfg_logs_dir, default_log_dir]):
        p = candidate_dir / filename
        candidates_checked.append(str(p))

        # Path-traversal guard — file must stay within its candidate dir
        try:
            _assert_within(p, candidate_dir)
        except ValueError:
            continue

        if p.exists():
            log_path = p
            break

    not_found: dict = {
        "found": False,
        "candidate_paths": candidates_checked,
        "tail": "",
    }

    if log_path is None:
        return not_found

    try:
        tail = _safe_tail(log_path, tail_lines)
    except OSError as exc:
        return {**not_found, "error": str(exc)}

    return {
        "found": True,
        "path": str(log_path),
        "tail": tail,
        "mtime": _mtime_iso(log_path),
    }
