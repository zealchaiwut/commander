"""Tests for issue #1860 — changelog index API endpoint.

AC coverage:
  AC1 — GET /api/projects/{slug}/changelog returns entries from both
         uat/ and prd/ newest-first, each with env, date, slug, path,
         title, excerpt
  AC2 — env= filters to one environment; limit= caps result count
  AC3 — _template.md excluded; malformed filenames (no date prefix)
         included with date null rather than crashing
  AC4 — Behavioral test: seed a temp docs/changelog tree with
         2 uat + 1 prd entries + template, assert order, filtering,
         and template exclusion
  AC5 — Zero GitHub API calls
"""
from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routers.changelog_service as changelog_service  # noqa: E402
from routers.changelog import router as changelog_router  # noqa: E402


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_app():
    app = FastAPI()
    app.include_router(changelog_router)
    return TestClient(app)


def _build_changelog_tree(base: Path) -> Path:
    """Seed a docs/changelog tree: 2 uat + 1 prd entries + templates.

    Returns the fake clone root.
    """
    clone = base / "testproject"
    clone.mkdir(parents=True)
    (clone / ".git").mkdir()

    uat_dir = clone / "docs" / "changelog" / "uat"
    uat_dir.mkdir(parents=True)
    prd_dir = clone / "docs" / "changelog" / "prd"
    prd_dir.mkdir(parents=True)

    # UAT entry 1 — newer
    (uat_dir / "2026-06-10-sprint-50.md").write_text(
        "# UAT Changelog — Sprint 50\n\n"
        "First paragraph for sprint 50.\n\nSecond paragraph.",
        encoding="utf-8",
    )
    # UAT entry 2 — older
    (uat_dir / "2026-05-01-sprint-30.md").write_text(
        "# UAT Changelog — Sprint 30\n\nFirst paragraph for sprint 30.",
        encoding="utf-8",
    )
    # PRD entry
    (prd_dir / "2026-05-20-sprint-40.md").write_text(
        "# PRD Changelog — Sprint 40\n\nDeployed to production.",
        encoding="utf-8",
    )
    # Templates — must be excluded
    (uat_dir / "_template.md").write_text(
        "# Template\n\nThis is a template file.",
        encoding="utf-8",
    )
    (prd_dir / "_template.md").write_text(
        "# Template\n\nThis is a template file.",
        encoding="utf-8",
    )
    # Malformed filename — no date prefix — must be included with date=null
    (uat_dir / "no-date-here.md").write_text(
        "# No Date Entry\n\nThis file has no date prefix.",
        encoding="utf-8",
    )

    return clone


# ── AC1: returns both envs newest-first with required fields ─────────────────

