"""Tests for issue #801 — Sprint capacity budget bar on the sprint pane.

AC coverage:
  AC1 — New `sprint_budget_minutes` setting exists globally (default 180) and is
        overridable per-project via the settings KV API (#639).
  AC2 — Each sprint pane renders a capacity bar (`.cap` / `.cap-bar` / `.cap-seg`
        / `.cap-hint`) when at least one estimated ticket exists.
  AC3 — Bar segments are proportional to per-ticket calibrated minutes, coloured
        with the existing size palette CSS vars, and sum to the correct total.
  AC4 — Headroom text shows remaining minutes under budget; turns red and reads
        "over budget by ~Xh" when total exceeds budget.
  AC5 — Token/$ forecast line renders when per-size averages are available from
        the analytics endpoint; hidden otherwise; never crashes. The cost endpoint
        exposes a `usd_per_token` rate so the $ figure can be derived.
  AC6 — Unestimated tickets are excluded from the bar with a visible note; their
        absence never throws.
  AC7 — Editing `sprint_budget_minutes` in Project Settings updates all visible
        bars without a page reload.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


# ───────────────────────────── Settings fixture ─────────────────────────────

def _make_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                project TEXT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(scope, project, key)
            )
        """))
        conn.commit()
    return engine


@pytest.fixture()
def client_ctx():
    """Yield (client, settings_repo) with an in-memory settings DB and projects patched."""
    engine = _make_engine()
    for mod in ("server", "services.sprint_manager.settings_repo", "services.sprint_manager.settings_schema"):
        sys.modules.pop(mod, None)

    import server as srv
    import services.sprint_manager.settings_repo as settings_repo

    SessionLocal = sessionmaker(bind=engine)
    settings_repo._session_factory = SessionLocal

    from fastapi.testclient import TestClient
    with patch.object(srv.projects_module, "load_projects", return_value=[{"repo": "owner/test-proj"}]):
        with patch.object(srv, "_settings_repo", settings_repo):
            client = TestClient(srv.app, raise_server_exceptions=False)
            yield client, settings_repo


# ─────────────────────── AC1: sprint_budget_minutes setting ──────────────────

def test_ac1_schema_defines_sprint_budget_minutes_default_180():
    """The settings schema must define sprint_budget_minutes with a default of 180."""
    import services.sprint_manager.settings_schema as schema
    assert "sprint_budget_minutes" in schema.KNOWN_FIELDS
    meta = schema.KNOWN_FIELDS["sprint_budget_minutes"]
    assert meta["default"] == 180
    assert meta["secret"] is False


def test_ac1_global_get_returns_default_180(client_ctx):
    """GET /api/settings must surface the global sprint_budget_minutes default."""
    client, _ = client_ctx
    data = client.get("/api/settings").json()
    assert data["sprint_budget_minutes"] == 180


def test_ac1_global_put_persists_override(client_ctx):
    """PUT /api/settings persists a global sprint_budget_minutes override."""
    client, _ = client_ctx
    resp = client.put("/api/settings", json={"sprint_budget_minutes": 240})
    assert resp.status_code == 200, resp.text
    assert client.get("/api/settings").json()["sprint_budget_minutes"] == 240


def test_ac1_project_override_wins_over_global(client_ctx):
    """A per-project override of sprint_budget_minutes wins over the global value."""
    client, _ = client_ctx
    client.put("/api/settings", json={"sprint_budget_minutes": 240})
    resp = client.put(
        "/api/projects/test-proj/settings", json={"sprint_budget_minutes": 60}
    )
    assert resp.status_code == 200, resp.text
    proj = client.get("/api/projects/test-proj/settings").json()
    assert proj["sprint_budget_minutes"] == 60
    # Global remains untouched.
    assert client.get("/api/settings").json()["sprint_budget_minutes"] == 240


# ───────────────── AC2: capacity bar markup hooks present ────────────────────

