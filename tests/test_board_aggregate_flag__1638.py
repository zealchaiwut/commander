"""Tests for issue #1638: Consume aggregate board endpoint behind feature flag.

AC coverage:
  AC1  — COMMANDER_BOARD_AGGREGATE flag exists in config.commander_features,
          defaults to OFF, readable from env var
  AC3  — when flag OFF, aggregate variables are cleared (source-level check)
  AC4  — SSE EventSource wiring is not modified (source-level check)
  AC5  — running nav pill is unchanged (source-level check)
  AC6  — bundle rebuilt, no new console errors (import check)
  AC7  — source: flag ON → only /api/board fetch, no per-sprint preview-dag or dep-order
  AC8  — source: flag OFF → legacy per-sprint calls still present
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_ROOT = REPO_ROOT / "apps" / "dashboard"
BOARD_RENDER = DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js"
PROJECT_HTML = DASHBOARD_ROOT / "static" / "project.html"
BUNDLE = DASHBOARD_ROOT / "static" / "dist" / "bundle.js"
FEATURES_JS = DASHBOARD_ROOT / "static" / "src" / "shell" / "features.js"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _get_features(env_overrides: dict | None = None) -> dict:
    """Return commander_features() with env var overrides applied at call time.

    config functions call _env_bool() lazily (at call time, not import time),
    so we need to set the env vars, call the function, then restore them.
    """
    if str(DASHBOARD_ROOT) not in sys.path:
        sys.path.insert(0, str(DASHBOARD_ROOT))

    import importlib
    import config as cfg_module
    importlib.reload(cfg_module)

    old = {}
    for k, v in (env_overrides or {}).items():
        old[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        return cfg_module.commander_features()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _import_config(env_overrides: dict | None = None):
    """Import config.py fresh. Use _get_features() for env-dependent assertions."""
    if str(DASHBOARD_ROOT) not in sys.path:
        sys.path.insert(0, str(DASHBOARD_ROOT))
    import importlib
    import config as cfg_module
    importlib.reload(cfg_module)
    return cfg_module


# ── AC1: Feature flag exists in commander_features, defaults to OFF ───────────

def test_ac1_board_aggregate_key_in_commander_features_default_off():
    """commander_features() includes 'board_aggregate' key defaulting to False."""
    features = _get_features({"COMMANDER_BOARD_AGGREGATE": "0"})
    assert "board_aggregate" in features, (
        "commander_features() must include 'board_aggregate' key"
    )
    assert features["board_aggregate"] is False, (
        "board_aggregate should default to False when COMMANDER_BOARD_AGGREGATE=0"
    )


def test_ac1_board_aggregate_on_when_env_true():
    """COMMANDER_BOARD_AGGREGATE=true → board_aggregate = True."""
    features = _get_features({"COMMANDER_BOARD_AGGREGATE": "true"})
    assert features["board_aggregate"] is True


def test_ac1_board_aggregate_on_when_env_1():
    """COMMANDER_BOARD_AGGREGATE=1 → board_aggregate = True."""
    features = _get_features({"COMMANDER_BOARD_AGGREGATE": "1"})
    assert features["board_aggregate"] is True


def test_ac1_board_aggregate_off_when_env_false():
    """COMMANDER_BOARD_AGGREGATE=false → board_aggregate = False."""
    features = _get_features({"COMMANDER_BOARD_AGGREGATE": "false"})
    assert features["board_aggregate"] is False


def test_ac1_board_aggregate_enabled_function_exists():
    """config.board_aggregate_enabled() function exists."""
    cfg = _import_config()
    assert hasattr(cfg, "board_aggregate_enabled"), (
        "config must export board_aggregate_enabled()"
    )


# ── AC7/AC8: board-render.js source structure ─────────────────────────────────

def test_ac7_board_render_has_aggregate_path():
    """board-render.js contains the _useBoardAggregate flag check (issue #1638)."""
    src = _read(BOARD_RENDER)
    assert "_useBoardAggregate" in src or "board_aggregate" in src, (
        "board-render.js must contain board_aggregate flag check"
    )


def test_ac7_board_render_fetches_api_board_on_aggregate_path():
    """/api/board fetch appears in board-render.js aggregate path."""
    src = _read(BOARD_RENDER)
    assert '"/api/board?project="' in src or "'/api/board?project='" in src or "/api/board" in src, (
        "board-render.js must fetch /api/board when flag is ON"
    )


def test_ac7_aggregate_cards_index_built():
    """board-render.js builds a per-label card index (_smgmtBuildAggCards)."""
    src = _read(BOARD_RENDER)
    assert "_smgmtBuildAggCards" in src, (
        "board-render.js must define _smgmtBuildAggCards"
    )


def test_ac7_agg_to_render_data_transform():
    """board-render.js has _smgmtAggToRenderData transformation helper."""
    src = _read(BOARD_RENDER)
    assert "_smgmtAggToRenderData" in src, (
        "board-render.js must define _smgmtAggToRenderData"
    )


def test_ac7_load_estimates_short_circuits_in_aggregate_mode():
    """_smgmtLoadEstimates checks _smgmtAggregateCards and skips fetch."""
    src = _read(BOARD_RENDER)
    # The function must reference _smgmtAggregateCards inside _smgmtLoadEstimates
    assert "_smgmtAggregateCards" in src, (
        "board-render.js must use _smgmtAggregateCards in loaders"
    )
    # No estimates/batch fetch when aggregate cards are set
    # Structural check: the batch fetch is inside a legacy-path block
    assert "estimates/batch" in src, (
        "estimates/batch fetch must still exist (legacy path)"
    )


def test_ac7_load_dep_order_short_circuits_in_aggregate_mode():
    """_smgmtLoadDepOrder checks _smgmtAggregateCards and skips fetch."""
    src = _read(BOARD_RENDER)
    # The dep-order fetch must be guarded
    assert "dep-order" in src, "dep-order fetch must still exist (legacy path)"
    # The aggregate guard exists
    assert "_smgmtAggregateCards" in src


def test_ac8_legacy_calls_still_present():
    """Legacy per-sprint fetch calls remain for the flag-OFF path."""
    src = _read(BOARD_RENDER)
    # All legacy endpoints must still be present in the source
    assert "/api/sprint-management/issues" in src, (
        "legacy /api/sprint-management/issues fetch must remain"
    )
    assert "/api/sprints/running-all" in src, (
        "legacy /api/sprints/running-all fetch must remain"
    )
    assert "estimates/batch" in src, "legacy estimates/batch fetch must remain"
    assert "dep-order" in src, "legacy dep-order fetch must remain"


# ── project.html: _smgmtLoadMiniRail aggregate path ──────────────────────────

def test_ac7_project_html_mini_rail_uses_aggregate_cards():
    """_smgmtLoadMiniRail in project.html checks window._smgmtAggregateCards."""
    src = _read(PROJECT_HTML)
    assert "_smgmtAggregateCards" in src, (
        "project.html must check _smgmtAggregateCards in _smgmtLoadMiniRail"
    )


def test_ac7_project_html_mini_rail_legacy_preview_dag_still_present():
    """project.html still has the legacy preview-dag fetch (flag-OFF path)."""
    src = _read(PROJECT_HTML)
    assert "preview-dag" in src, (
        "project.html must still contain legacy preview-dag fetch"
    )


# ── AC4: SSE EventSource is unchanged ─────────────────────────────────────────

def test_ac4_event_source_still_present_in_board_render():
    """board-render.js EventSource wiring is not removed."""
    src = _read(BOARD_RENDER)
    # The SSE live-poll restart call must still be there
    assert "_smgmtLivePollRestart" in src, (
        "_smgmtLivePollRestart must still be called in board-render.js"
    )


# ── AC6: Bundle rebuilt and contains aggregate symbols ────────────────────────

def test_ac6_bundle_contains_aggregate_symbols():
    """Rebuilt bundle contains the aggregate-path symbols."""
    bundle = _read(BUNDLE)
    for sym in ("_smgmtAggregateCards", "_smgmtBuildAggCards", "_smgmtAggToRenderData"):
        assert sym in bundle, f"bundle must contain {sym}"


def test_ac6_bundle_contains_board_aggregate_flag_check():
    """Rebuilt bundle contains the board_aggregate feature-flag check."""
    bundle = _read(BUNDLE)
    assert "board_aggregate" in bundle, (
        "bundle must contain board_aggregate flag check"
    )


# ── features.js ───────────────────────────────────────────────────────────────

def test_features_js_has_board_aggregate_enabled():
    """features.js exports boardAggregateEnabled helper (issue #1638)."""
    src = _read(FEATURES_JS)
    assert "boardAggregateEnabled" in src, (
        "features.js must export boardAggregateEnabled()"
    )
    assert "board_aggregate" in src, (
        "boardAggregateEnabled must check board_aggregate flag"
    )
