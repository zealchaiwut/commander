"""Behavioral tests for issue #2028 — Brain search: GET /api/brain/search (FTS5).

AC1 — FTS5 endpoint:
  (a) Seed docs tree → query matching a phrase returns that file as a hit.
  (b) Query matching nothing returns empty list, not an error.
  (c) Malformed/injection-y q (unbalanced quote, bare AND OR, *) returns [] or 400, never 500.
  (d) All four content sources (docs/, bulk-create/, decisions/, retros/) each produce
      a hit when queried for a phrase unique to that source.

AC4 (backend) — behavioral via TestClient against the real route with a seeded
  tmp docs tree.  No source-regex checks (CLAUDE.md issue #1746).

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture (pattern copied
from test_2031__false_orphan_sweep.py).  The FTS5 index uses :memory: SQLite and
tmp_path docs trees — no persistent index file is written to the repo.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", str(DASHBOARD_DIR / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'.\n"
        "Ensure all git-touching steps are stubbed."
    )


# ── Import the service under test ─────────────────────────────────────────────

from routers import brain_service  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def docs_tree(tmp_path: Path) -> Path:
    """Seed a minimal docs tree with one file per source."""
    root = tmp_path / "repo"

    # Source: docs/ (general)
    _write(
        root / "docs" / "architecture.md",
        "# Architecture\n\nThis is the XRAY_SENTINEL_DOCS architecture overview.\n",
    )

    # Source: bulk-create/
    _write(
        root / "docs" / "bulk-create" / "2026-07-01-1-planning.md",
        "# Planning prompt\n\nBulk create session: XRAY_SENTINEL_BULK planning notes.\n",
    )

    # Source: decisions/
    _write(
        root / "docs" / "decisions" / "2026-07-02-1-test-decision.md",
        "# Test decision\n\n## Context\n\nXRAY_SENTINEL_DECISION context here.\n\n"
        "## Decision\n\nWe chose option A.\n\n## Consequences\n\nMinimal.\n",
    )

    # Source: retros/ (must match pattern YYYY-MM-DD-sprint-*.md)
    _write(
        root / "docs" / "changelog" / "uat" / "2026-07-01-sprint-99.md",
        "# UAT Changelog — Sprint 99\n\n## Key Learnings\n\n"
        "- XRAY_SENTINEL_RETRO learning from sprint 99\n",
    )

    return root


# ── AC1(a): hit in each source ────────────────────────────────────────────────

class TestSearchHits:
    """Seed a docs tree and verify query returns hits from each source."""

    def test_docs_source_returns_hit(self, docs_tree: Path):
        hits = brain_service.search("XRAY_SENTINEL_DOCS", docs_tree)
        assert len(hits) >= 1
        paths = [h["path"] for h in hits]
        assert any("architecture.md" in p for p in paths), f"No architecture.md in {paths}"
        sources = [h["source"] for h in hits]
        assert "docs" in sources

    def test_bulk_create_source_returns_hit(self, docs_tree: Path):
        hits = brain_service.search("XRAY_SENTINEL_BULK", docs_tree)
        assert len(hits) >= 1
        paths = [h["path"] for h in hits]
        assert any("bulk-create" in p for p in paths), f"No bulk-create file in {paths}"
        sources = [h["source"] for h in hits]
        assert "bulk-create" in sources

    def test_decisions_source_returns_hit(self, docs_tree: Path):
        hits = brain_service.search("XRAY_SENTINEL_DECISION", docs_tree)
        assert len(hits) >= 1
        sources = [h["source"] for h in hits]
        assert "decisions" in sources

    def test_retros_source_returns_hit(self, docs_tree: Path):
        hits = brain_service.search("XRAY_SENTINEL_RETRO", docs_tree)
        assert len(hits) >= 1
        sources = [h["source"] for h in hits]
        assert "retros" in sources

    def test_all_four_sources_indexed(self, docs_tree: Path):
        """One query per source — all four must produce a hit (AC1-d)."""
        for phrase, expected_source in [
            ("XRAY_SENTINEL_DOCS", "docs"),
            ("XRAY_SENTINEL_BULK", "bulk-create"),
            ("XRAY_SENTINEL_DECISION", "decisions"),
            ("XRAY_SENTINEL_RETRO", "retros"),
        ]:
            hits = brain_service.search(phrase, docs_tree)
            sources = [h["source"] for h in hits]
            assert expected_source in sources, (
                f"Source '{expected_source}' not found for phrase '{phrase}'; got {sources}"
            )

    def test_hit_has_required_fields(self, docs_tree: Path):
        """Each hit must include path, source, and snippet."""
        hits = brain_service.search("XRAY_SENTINEL_DOCS", docs_tree)
        assert len(hits) >= 1
        h = hits[0]
        assert "path" in h
        assert "source" in h
        assert "snippet" in h
        assert isinstance(h["snippet"], str)
        assert len(h["snippet"]) > 0


# ── AC1(b): empty result on no match ─────────────────────────────────────────

class TestNoMatch:
    def test_query_matching_nothing_returns_empty_list(self, docs_tree: Path):
        hits = brain_service.search("ZZZNOMATCH_UNICORN_99999", docs_tree)
        assert hits == []

    def test_empty_query_returns_empty_list(self, docs_tree: Path):
        hits = brain_service.search("", docs_tree)
        assert hits == []

    def test_whitespace_only_query_returns_empty_list(self, docs_tree: Path):
        hits = brain_service.search("   ", docs_tree)
        assert hits == []


# ── AC1(c): malformed queries — no 500 ───────────────────────────────────────

class TestMalformedQuery:
    """Malformed/injection-y queries must return [] or raise HTTPException(400),
    never raise an unhandled exception (which would become a 500)."""

    @pytest.mark.parametrize("bad_q", [
        '"unbalanced quote',       # unbalanced double-quote
        "AND OR",                  # bare operators
        "NOT",                     # lone NOT
        "*",                       # bare wildcard (FTS5 prefix asterisk alone)
        'hello "world',            # unbalanced quote in middle
        '"""',                     # multiple unbalanced quotes
        "()",                      # empty parentheses
    ])
    def test_malformed_query_returns_list_not_exception(self, bad_q: str, docs_tree: Path):
        """Must return a list (possibly empty) — must not raise an exception."""
        result = brain_service.search(bad_q, docs_tree)
        assert isinstance(result, list), (
            f"Expected list for malformed query {bad_q!r}, got {type(result)}"
        )


