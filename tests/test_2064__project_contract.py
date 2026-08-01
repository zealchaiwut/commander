"""Behavioral tests for issue #2064 — canonical project= contract.

Acceptance Criteria:
  AC1 — One canonical format (owner/repo), reasoning documented.
  AC2 — Single shared utility replaces ~12 duplicated helpers.
  AC3 — Both forms normalised centrally; endpoint behaviour identical for either form.
  AC4 — Contract documented in endpoint docstrings and CLAUDE.md.
  AC5 — Resolution failure NEVER falls back to a default project (brain fix).
  AC6 — Behavioral: canonical form resolves; non-canonical resolves identically;
         unknown project returns typed error, not another project's data.

All tests exercise actual code paths (CLAUDE.md issue #1746 — no source-regex).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")


# ── Fixture: fake projects list ───────────────────────────────────────────────

FAKE_PROJECTS = [
    {"repo": "zealchaiwut/commander", "name": "Commander"},
    {"repo": "zealchaiwut/perf-coach", "name": "Perf Coach"},
]


# ── AC2 / AC3: resolve_project_path shared utility ───────────────────────────

class TestResolveProjectPath:
    """project_resolver.resolve_project_path is the single shared utility (AC2).

    AC3: owner/repo and bare-slug forms normalise to the same path.
    AC6: canonical form resolves; non-canonical resolves identically;
         unknown → HTTPException(404).
    """

    @pytest.fixture(autouse=True)
    def patch_projects(self, tmp_path):
        """Override load_projects() and _PROJECTS_BASE so tests are filesystem-isolated."""
        import project_resolver as pr
        with (
            patch("project_resolver._PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=FAKE_PROJECTS),
        ):
            yield tmp_path

    def test_canonical_owner_repo_resolves(self, patch_projects):
        """AC6(a): canonical owner/repo form returns _PROJECTS_BASE / slug."""
        import project_resolver as pr
        result = pr.resolve_project_path("zealchaiwut/commander")
        assert result == patch_projects / "commander"

    def test_bare_slug_resolves_to_same_path(self, patch_projects):
        """AC6(a): bare slug resolves to the same path as owner/repo."""
        import project_resolver as pr
        canonical = pr.resolve_project_path("zealchaiwut/commander")
        non_canonical = pr.resolve_project_path("commander")
        assert canonical == non_canonical

    def test_second_project_resolves_correctly(self, patch_projects):
        """AC3: normalisation works for all tracked projects, not just commander."""
        import project_resolver as pr
        result = pr.resolve_project_path("zealchaiwut/perf-coach")
        assert result == patch_projects / "perf-coach"

    def test_unknown_owner_repo_raises_404(self, patch_projects):
        """AC5/AC6(c): unknown project raises HTTPException(404), not FileNotFoundError."""
        from fastapi import HTTPException
        import project_resolver as pr
        with pytest.raises(HTTPException) as exc_info:
            pr.resolve_project_path("nobody/ghost-project")
        assert exc_info.value.status_code == 404

    def test_unknown_bare_slug_raises_404(self, patch_projects):
        """AC6(c): unknown bare slug → 404, never another project's data."""
        from fastapi import HTTPException
        import project_resolver as pr
        with pytest.raises(HTTPException) as exc_info:
            pr.resolve_project_path("ghost-project")
        assert exc_info.value.status_code == 404

    def test_empty_string_raises_400(self, patch_projects):
        """Empty project param → 400, not a silent fallback."""
        from fastapi import HTTPException
        import project_resolver as pr
        with pytest.raises(HTTPException) as exc_info:
            pr.resolve_project_path("")
        assert exc_info.value.status_code == 400

    def test_whitespace_only_raises_400(self, patch_projects):
        """Whitespace-only project param → 400."""
        from fastapi import HTTPException
        import project_resolver as pr
        with pytest.raises(HTTPException) as exc_info:
            pr.resolve_project_path("   ")
        assert exc_info.value.status_code == 400

    def test_case_insensitive_match(self, patch_projects):
        """Slug comparison is case-insensitive (Commander == commander)."""
        import project_resolver as pr
        result = pr.resolve_project_path("COMMANDER")
        assert result == patch_projects / "commander"


# ── AC5: resolve_clone_root normalises owner/repo (docs_service) ─────────────