def test_ac2_capacity_bar_classes_present():
    """The four AC-mandated capacity-bar class hooks must exist in project.html."""
    for cls in ("cap-bar", "cap-seg", "cap-hint"):
        assert cls in PROJECT_HTML, f"missing class hook: {cls}"
    # The container is rendered per sprint with a stable id prefix.
    assert 'class="cap"' in PROJECT_HTML
    assert "smgmt-cap-" in PROJECT_HTML


def test_ac2_update_function_exists_and_is_invoked_on_render():
    """A per-sprint update function must exist and be wired into the board render."""
    assert "function _smgmtUpdateCapBar(" in PROJECT_HTML
    # Bars are (re)rendered for all visible panes from a single entrypoint.
    assert "_smgmtRenderAllCapBars" in PROJECT_HTML


def test_ac2_bar_only_when_estimated_ticket_exists():
    """With no estimated ticket the container is cleared (no bar drawn)."""
    body = _fn_body("_smgmtUpdateCapBar")
    # Guard clears innerHTML and returns when there are zero estimated tickets.
    assert "estimated" in body.lower()
    assert "innerHTML = ''" in body or 'innerHTML = ""' in body


# ──────────────── AC3: proportional, size-coloured segments ──────────────────

def test_ac3_segments_use_size_palette_vars():
    """Segments must be coloured from the existing size palette CSS vars."""
    body = _fn_body("_smgmtUpdateCapBar")
    for var in ("var(--green)", "var(--blue)", "var(--amber)", "var(--red)"):
        assert var in body, f"segment colouring missing {var}"
    assert "cap-seg" in body


def test_ac3_uses_calibrated_minutes_not_hardcoded():
    """Segment minutes come from the calibrated (settings-driven) size mapping."""
    assert "function _smgmtCalibratedMinutes(" in PROJECT_HTML
    body = _fn_body("_smgmtUpdateCapBar")
    assert "_smgmtCalibratedMinutes" in body


# ──────────────────── AC4: headroom / over-budget text ───────────────────────

def test_ac4_headroom_and_over_budget_text():
    """Hint shows headroom under budget and the over-budget phrasing when over."""
    body = _fn_body("_smgmtUpdateCapBar")
    assert "over budget by ~" in body
    assert "headroom" in body.lower()
    # An over-budget modifier class drives the red colour.
    assert "cap-hint--over" in PROJECT_HTML


def test_ac4_over_budget_modifier_is_red():
    """The over-budget hint modifier must resolve to the red palette var."""
    # CSS rule: .cap-hint--over { color: var(--red) ... }
    assert ".cap-hint--over" in PROJECT_HTML
    idx = PROJECT_HTML.index(".cap-hint--over")
    rule = PROJECT_HTML[idx:idx + 120]
    assert "var(--red)" in rule


# ──────────────────── AC5: token/$ forecast line ─────────────────────────────

def test_ac5_cost_endpoint_exposes_usd_per_token():
    """The cost analytics endpoint must expose a usd_per_token rate field."""
    src = (DASHBOARD_DIR / "routers" / "analytics.py").read_text(encoding="utf-8")
    assert '"usd_per_token"' in src


def test_ac5_usd_per_token_none_without_price_map(tmp_path):
    """Without a price map, usd_per_token is None (no $ forecast possible)."""
    data = _call_cost(tmp_path, price_map=None).json()
    assert "usd_per_token" in data
    assert data["usd_per_token"] is None


