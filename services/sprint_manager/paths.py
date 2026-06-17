"""Pure path/constant helpers for sprint_manager.

Extracted from sprint_manager.py (issue #1270) — pure move, no logic changes.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

# This file lives at services/sprint_manager/paths.py
# Repo root is three levels up: paths.py → sprint_manager/ → services/ → root
_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_SPRINTS_DIR = _DASHBOARD_DIR / "sprints"


def _sprint_number(label: str) -> Optional[int]:
    m = re.search(r"(\d+)", label)
    return int(m.group(1)) if m else None


def _label_base(label: str) -> str:
    """Base sprint label: sprint-68.6 → sprint-68 (lineage display only)."""
    m = re.match(r"^(sprint-\d+)", label)
    return m.group(1) if m else label


def _is_child_sprint_label(label: str) -> bool:
    return bool(re.match(r"^sprint-\d+\.\d+", label))


def _sprint_branch_for_label(label: str) -> str:
    return f"sprint/{label}"


def _base_sprint_branch(label: str) -> str:
    """Base sprint branch for lineage (sprint/sprint-68 for any 68.x label)."""
    return _sprint_branch_for_label(_label_base(label))


def _pid_file_path(sprint_label: str, cfg: "Optional[SprintConfig]" = None) -> Path:
    """Return the per-(project, sprint_label) PID file path."""
    if cfg is not None:
        sprints_dir = cfg.sprints_dir
    else:
        sprints_dir = _REPO_ROOT / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    return sprints_dir / f"{sprint_label}-pid"


def _plan_json_path(label: str, cfg: "Optional[SprintConfig]" = None) -> Path:
    """Return path to {label}-plan.json in the sprints directory."""
    sprints_dir = cfg.sprints_dir if cfg is not None else _REPO_ROOT / ".commander" / "sprints"
    return sprints_dir / f"{label}-plan.json"


def _state_path(
    sprint_number: Optional[int],
    sprint_label: str,
    cfg: "Optional[SprintConfig]" = None,
) -> Path:
    """Per-label state file: ``sprint-68.6-state.json`` (lifecycle P3)."""
    sprints_dir = cfg.sprints_dir if cfg is not None else _SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    return sprints_dir / f"{sprint_label}-state.json"


def _summary_path(
    sprint_number: Optional[int],
    sprint_label: str,
    cfg: "Optional[SprintConfig]" = None,
) -> Path:
    sprints_dir = cfg.sprints_dir if cfg is not None else _SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    return sprints_dir / f"{sprint_label}-summary-{day}.md"