# ── AC1(c): TestClient behavioral test for the HTTP endpoint ─────────────────

class TestEndpoint:
    """Behavioral test via TestClient — exercises the real HTTP route."""

    @pytest.fixture(autouse=True)
    def _patch_docs_root(self, docs_tree: Path, monkeypatch):
        """Redirect the brain router to use the seeded tmp docs tree."""
        from routers import brain as brain_router_module
        monkeypatch.setattr(brain_router_module, "_REPO_ROOT", docs_tree)

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routers.brain import router as brain_rtr
        app = FastAPI()
        app.include_router(brain_rtr)
        return TestClient(app)

    def test_search_endpoint_returns_hits(self, client):
        resp = client.get("/api/brain/search?q=XRAY_SENTINEL_DOCS")
        assert resp.status_code == 200
        hits = resp.json()
        assert isinstance(hits, list)
        assert len(hits) >= 1
        assert any(h["source"] == "docs" for h in hits)

    def test_search_endpoint_empty_query_returns_empty_list(self, client):
        resp = client.get("/api/brain/search?q=")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_endpoint_no_match_returns_empty_list(self, client):
        resp = client.get("/api/brain/search?q=ZZZNOMATCH_UNICORN_99999")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_endpoint_malformed_query_not_500(self, client):
        """Malformed FTS5 query must not produce a 500."""
        for bad_q in ['"unbalanced', "AND OR", "*"]:
            resp = client.get("/api/brain/search", params={"q": bad_q})
            assert resp.status_code in (200, 400), (
                f"Got {resp.status_code} for malformed query {bad_q!r}"
            )
            # 200 must return a list; 400 is acceptable too
            if resp.status_code == 200:
                assert isinstance(resp.json(), list)

    def test_panels_endpoint_returns_structure(self, client):
        resp = client.get("/api/brain/panels")
        assert resp.status_code == 200
        data = resp.json()
        assert "recent_decisions" in data
        assert "open_decisions" in data
        assert "last_learnings" in data
        assert "backlog_rationale" in data
        assert isinstance(data["recent_decisions"], list)
        assert isinstance(data["open_decisions"], list)
        assert isinstance(data["last_learnings"], list)
        assert isinstance(data["backlog_rationale"], list)

    def test_panels_recent_decisions_populated(self, client):
        resp = client.get("/api/brain/panels")
        assert resp.status_code == 200
        data = resp.json()
        # Should find the seeded decision file
        assert len(data["recent_decisions"]) >= 1
        d = data["recent_decisions"][0]
        assert "path" in d
        assert "title" in d


# ── get_panels: last_learnings from retro ────────────────────────────────────

class TestPanels:
    def test_last_learnings_extracted_from_retro(self, docs_tree: Path):
        panels = brain_service.get_panels(docs_tree)
        learnings = panels["last_learnings"]
        assert isinstance(learnings, list)
        # The seeded retro has one bullet with XRAY_SENTINEL_RETRO
        assert len(learnings) >= 1
        assert any("XRAY_SENTINEL_RETRO" in l for l in learnings)

    def test_recent_decisions_lists_adrs(self, docs_tree: Path):
        panels = brain_service.get_panels(docs_tree)
        decisions = panels["recent_decisions"]
        assert isinstance(decisions, list)
        assert len(decisions) >= 1
        d = decisions[0]
        assert "path" in d
        assert "title" in d
        assert "decision" in d

    def test_panels_empty_for_missing_docs_root(self, tmp_path: Path):
        empty_root = tmp_path / "empty_repo"
        empty_root.mkdir()
        panels = brain_service.get_panels(empty_root)
        assert panels["recent_decisions"] == []
        assert panels["open_decisions"] == []
        assert panels["last_learnings"] == []
        assert panels["backlog_rationale"] == []

    def test_open_decisions_finds_marker(self, tmp_path: Path):
        root = tmp_path / "repo"
        _write(
            root / "docs" / "planning.md",
            "# Planning\n\nSome context ⟶ DECISION: which database to use?\n",
        )
        panels = brain_service.get_panels(root)
        assert len(panels["open_decisions"]) >= 1
        item = panels["open_decisions"][0]
        assert "⟶ DECISION" in item["line"]
