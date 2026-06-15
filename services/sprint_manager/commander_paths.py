"""Resolve the project-level ``.commander`` directory (nested layout aware).

In nested layouts the live sprint config lives at
``~/dev/<project>/.commander/`` (outside any clone). Clones like ``uat/`` may
also have a local ``.commander/`` — prefer the directory that owns
``sprint.yaml``, otherwise the outermost ``.commander`` found when walking up
from *start* (mirrors ``backup._find_commander_dir``).
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def discover_commander_dir(start: Path | None = None) -> Path:
    """Return the best ``.commander`` directory for durable project state."""
    found: list[Path] = []
    candidate = (start or Path(__file__).resolve()).parent
    for _ in range(15):
        commander = candidate / ".commander"
        if commander.is_dir():
            found.append(commander)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    if not found:
        d = _REPO_ROOT / ".commander"
        d.mkdir(parents=True, exist_ok=True)
        return d

    for d in found:
        if (d / "sprint.yaml").exists():
            return d

    return found[-1]
