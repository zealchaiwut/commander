"""Tests for issue #884 — advisor suggestions section in morning brief.

Each test is anchored to a specific acceptance criterion.  The DB is a real
(temp) SQLite file; advisor tables are created manually so the tests run even
before issues #881/#883 are merged into the sprint branch.

AC1  build_project_brief() payload includes a 'suggested_next' key.
AC2  Section is capped at 5 lines total (suggestions + look-ahead combined).
AC3  First look-ahead entry, when present, is the final item with type 'look_ahead'.
AC4  Each item in 'suggested_next' carries a 'slug' field used to build the
     Roadmap-tab href (/project/<slug>/roadmap).
AC5  'suggested_next' is an empty list when advisor has no suggestions and no
     look-ahead entry (section omitted entirely in the UI).
AC6  home.html contains a renderSuggestedNext function with a visible section
     title (distinguishable from the rest of the brief).
AC7  Each item in 'suggested_next' has a 'slug' field; the frontend computes a
     link to /project/<slug>/roadmap so clicking navigates to the Roadmap tab.
"""
from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"
HOME_HTML = DASHBOARD_DIR / "static" / "home.html"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import os
os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

REPO = "owner/proj"
SLUG = "proj"
DATE = "2026-06-14"
WIN_START = f"{DATE}T00:00:00"
WIN_END = f"{DATE}T23:59:59"


def _load_module(name: str, path: Path):
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


import db as db_module  # noqa: E402

svc = _load_module("routers.brief_service", ROUTERS_DIR / "brief_service.py")


# ── DB fixture ────────────────────────────────────────────────────────────────

@pytest.fixture()
def fresh_db(tmp_path):
    db_file = tmp_path / "test_884.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    _ensure_advisor_tables(db_module)
    yield db_module
    db_module.DB_PATH = original


def _ensure_advisor_tables(db) -> None:
    """Create advisor tables if not present (before #881/#883 merge)."""
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisor_suggestions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                project   TEXT NOT NULL,
                run_at    TEXT NOT NULL,
                on_demand INTEGER NOT NULL DEFAULT 0,
                pitch     TEXT NOT NULL,
                rationale TEXT NOT NULL DEFAULT '',
                milestone TEXT NOT NULL DEFAULT '',
                scope     TEXT NOT NULL DEFAULT 'M'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS advisor_look_ahead (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                project   TEXT NOT NULL,
                run_at    TEXT NOT NULL,
                position  INTEGER NOT NULL,
                entry     TEXT NOT NULL,
                on_demand INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()


@pytest.fixture(autouse=True)
def one_project(monkeypatch):
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": REPO, "name": "Proj"}],
        raising=True,
    )
    monkeypatch.setattr(
        svc, "_sprint_goal",
        lambda project_key, slug, label: "",
        raising=True,
    )


# ── seeding helpers ───────────────────────────────────────────────────────────

def _seed_suggestions(db, pitches: list[str], project: str = REPO) -> None:
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO advisor_suggestions "
            "(project, run_at, pitch, rationale, milestone, scope) "
            "VALUES (?, '2026-06-14T06:00:00', ?, '', '', 'M')",
            [(project, p) for p in pitches],
        )
        conn.commit()


def _seed_look_ahead(db, entries: list[str], project: str = REPO) -> None:
    with db.get_conn() as conn:
        conn.executemany(
            "INSERT INTO advisor_look_ahead "
            "(project, run_at, position, entry) "
            "VALUES (?, '2026-06-14T06:00:00', ?, ?)",
            [(project, i + 1, e) for i, e in enumerate(entries)],
        )
        conn.commit()


def _project_brief(db):
    return svc.build_project_brief(SLUG, date=DATE)


def _home_brief(db):
    return svc.build_home_brief(date=DATE)


# ── AC1: build_project_brief() includes 'suggested_next' key ─────────────────

def test_ac1_suggested_next_key_present_in_project_brief(fresh_db):
    """AC1: build_project_brief() payload always includes 'suggested_next'."""
    brief = _project_brief(fresh_db)
    assert "suggested_next" in brief, "brief payload must include 'suggested_next' key"


def test_ac1_suggested_next_key_present_in_home_brief(fresh_db):
    """AC1: build_home_brief() payload always includes 'suggested_next'."""
    home = _home_brief(fresh_db)
    assert "suggested_next" in home, "home brief must include 'suggested_next' key"


# ── AC2: capped at 5 lines total ─────────────────────────────────────────────

def test_ac2_suggestions_cap_without_look_ahead(fresh_db):
    """AC2: more than 5 suggestions are truncated to 5."""
    _seed_suggestions(fresh_db, [f"Suggestion {i}" for i in range(8)])
    brief = _project_brief(fresh_db)
    assert len(brief["suggested_next"]) == 5


def test_ac2_suggestions_plus_look_ahead_capped_at_five(fresh_db):
    """AC2: 4 suggestions + 1 look-ahead entry = 5 total."""
    _seed_suggestions(fresh_db, [f"Suggestion {i}" for i in range(6)])
    _seed_look_ahead(fresh_db, ["Sprint 80: add X", "Sprint 81: add Y"])
    brief = _project_brief(fresh_db)
    assert len(brief["suggested_next"]) == 5


def test_ac2_fewer_than_five_allowed(fresh_db):
    """AC2: fewer than 5 items returned when advisor provides fewer."""
    _seed_suggestions(fresh_db, ["A", "B"])
    brief = _project_brief(fresh_db)
    assert len(brief["suggested_next"]) == 2