class TestResolveCloneRoot:
    """docs_service.resolve_clone_root must accept owner/repo, not just bare slug.

    This is the root cause of issue #2052 — the slug comparison failed for
    owner/repo inputs, causing silent cross-project doc bleed.
    """

    @pytest.fixture(autouse=True)
    def setup_fake_project(self, tmp_path):
        """Create a minimal git-clone directory structure for testing."""
        commander_main = tmp_path / "commander" / "main"
        commander_main.mkdir(parents=True)
        (commander_main / ".git").mkdir()

        from routers import docs_service as _ds
        with (
            patch.object(_ds, "_PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=FAKE_PROJECTS),
        ):
            yield tmp_path

    def test_owner_repo_resolves_clone(self):
        """AC5: owner/repo form must reach the correct clone, not fall back."""
        from routers import docs_service
        result = docs_service.resolve_clone_root("zealchaiwut/commander")
        assert "commander" in str(result)

    def test_bare_slug_still_works(self):
        """Bare slug remains supported (backwards compat)."""
        from routers import docs_service
        result = docs_service.resolve_clone_root("commander")
        assert "commander" in str(result)

    def test_unknown_slug_raises_404(self):
        """Unknown project → HTTPException(404), never silently resolved."""
        from fastapi import HTTPException
        from routers import docs_service
        with pytest.raises(HTTPException) as exc_info:
            docs_service.resolve_clone_root("totally-unknown-project")
        assert exc_info.value.status_code == 404

    def test_owner_repo_and_slug_return_same_path(self):
        """owner/repo and bare slug must resolve to the same clone root."""
        from routers import docs_service
        by_full = docs_service.resolve_clone_root("zealchaiwut/commander")
        by_slug = docs_service.resolve_clone_root("commander")
        assert by_full == by_slug


# ── AC5: brain endpoint must not silently fall back to Commander docs ─────────

class TestBrainNoSilentFallback:
    """AC5 — resolution failure must NEVER fall back to a default project.

    Specifically: when the browser sends project=owner/repo and the project
    exists, return that project's data. When the project is unknown, return 404.
    The REPO_ROOT fallback is only for when project= is entirely absent.
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        """TestClient for the brain router with patched project resolution."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import brain as brain_module
        from routers import docs_service as _ds
        import projects as _projects_mod

        # Give the known project a docs directory so the endpoint can scan it.
        commander_main = tmp_path / "commander" / "main"
        commander_main.mkdir(parents=True)
        (commander_main / ".git").mkdir()
        (commander_main / "docs").mkdir()
        (commander_main / "docs" / "test.md").write_text(
            "# Test\n\nCOMMANDER_ONLY_SENTINEL content.\n", encoding="utf-8"
        )

        # A separate docs tree that acts as the "server's own" docs.
        server_docs = tmp_path / "server_root"
        server_docs.mkdir()
        (server_docs / "docs").mkdir()
        (server_docs / "docs" / "server.md").write_text(
            "# Server\n\nSERVER_ONLY_SENTINEL content.\n", encoding="utf-8"
        )

        monkeypatch.setattr(brain_module, "_REPO_ROOT", server_docs)
        monkeypatch.setattr(_ds, "_PROJECTS_BASE", tmp_path)
        monkeypatch.setattr(_projects_mod, "load_projects", lambda: FAKE_PROJECTS)

        app = FastAPI()
        app.include_router(brain_module.router)
        return TestClient(app, raise_server_exceptions=False)

    def test_known_project_owner_repo_returns_project_data(self, client):
        """AC5: project=owner/repo must return THAT project's docs, not the server's."""
        resp = client.get("/api/brain/search?q=COMMANDER_ONLY_SENTINEL&project=zealchaiwut/commander")
        assert resp.status_code == 200
        hits = resp.json()
        # Some match should come from commander's docs, not from server's docs
        assert isinstance(hits, list)
        # Must find the commander-specific sentinel
        assert any("COMMANDER_ONLY_SENTINEL" in str(h) for h in hits), (
            f"Expected commander docs hit, got: {hits}"
        )

    def test_known_project_owner_repo_does_not_return_server_docs(self, client):
        """AC5: server sentinel must NOT appear when a valid project is specified."""
        resp = client.get("/api/brain/search?q=SERVER_ONLY_SENTINEL&project=zealchaiwut/commander")
        assert resp.status_code == 200
        hits = resp.json()
        assert hits == [], (
            f"Server docs leaked into project-scoped response: {hits}"
        )

    def test_unknown_project_returns_404(self, client):
        """AC5/AC6(c): unknown project= → 404, not Commander's own docs."""
        resp = client.get("/api/brain/search?q=anything&project=nobody/ghost-project")
        assert resp.status_code == 404

    def test_no_project_param_uses_server_docs(self, client):
        """When project= is absent, the server's own docs remain the default (intentional)."""
        resp = client.get("/api/brain/search?q=SERVER_ONLY_SENTINEL")
        assert resp.status_code == 200
        hits = resp.json()
        assert isinstance(hits, list)
        assert any("SERVER_ONLY_SENTINEL" in str(h) for h in hits), (
            f"Expected server docs hit without project=, got: {hits}"
        )


# ── AC4: contract is documented in the module ────────────────────────────────

class TestContractDocumented:
    """AC4 — the project_resolver module exposes the contract in its docstring."""

    def test_module_docstring_mentions_canonical_format(self):
        """project_resolver's module docstring must name owner/repo as the canonical form."""
        import project_resolver
        doc = project_resolver.__doc__ or ""
        assert "owner/repo" in doc, (
            "project_resolver module docstring must document 'owner/repo' as canonical format"
        )

    def test_resolve_project_path_has_docstring(self):
        """Public function must document both accepted forms."""
        import project_resolver
        fn_doc = project_resolver.resolve_project_path.__doc__ or ""
        assert fn_doc, "resolve_project_path must have a docstring"
        assert "owner/repo" in fn_doc, "docstring must mention owner/repo"
