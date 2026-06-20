"""Tests for issue #1424 — Surface XL split suggestions before Run Sprint.

AC coverage:
  AC1: Run Sprint preflight returns xl_suggestions for XL or threshold-exceeding tickets
  AC2: Each suggestion includes title, size/estimate, and split-flow link in frontend
  AC3: Minutes-saved rollup shown when suggestions are present
  AC4: Default strict_xl_gate is False (advisory only — does not block Run Sprint)
  AC5: strict_xl_gate setting exists in schema and blocks confirm when enabled
  AC6: xl_minute_threshold setting exists with sensible default
  AC7: xl_suggestions is empty when no qualifying tickets
  AC8: Dismiss endpoint persists per-ticket per-sprint; dismissed tickets excluded from suggestions
"""
from __future__ import annotations

import py_compile
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"
PROJECT_HTML = STATIC_DIR / "project.html"
RUN_CONTROLS_JS = STATIC_DIR / "src" / "sprint-board" / "run-controls.js"
BUNDLE_JS = STATIC_DIR / "dist" / "bundle.js"
SCHEMA_PATH = REPO_ROOT / "services" / "sprint_manager" / "settings_schema.py"
PREFLIGHT_PATH = DASHBOARD_DIR / "routers" / "sprint_preflight.py"
XL_ROUTER_PATH = DASHBOARD_DIR / "routers" / "xl_suggestions.py"
XL_SERVICE_PATH = DASHBOARD_DIR / "routers" / "xl_suggestions_service.py"
ROUTERS_INIT = DASHBOARD_DIR / "routers" / "__init__.py"
STATE_JS = STATIC_DIR / "src" / "sprint-board" / "state.js"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


def _js() -> str:
    """JS source — run-controls.js (authoritative) with bundle.js as fallback."""
    rc = RUN_CONTROLS_JS.read_text(encoding="utf-8") if RUN_CONTROLS_JS.exists() else ""
    bundle = BUNDLE_JS.read_text(encoding="utf-8") if BUNDLE_JS.exists() else ""
    return rc + "\n" + bundle


# ── AC6: xl_minute_threshold setting ──────────────────────────────────────────

def test_ac6_xl_minute_threshold_in_schema():
    """AC6: xl_minute_threshold field exists in settings_schema.py with a sensible default."""
    src = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "xl_minute_threshold" in src, (
        "xl_minute_threshold not found in settings_schema.py — add it to KNOWN_FIELDS"
    )


def test_ac6_xl_minute_threshold_default_is_sensible():
    """AC6: xl_minute_threshold default is >= 30 (not trivially small)."""
    import settings_schema
    import importlib
    importlib.reload(settings_schema)
    default = settings_schema.KNOWN_FIELDS.get("xl_minute_threshold", {}).get("default")
    assert default is not None, "xl_minute_threshold missing from KNOWN_FIELDS"
    assert default >= 30, f"Default xl_minute_threshold {default} is too small — must be >= 30"


def test_ac6_xl_minute_threshold_is_not_secret():
    """AC6: xl_minute_threshold is a non-secret configurable field."""
    import settings_schema
    import importlib
    importlib.reload(settings_schema)
    meta = settings_schema.KNOWN_FIELDS.get("xl_minute_threshold", {})
    assert meta.get("secret") is False, "xl_minute_threshold must be non-secret"


# ── AC5: strict_xl_gate setting ───────────────────────────────────────────────

def test_ac5_strict_xl_gate_in_schema():
    """AC5: strict_xl_gate field exists in settings_schema.py."""
    src = SCHEMA_PATH.read_text(encoding="utf-8")
    assert "strict_xl_gate" in src, (
        "strict_xl_gate not found in settings_schema.py — add it to KNOWN_FIELDS"
    )


def test_ac5_strict_xl_gate_default_is_false():
    """AC5: strict_xl_gate defaults to False."""
    import settings_schema
    import importlib
    importlib.reload(settings_schema)
    default = settings_schema.KNOWN_FIELDS.get("strict_xl_gate", {}).get("default")
    assert default is False, f"strict_xl_gate default must be False, got {default!r}"


def test_ac5_strict_xl_gate_is_not_secret():
    """AC5: strict_xl_gate is a non-secret configurable field."""
    import settings_schema
    import importlib
    importlib.reload(settings_schema)
    meta = settings_schema.KNOWN_FIELDS.get("strict_xl_gate", {})
    assert meta.get("secret") is False, "strict_xl_gate must be non-secret"