def test_ac2_three_suggestions_one_look_ahead_total_four(fresh_db):
    """AC2: 3 suggestions + 1 look-ahead = 4 total (under cap)."""
    _seed_suggestions(fresh_db, ["A", "B", "C"])
    _seed_look_ahead(fresh_db, ["Sprint 80: milestone"])
    brief = _project_brief(fresh_db)
    assert len(brief["suggested_next"]) == 4


# ── AC3: first look-ahead entry is the final item ────────────────────────────

def test_ac3_look_ahead_is_final_item(fresh_db):
    """AC3: look-ahead entry appears last in suggested_next."""
    _seed_suggestions(fresh_db, ["Alpha", "Beta"])
    _seed_look_ahead(fresh_db, ["Sprint 80: ship roadmap", "Sprint 81: other"])
    brief = _project_brief(fresh_db)
    last = brief["suggested_next"][-1]
    assert last["type"] == "look_ahead"
    assert "Sprint 80" in last["text"]


def test_ac3_only_first_look_ahead_included(fresh_db):
    """AC3: only the first look-ahead entry (position 1) is appended."""
    _seed_suggestions(fresh_db, ["Alpha"])
    _seed_look_ahead(fresh_db, ["First LA", "Second LA", "Third LA"])
    brief = _project_brief(fresh_db)
    look_ahead_items = [i for i in brief["suggested_next"] if i["type"] == "look_ahead"]
    assert len(look_ahead_items) == 1
    assert look_ahead_items[0]["text"] == "First LA"


def test_ac3_look_ahead_absent_when_none_stored(fresh_db):
    """AC3: no look-ahead item when table is empty."""
    _seed_suggestions(fresh_db, ["Alpha"])
    brief = _project_brief(fresh_db)
    types_ = [i["type"] for i in brief["suggested_next"]]
    assert "look_ahead" not in types_


# ── AC4 / AC7: each item carries 'slug' for Roadmap-tab link ─────────────────

def test_ac4_each_item_has_slug(fresh_db):
    """AC4/AC7: every suggested_next item carries 'slug' for link building."""
    _seed_suggestions(fresh_db, ["Build X", "Build Y"])
    brief = _project_brief(fresh_db)
    for item in brief["suggested_next"]:
        assert "slug" in item, "each item must have 'slug' for href generation"
        assert item["slug"] == SLUG


def test_ac4_look_ahead_item_has_slug(fresh_db):
    """AC4/AC7: look-ahead item also carries 'slug'."""
    _seed_suggestions(fresh_db, ["Build X"])
    _seed_look_ahead(fresh_db, ["Sprint 80: ship roadmap"])
    brief = _project_brief(fresh_db)
    la = next(i for i in brief["suggested_next"] if i["type"] == "look_ahead")
    assert la["slug"] == SLUG


# ── AC5: empty list when no suggestions and no look-ahead ─────────────────────

def test_ac5_empty_when_no_advisor_data(fresh_db):
    """AC5: suggested_next is [] when advisor has never run for this project."""
    brief = _project_brief(fresh_db)
    assert brief["suggested_next"] == []


def test_ac5_empty_when_advisor_tables_missing(tmp_path, monkeypatch):
    """AC5: graceful empty list when advisor tables don't exist (pre-881 schema)."""
    db_file = tmp_path / "no_advisor.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    monkeypatch.setattr(
        svc, "_load_projects",
        lambda: [{"repo": REPO, "name": "Proj"}],
        raising=True,
    )
    monkeypatch.setattr(
        svc, "_sprint_goal",
        lambda project_key, slug, label: "",
        raising=True,
    )
    try:
        # Intentionally no advisor tables — brief must not raise
        brief = svc.build_project_brief(SLUG, date=DATE)
        assert brief["suggested_next"] == []
    finally:
        db_module.DB_PATH = original


# ── AC6: home.html has renderSuggestedNext with section title ─────────────────

@pytest.fixture(scope="module")
def home_html() -> str:
    assert HOME_HTML.exists(), f"home.html not found at {HOME_HTML}"
    return HOME_HTML.read_text(encoding="utf-8")


def test_ac6_render_suggested_next_function_exists(home_html):
    """AC6: home.html defines renderSuggestedNext function."""
    assert "renderSuggestedNext" in home_html, \
        "home.html must define renderSuggestedNext()"


def test_ac6_section_has_visible_title(home_html):
    """AC6: the section renders a distinguishing title like 'Suggested Next'."""
    assert "Suggested Next" in home_html, \
        "home.html must render a 'Suggested Next' section title"


def test_ac6_section_omitted_when_empty(home_html):
    """AC6: renderSuggestedNext returns '' when list is empty."""
    # The function must check for empty list and return '' (no header rendered).
    assert re.search(
        r"function renderSuggestedNext.*?if\s*\(!.*?\.length",
        home_html,
        re.DOTALL,
    ), "renderSuggestedNext must skip rendering when list is empty"


# ── AC7: link href contains /roadmap ─────────────────────────────────────────

def test_ac7_links_contain_roadmap_path(home_html):
    """AC7: suggestion links point to the Roadmap tab (/project/<slug>/roadmap)."""
    # The renderSuggestedNext function must build hrefs with /roadmap
    m = re.search(
        r"function renderSuggestedNext.*?/roadmap",
        home_html,
        re.DOTALL,
    )
    assert m, "renderSuggestedNext must build href containing /roadmap"


def test_ac7_suggested_next_called_in_render_pipeline(home_html):
    """AC7: renderSuggestedNext is called in the main brief render pipeline."""
    assert re.search(r"renderSuggestedNext\s*\(", home_html), \
        "renderSuggestedNext must be called in the render pipeline"
