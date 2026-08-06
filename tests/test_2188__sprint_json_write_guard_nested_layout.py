"""Regression tests for issue #2188: sprint JSON write guard watches wrong dir
in nested clone layout.

The bug: _find_production_sprints_dir() in conftest.py walked up from _REPO_ROOT
looking for an ancestor directory NAMED '.commander'. In the nested layout the
coder clone lives at ~/dev/<project>/coder/ — no ancestor is literally named
'.commander', so the guard fell back to coder/.commander/sprints (wrong).

The fix: use discover_commander_dir (the same resolver the sprint_manager write
path uses), which walks up looking for directories that CONTAIN a .commander
child, then prefers the one with sprint.yaml.

AC1: _find_production_sprints_dir(start=coder_clone) returns project-level
     .commander/sprints in a simulated nested layout.
AC2: _find_production_sprints_dir() with no args is consistent with
     discover_commander_dir(_REPO_ROOT)/sprints (same resolver as write path).
AC3: The nested-layout result does NOT equal the clone-level .commander/sprints.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
_SPRINT_MGR_DIR = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_REPO_ROOT), str(_SPRINT_MGR_DIR), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from conftest import _REPO_ROOT as _CONFTEST_REPO_ROOT, _find_production_sprints_dir  # noqa: E402
from services.sprint_manager.commander_paths import discover_commander_dir  # noqa: E402


# ── AC1: nested layout — project-level .commander/sprints is returned ─────────

def test_nested_layout_coder_clone_resolves_to_project_commander(tmp_path):
    """AC1: _find_production_sprints_dir resolves to the project-level
    .commander/sprints when called with a coder clone path in nested layout.
    """
    project_dir = tmp_path / "project"
    commander_dir = project_dir / ".commander"
    commander_dir.mkdir(parents=True)
    (commander_dir / "sprint.yaml").write_text("label: sprint-1\n")
    (commander_dir / "sprints").mkdir()
    coder_clone = project_dir / "coder"
    coder_clone.mkdir()

    result = _find_production_sprints_dir(start=coder_clone)

    assert result == commander_dir / "sprints", (
        f"Expected {commander_dir / 'sprints'!r}, got {result!r}. "
        "In nested layout the guard must watch the project-level .commander/sprints."
    )


# ── AC2: no-args call is consistent with discover_commander_dir ───────────────

def test_no_args_call_consistent_with_discover_commander_dir():
    """AC2: _find_production_sprints_dir() == discover_commander_dir(_REPO_ROOT)/sprints.

    Ensures the guard watches the exact same directory the sprint_manager write
    path targets — both use the same discovery function.
    """
    expected = discover_commander_dir(_CONFTEST_REPO_ROOT) / "sprints"
    actual = _find_production_sprints_dir()
    assert actual == expected, (
        f"Guard watches {actual!r} but write path would resolve to {expected!r}. "
        "The two must agree or the guard cannot catch real leaks."
    )


# ── AC3: nested layout does NOT return clone-level .commander/sprints ─────────

def test_nested_layout_does_not_return_clone_level_commander(tmp_path):
    """AC3: When the project-level .commander/sprint.yaml exists, the guard must
    NOT fall back to the clone-local .commander/sprints.
    """
    project_dir = tmp_path / "project"
    commander_dir = project_dir / ".commander"
    commander_dir.mkdir(parents=True)
    (commander_dir / "sprint.yaml").write_text("label: sprint-1\n")

    coder_clone = project_dir / "coder"
    coder_clone.mkdir()
    clone_level_commander = coder_clone / ".commander"
    clone_level_commander.mkdir()  # local .commander without sprint.yaml

    result = _find_production_sprints_dir(start=coder_clone)

    assert result == commander_dir / "sprints"
    assert result != clone_level_commander / "sprints", (
        "Guard must not watch the clone-local .commander/sprints — "
        "that is not where sprint_manager writes production sprint JSON."
    )
