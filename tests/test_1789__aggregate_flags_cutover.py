"""Tests for issue #1789: Cut over aggregate flags and delete legacy fan-out paths.

AC coverage:
  AC1  — COMMANDER_BOARD_AGGREGATE, COMMANDER_HISTORY_AGGREGATE, COMMANDER_RUNNING_AGGREGATE
          no longer exist in config.py; flag functions removed; commander_features() omits
          the three keys.
  AC2  — board-render.js always uses the /api/board aggregate path; legacy
          /api/sprint-management/issues and /api/sprints/running-all calls removed.
  AC3  — history.js always seeds run_stats from inline data; flag guard removed from
          _histSeedRunStatsFromInline.
  AC4  — project.html always calls _smgmtRunningFirstPaint (no flag branch on
          running_aggregate).
  AC5  — All three flag constant names are grep-clean (zero hits) across the codebase.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")

BOARD_RENDER = (DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js").read_text(encoding="utf-8")
HISTORY_JS = (DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "history.js").read_text(encoding="utf-8")
FEATURES_JS = (DASHBOARD_ROOT / "static" / "src" / "shell" / "features.js").read_text(encoding="utf-8")
PROJECT_HTML = (DASHBOARD_ROOT / "static" / "project.html").read_text(encoding="utf-8")
CONFIG_PY = (DASHBOARD_ROOT / "config.py").read_text(encoding="utf-8")


# ── AC1: Flag functions and config keys removed ──────────────────────────────

def test_board_aggregate_not_in_commander_features():
    """commander_features() must not expose board_aggregate key after flag removal."""
    import config
    importlib.reload(config)
    features = config.commander_features()
    assert "board_aggregate" not in features, (
        "board_aggregate key must be removed from commander_features() in #1789"
    )


def test_history_aggregate_not_in_commander_features():
    """commander_features() must not expose history_aggregate key after flag removal."""
    import config
    importlib.reload(config)
    features = config.commander_features()
    assert "history_aggregate" not in features, (
        "history_aggregate key must be removed from commander_features() in #1789"
    )


def test_running_aggregate_not_in_commander_features():
    """commander_features() must not expose running_aggregate key after flag removal."""
    import config
    importlib.reload(config)
    features = config.commander_features()
    assert "running_aggregate" not in features, (
        "running_aggregate key must be removed from commander_features() in #1789"
    )


def test_flag_functions_removed_from_config():
    """config.py must not define the removed flag accessor functions."""
    import config
    importlib.reload(config)
    assert not hasattr(config, "board_aggregate_enabled"), (
        "board_aggregate_enabled() must be removed from config.py in #1789"
    )
    assert not hasattr(config, "history_aggregate_enabled"), (
        "history_aggregate_enabled() must be removed from config.py in #1789"
    )
    assert not hasattr(config, "running_aggregate_enabled"), (
        "running_aggregate_enabled() must be removed from config.py in #1789"
    )


# ── AC5: Flag constant names grep-clean ─────────────────────────────────────

def test_commander_board_aggregate_grep_clean():
    """COMMANDER_BOARD_AGGREGATE must not appear anywhere in the source after cutover."""
    assert "COMMANDER_BOARD_AGGREGATE" not in CONFIG_PY
    assert "COMMANDER_BOARD_AGGREGATE" not in BOARD_RENDER
    assert "COMMANDER_BOARD_AGGREGATE" not in FEATURES_JS
    assert "COMMANDER_BOARD_AGGREGATE" not in PROJECT_HTML


def test_commander_history_aggregate_grep_clean():
    """COMMANDER_HISTORY_AGGREGATE must not appear anywhere in the source after cutover."""
    assert "COMMANDER_HISTORY_AGGREGATE" not in CONFIG_PY
    assert "COMMANDER_HISTORY_AGGREGATE" not in HISTORY_JS
    assert "COMMANDER_HISTORY_AGGREGATE" not in FEATURES_JS
    assert "COMMANDER_HISTORY_AGGREGATE" not in PROJECT_HTML


def test_commander_running_aggregate_grep_clean():
    """COMMANDER_RUNNING_AGGREGATE must not appear anywhere in the source after cutover."""
    assert "COMMANDER_RUNNING_AGGREGATE" not in CONFIG_PY
    assert "COMMANDER_RUNNING_AGGREGATE" not in PROJECT_HTML
    assert "COMMANDER_RUNNING_AGGREGATE" not in FEATURES_JS


def test_board_aggregate_feature_key_grep_clean():
    """'board_aggregate' must not appear in features.js or board-render.js flag checks."""
    assert "board_aggregate" not in FEATURES_JS
    assert "board_aggregate" not in BOARD_RENDER


def test_history_aggregate_feature_key_grep_clean():
    """'history_aggregate' must not appear in features.js or history.js after flag removal."""
    assert "history_aggregate" not in FEATURES_JS
    assert "history_aggregate" not in HISTORY_JS


def test_running_aggregate_feature_key_grep_clean():
    """'running_aggregate' must not appear in features.js or project.html after flag removal."""
    assert "running_aggregate" not in FEATURES_JS
    assert "running_aggregate" not in PROJECT_HTML


# ── AC2: Board always uses aggregate path ────────────────────────────────────

def test_board_render_no_legacy_sprint_management_fetch():
    """/api/sprint-management/issues must not appear in board-render.js after legacy removal."""
    assert "/api/sprint-management/issues" not in BOARD_RENDER, (
        "Legacy /api/sprint-management/issues call still present in board-render.js"
    )


def test_board_render_no_running_all_fetch():
    """/api/sprints/running-all must not appear in board-render.js after legacy removal."""
    assert "/api/sprints/running-all" not in BOARD_RENDER, (
        "Legacy /api/sprints/running-all call still present in board-render.js"
    )


def test_board_render_api_board_fetch_present():
    """board-render.js must still fetch /api/board (the aggregate endpoint)."""
    assert "/api/board?project=" in BOARD_RENDER, (
        "/api/board?project= not found in board-render.js — aggregate fetch must be present"
    )


# ── AC3: History always seeds run_stats from inline ──────────────────────────

def test_history_seed_no_flag_guard():
    """_histSeedRunStatsFromInline must not guard on history_aggregate feature flag."""
    assert "history_aggregate" not in HISTORY_JS, (
        "_histSeedRunStatsFromInline still checks history_aggregate — flag guard must be removed"
    )


# ── AC4: Running view always calls first-paint ───────────────────────────────

def test_project_html_no_running_aggregate_branch():
    """project.html must not branch on running_aggregate flag in _smgmtLivePollRestart."""
    assert "running_aggregate" not in PROJECT_HTML, (
        "running_aggregate flag branch still present in project.html — must be removed in #1789"
    )


def test_project_html_first_paint_function_present():
    """_smgmtRunningFirstPaint() must still exist in project.html after the flag branch is removed."""
    assert "async function _smgmtRunningFirstPaint()" in PROJECT_HTML, (
        "_smgmtRunningFirstPaint() function must remain in project.html"
    )