# ── AC1: Preflight returns xl_suggestions ────────────────────────────────────

def test_ac1_preflight_source_includes_xl_suggestions():
    """AC1: sprint_preflight.py includes xl_suggestions in its response."""
    src = PREFLIGHT_PATH.read_text(encoding="utf-8")
    assert "xl_suggestions" in src, (
        "xl_suggestions not found in sprint_preflight.py — must be added to preflight response"
    )


def test_ac1_preflight_source_includes_strict_xl_gate():
    """AC5: sprint_preflight.py includes strict_xl_gate in its response."""
    src = PREFLIGHT_PATH.read_text(encoding="utf-8")
    assert "strict_xl_gate" in src, (
        "strict_xl_gate not found in sprint_preflight.py — must be surfaced in preflight response"
    )


def test_ac1_preflight_compiles():
    """AC1: sprint_preflight.py is syntactically valid after the change."""
    py_compile.compile(str(PREFLIGHT_PATH), doraise=True)


# ── AC8: Dismiss endpoint and service ────────────────────────────────────────

def test_ac8_xl_router_file_exists():
    """AC8: xl_suggestions.py router file exists."""
    assert XL_ROUTER_PATH.exists(), (
        "apps/dashboard/routers/xl_suggestions.py not found — dismiss endpoint must be created"
    )


def test_ac8_xl_service_file_exists():
    """AC8: xl_suggestions_service.py exists for testable service logic."""
    assert XL_SERVICE_PATH.exists(), (
        "apps/dashboard/routers/xl_suggestions_service.py not found — service layer needed"
    )


def test_ac8_xl_router_has_dismiss_endpoint():
    """AC8: xl_suggestions.py registers the dismiss endpoint."""
    src = XL_ROUTER_PATH.read_text(encoding="utf-8")
    assert "dismiss" in src, (
        "Dismiss endpoint not found in xl_suggestions.py"
    )
    assert "@router.post" in src or "@router.delete" in src, (
        "Router must use @router.post or @router.delete for the dismiss endpoint"
    )


def test_ac8_xl_router_compiles():
    """AC8: xl_suggestions.py is syntactically valid."""
    py_compile.compile(str(XL_ROUTER_PATH), doraise=True)


def test_ac8_xl_service_compiles():
    """AC8: xl_suggestions_service.py is syntactically valid."""
    py_compile.compile(str(XL_SERVICE_PATH), doraise=True)


def test_ac8_xl_router_registered_in_init():
    """AC8: xl_suggestions router is imported in routers/__init__.py."""
    src = ROUTERS_INIT.read_text(encoding="utf-8")
    assert "xl_suggestions" in src, (
        "xl_suggestions_router not imported in routers/__init__.py"
    )


def test_ac8_dismiss_persist_load_roundtrip(tmp_path):
    """AC8: Save/load dismissed suggestions round-trips correctly."""
    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    try:
        import xl_suggestions_service as svc
        import importlib
        importlib.reload(svc)
    finally:
        pass
    project_root = tmp_path
    sprint_label = "sprint-99"

    # Start empty
    dismissed = svc.load_xl_dismissed(project_root, sprint_label)
    assert dismissed == set()

    # Add a dismissal
    svc.save_xl_dismissed(project_root, sprint_label, {123, 456})
    dismissed = svc.load_xl_dismissed(project_root, sprint_label)
    assert 123 in dismissed
    assert 456 in dismissed

    # Add another
    svc.save_xl_dismissed(project_root, sprint_label, dismissed | {789})
    dismissed = svc.load_xl_dismissed(project_root, sprint_label)
    assert 789 in dismissed
    assert 123 in dismissed


def test_ac8_qualify_xl_ticket():
    """AC1/AC8: is_xl_suggestion correctly identifies XL tickets."""
    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    import xl_suggestions_service as svc
    import importlib
    importlib.reload(svc)

    # XL size label
    assert svc.is_xl_suggestion(size="XL", estimated_minutes=90, threshold=90)
    # Exceeds threshold even if not XL
    assert svc.is_xl_suggestion(size="L", estimated_minutes=95, threshold=90)
    # Below threshold and not XL
    assert not svc.is_xl_suggestion(size="L", estimated_minutes=30, threshold=90)
    # Non-XL below threshold
    assert not svc.is_xl_suggestion(size="M", estimated_minutes=15, threshold=90)