def test_ac5_usd_per_token_numeric_with_price_map(tmp_path):
    """With a price map configured, usd_per_token is a number."""
    db = _mk_runs_db(tmp_path)
    _insert_run(db, 1, "sprint-1", "coder", 1000, model="claude-sonnet-4-6")
    _insert_token_usage(db, "claude-sonnet-4-6", 800, 200)
    pm = {"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}}
    data = _call_cost(tmp_path, db_path=db, price_map=pm).json()
    assert isinstance(data["usd_per_token"], (int, float))


def test_ac5_forecast_line_guarded_and_optional():
    """The forecast consumes per-size averages and is guarded so it never crashes."""
    body = _fn_body("_smgmtUpdateCapBar")
    assert "_smgmtCostCache" in body
    assert "usd_per_token" in PROJECT_HTML
    # The forecast reads cached cost data only behind a truthiness guard.
    assert "_smgmtCostCache &&" in body or "if (_smgmtCostCache" in body


# ───────────────── AC6: unestimated tickets excluded + noted ─────────────────

def test_ac6_unestimated_excluded_with_note():
    """Unestimated tickets are excluded and a visible note states how many."""
    body = _fn_body("_smgmtUpdateCapBar")
    assert "unestimated" in body.lower()
    assert "excluded" in body.lower()


# ─────────────── AC7: settings edit refreshes bars in-place ──────────────────

def test_ac7_project_settings_has_budget_input():
    """Project Settings exposes a sprint_budget_minutes input control."""
    assert 'id="ps-sprint-budget"' in PROJECT_HTML


def test_ac7_save_sends_budget_and_refreshes_bars():
    """projSettingsSave persists the budget and refreshes bars without reload."""
    save_body = _fn_body("projSettingsSave")
    assert "sprint_budget_minutes" in save_body
    # A reload-free refresh re-fetches cap data and re-renders visible bars.
    assert "_smgmtEnsureCapData" in save_body


def test_ac7_load_populates_budget_input():
    """projSettingsLoad populates the budget input from the API response."""
    load_body = _fn_body("projSettingsLoad")
    assert "ps-sprint-budget" in load_body
    assert "sprint_budget_minutes" in load_body


# ──────────────────────────── helpers ────────────────────────────────────────

def _fn_body(name: str) -> str:
    """Return the brace-balanced body of a JS function defined in project.html.

    Supports both `function name(` and `name = function(`/`async function name(`.
    """
    for needle in (f"function {name}(", f"{name} = function", f"{name}(slug"):
        pos = PROJECT_HTML.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found in project.html"
    brace = PROJECT_HTML.find("{", pos)
    assert brace != -1, f"no opening brace for {name}"
    depth = 0
    for i in range(brace, len(PROJECT_HTML)):
        c = PROJECT_HTML[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return PROJECT_HTML[brace:i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _mk_runs_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number INTEGER NOT NULL,
            sprint_label TEXT NOT NULL,
            agent TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds INTEGER,
            outcome TEXT,
            total_tokens INTEGER,
            model_used TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            agent_role TEXT,
            model_name TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_run(db_path, issue, sprint, agent, tokens, model="claude-sonnet-4-6",
                started_at="2026-06-01T10:00:00"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, "
        "total_tokens, model_used) VALUES (?, ?, ?, ?, ?, ?)",
        (issue, sprint, agent, started_at, tokens, model),
    )
    conn.commit()
    conn.close()


def _insert_token_usage(db_path, model, inp, out, recorded_at="2026-06-01T10:00:00",
                        role="coder"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO token_usage (session_id, project, input_tokens, output_tokens, "
        "recorded_at, agent_role, model_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess1", "owner/repo", inp, out, recorded_at, role, model),
    )
    conn.commit()
    conn.close()


def _call_cost(project_root, db_path=None, slug="myrepo", project="owner/myrepo",
               price_map=None):
    import db as _db
    import server as srv
    from starlette.testclient import TestClient
    import routers.analytics as analytics_mod

    if db_path is None:
        db_path = _mk_runs_db(project_root)

    settings_val: dict = {}
    if price_map is not None:
        settings_val["price_map"] = price_map

    with (
        patch.object(_db, "DB_PATH", str(db_path)),
        patch.object(analytics_mod, "_resolve_project_slug", return_value=project),
        patch.object(analytics_mod, "_project_root_path", return_value=project_root),
        patch.object(analytics_mod, "_settings_repo") as mock_sr,
    ):
        mock_sr.get_setting.return_value = settings_val
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/cost")