class TestChangelogBothEnvs:
    """AC1 — entries from both uat/ and prd/, newest-first, all fields."""

    def test_returns_200(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        r = client.get("/api/projects/testproject/changelog")
        assert r.status_code == 200

    def test_returns_list(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        assert isinstance(data, list)

    def test_includes_both_envs(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        envs = {entry["env"] for entry in data}
        assert "uat" in envs
        assert "prd" in envs

    def test_each_entry_has_required_fields(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        assert len(data) > 0
        for entry in data:
            for field in ("env", "date", "slug", "path", "title", "excerpt"):
                assert field in entry, f"Missing field '{field}' in {entry}"

    def test_newest_first_ordering(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        dated = [e for e in data if e["date"] is not None]
        dates = [e["date"] for e in dated]
        assert dates == sorted(dates, reverse=True), (
            f"Entries not newest-first: {dates}"
        )

    def test_title_extracted_from_first_heading(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        entry_50 = next(
            e for e in data if "sprint-50" in e.get("slug", "")
        )
        assert entry_50["title"] == "UAT Changelog — Sprint 50"

    def test_excerpt_is_first_paragraph(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        entry_50 = next(
            e for e in data if "sprint-50" in e.get("slug", "")
        )
        assert entry_50["excerpt"] == "First paragraph for sprint 50."

    def test_path_is_relative(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        for entry in data:
            assert not entry["path"].startswith("/"), (
                f"path should be relative, got: {entry['path']}"
            )

    def test_prd_path_correct(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        prd_entry = next(
            e for e in data
            if e["env"] == "prd" and e["date"] is not None
        )
        assert prd_entry["path"] == (
            "docs/changelog/prd/2026-05-20-sprint-40.md"
        )


# ── AC2: env filter and limit ────────────────────────────────────────────────

class TestEnvFilterAndLimit:
    """AC2 — env= filters to one env; limit= caps result count."""

    def test_env_uat_returns_only_uat(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get(
            "/api/projects/testproject/changelog?env=uat"
        ).json()
        assert all(e["env"] == "uat" for e in data)

    def test_env_prd_returns_only_prd(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get(
            "/api/projects/testproject/changelog?env=prd"
        ).json()
        assert all(e["env"] == "prd" for e in data)

    def test_env_uat_excludes_prd_entries(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get(
            "/api/projects/testproject/changelog?env=uat"
        ).json()
        assert not any(e["env"] == "prd" for e in data)

    def test_limit_caps_results(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get(
            "/api/projects/testproject/changelog?limit=1"
        ).json()
        assert len(data) == 1

    def test_limit_larger_than_total_returns_all(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        all_data = client.get("/api/projects/testproject/changelog").json()
        limited_data = client.get(
            "/api/projects/testproject/changelog"
            f"?limit={len(all_data) + 100}"
        ).json()
        assert len(limited_data) == len(all_data)

    def test_limit_with_env_filter(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get(
            "/api/projects/testproject/changelog?env=uat&limit=1"
        ).json()
        assert len(data) == 1
        assert data[0]["env"] == "uat"


# ── AC3: template exclusion and malformed filenames ──────────────────────────

class TestTemplateExclusionAndMalformed:
    """AC3 — _template.md excluded; malformed filenames, date=null."""

    def test_template_files_excluded(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        for entry in data:
            assert "_template" not in entry["path"], (
                f"_template.md must be excluded: found {entry['path']}"
            )
            assert entry.get("slug") != "_template", (
                f"_template slug must be excluded: found {entry}"
            )

    def test_malformed_filename_included_with_null_date(
        self, tmp_path, monkeypatch
    ):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        malformed = [e for e in data if "no-date-here" in e["path"]]
        assert len(malformed) == 1, (
            "Malformed filename should be included once, "
            f"found: {malformed}"
        )
        assert malformed[0]["date"] is None, (
            "Malformed entry should have date=null, "
            f"got: {malformed[0]['date']}"
        )

    def test_malformed_title_still_extracted(self, tmp_path, monkeypatch):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        data = client.get("/api/projects/testproject/changelog").json()
        malformed = next(e for e in data if "no-date-here" in e["path"])
        assert malformed["title"] == "No Date Entry"
        assert "no date prefix" in malformed["excerpt"].lower()


# ── AC4: behavioral seed test ────────────────────────────────────────────────

class TestBehavioralSeed:
    """AC4 — seed a temp tree, assert order, filtering, template exclusion."""

    def test_seeded_tree_full_behavior(self, tmp_path, monkeypatch):
        """Seed 2 uat + 1 prd + template; assert all four AC props."""
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        # --- No filter: both envs, newest-first, template excluded ---
        all_data = client.get("/api/projects/testproject/changelog").json()

        # Template excluded
        paths = [e["path"] for e in all_data]
        assert not any("_template" in p for p in paths), (
            f"_template must be excluded; paths: {paths}"
        )

        # Order: 2026-06-10 (uat) > 2026-05-20 (prd) > 2026-05-01 (uat)
        dated = [e for e in all_data if e["date"] is not None]
        dates = [e["date"] for e in dated]
        assert dates == sorted(dates, reverse=True), (
            f"Expected newest-first, got: {dates}"
        )
        assert dated[0]["date"] == "2026-06-10"
        assert dated[0]["env"] == "uat"
        assert dated[1]["date"] == "2026-05-20"
        assert dated[1]["env"] == "prd"
        assert dated[2]["date"] == "2026-05-01"
        assert dated[2]["env"] == "uat"

        # --- env=uat filter ---
        uat_data = client.get(
            "/api/projects/testproject/changelog?env=uat"
        ).json()
        assert all(e["env"] == "uat" for e in uat_data)
        assert not any("_template" in e["path"] for e in uat_data)

        # --- env=prd filter ---
        prd_data = client.get(
            "/api/projects/testproject/changelog?env=prd"
        ).json()
        prd_dated = [e for e in prd_data if e["date"] is not None]
        assert len(prd_dated) == 1
        assert prd_dated[0]["date"] == "2026-05-20"

        # --- limit=2 ---
        limited = client.get(
            "/api/projects/testproject/changelog?limit=2"
        ).json()
        assert len(limited) == 2


# ── AC5: zero GitHub API calls ───────────────────────────────────────────────

class TestZeroGitHubCalls:
    """AC5 — endpoint makes zero GitHub API calls (pure filesystem reads)."""

    def test_changelog_makes_zero_gh_calls(
        self, tmp_path, monkeypatch, call_budget_fixture
    ):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        client.get("/api/projects/testproject/changelog")
        call_budget_fixture.assert_zero_gh()

    def test_changelog_env_filter_makes_zero_gh_calls(
        self, tmp_path, monkeypatch, call_budget_fixture
    ):
        clone = _build_changelog_tree(tmp_path)
        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", lambda slug: clone
        )
        client = _make_app()

        client.get("/api/projects/testproject/changelog?env=uat")
        call_budget_fixture.assert_zero_gh()


# ── Unknown slug ─────────────────────────────────────────────────────────────

class TestUnknownSlug:
    """Unknown project slug should return 404."""

    def test_unknown_slug_returns_404(self, monkeypatch):
        def _raise_404(slug):
            raise HTTPException(
                status_code=404, detail=f"Project '{slug}' not found"
            )

        monkeypatch.setattr(
            changelog_service, "resolve_clone_root", _raise_404
        )
        client = _make_app()

        r = client.get("/api/projects/does-not-exist/changelog")
        assert r.status_code == 404