def test_ac7_no_xl_suggestions_when_no_qualifying_tickets():
    """AC7: is_xl_suggestion returns False for non-XL tickets below threshold."""
    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    import xl_suggestions_service as svc
    import importlib
    importlib.reload(svc)

    assert not svc.is_xl_suggestion(size="S", estimated_minutes=5, threshold=90)
    assert not svc.is_xl_suggestion(size="M", estimated_minutes=15, threshold=90)
    assert not svc.is_xl_suggestion(size="L", estimated_minutes=30, threshold=90)


def test_ac3_minutes_saved_calculation():
    """AC3: compute_minutes_saved estimates savings for a list of XL suggestions."""
    sys.path.insert(0, str(DASHBOARD_DIR / "routers"))
    import xl_suggestions_service as svc
    import importlib
    importlib.reload(svc)

    # Two XL tickets @ 90 min each → saved = 90//2 + 90//2 = 90
    saved = svc.compute_minutes_saved([90, 90])
    assert saved == 90

    # One XL ticket @ 90 min → saved = 45
    saved = svc.compute_minutes_saved([90])
    assert saved == 45

    # Empty list
    saved = svc.compute_minutes_saved([])
    assert saved == 0


# ── AC2: Frontend shows XL suggestions with split link ───────────────────────

def test_ac2_xl_suggestions_builder_in_run_controls():
    """AC2: run-controls.js has a _pfBuildXLSuggestionsHtml function."""
    js = _js()
    assert "_pfBuildXLSuggestionsHtml" in js, (
        "_pfBuildXLSuggestionsHtml not found in run-controls.js — "
        "must build the XL suggestions section for the preflight modal"
    )


def test_ac2_consider_splitting_label_in_js():
    """AC2: The 'Consider splitting' label text appears in the JS output."""
    js = _js()
    assert "Consider splitting" in js or "consider-splitting" in js, (
        "'Consider splitting' label not found in JS — each XL ticket must show this label"
    )


def test_ac2_split_link_in_js():
    """AC2: JS opens the split flow via link/button for flagged tickets."""
    js = _js()
    assert "smgmtSplitOpen" in js or "split-flow" in js or "_pfXLSuggestions" in js, (
        "No split-flow link mechanism found in run-controls.js — each flagged ticket must have a split link"
    )


def test_ac2_xl_suggestions_state_in_state_js():
    """AC2: _pfXLSuggestions state variable is declared in state.js."""
    src = STATE_JS.read_text(encoding="utf-8")
    assert "_pfXLSuggestions" in src, (
        "_pfXLSuggestions not declared in state.js — add it to the global preflight state"
    )


# ── AC3: Minutes saved section in JS ─────────────────────────────────────────

def test_ac3_minutes_saved_in_js():
    """AC3: JS shows minutes-saved estimate when suggestions are present."""
    js = _js()
    assert "minutes saved" in js or "minutesSaved" in js or "minutes_saved" in js, (
        "Minutes-saved estimate not found in run-controls.js — "
        "must show rolled-up savings when split suggestions are present"
    )


# ── AC4/AC5: Strict gate blocks confirm button ────────────────────────────────

def test_ac5_strict_gate_check_in_js():
    """AC5: JS checks strict_xl_gate to conditionally disable the confirm button."""
    js = _js()
    assert "strict_xl_gate" in js or "_pfStrictXLGate" in js, (
        "strict_xl_gate / _pfStrictXLGate not referenced in JS — "
        "confirm button must be disabled when strict gate is on and suggestions pending"
    )


def test_ac4_strict_gate_state_in_state_js():
    """AC5: _pfStrictXLGate state variable is declared in state.js."""
    src = STATE_JS.read_text(encoding="utf-8")
    assert "_pfStrictXLGate" in src, (
        "_pfStrictXLGate not declared in state.js — must track the strict gate setting"
    )


# ── Compilation sanity ────────────────────────────────────────────────────────

def test_schema_file_compiles():
    """Sanity: settings_schema.py still compiles after changes."""
    py_compile.compile(str(SCHEMA_PATH), doraise=True)


def test_routers_init_compiles():
    """Sanity: routers/__init__.py compiles after adding xl_suggestions router."""
    py_compile.compile(str(ROUTERS_INIT), doraise=True)
