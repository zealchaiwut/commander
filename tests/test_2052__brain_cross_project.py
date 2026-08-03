"""Behavioral tests for issue #2052 — Brain cross-project isolation.

AC1  _resolve_docs_root falls back to _REPO_ROOT only when project is None;
     an unresolvable project raises 404.
AC4  resolve_clone_root prefers uat (develop) over main/prd (master).
AC5  (per #1746): two different projects return different payloads; an
     unresolvable project returns 404 instead of Commander's docs; a
     resolvable project returns its own docs.

All tests use TestClient against real routes with mocked boundaries
(docs_service.resolve_clone_root) — no source-regex checks.
"""
from __future__ import annotations

import os
import subprocess
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


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_docs_tree(root: Path, sentinel: str) -> Path:
    """Seed a minimal docs tree whose content is unique to `sentinel`."""
    _write(
        root / "docs" / "decisions" / "2026-01-01-1-test-decision.md",
        f"# Test decision\n\n## Decision\n\n{sentinel} was chosen.\n",
    )
    _write(
        root / "docs" / "architecture.md",
        f"# Architecture\n\nThis project is tagged {sentinel}.\n",
    )
    return root


def _make_app():
    from fastapi import FastAPI
    from routers.brain import router as brain_rtr
    app = FastAPI()
    app.include_router(brain_rtr)
    return app


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(_make_app())


# ── AC5a: two resolvable projects return DIFFERENT payloads ───────────────────

class TestCrossProjectIsolation:
    """Two distinct projects must return distinct panel payloads."""

    def test_two_projects_return_different_payloads(self, tmp_path: Path):
        """Payload sha must differ between two projects with different docs."""
        from fastapi.testclient import TestClient

        alpha_root = _make_docs_tree(tmp_path / "alpha", "SENTINEL_ALPHA_2052")
        beta_root = _make_docs_tree(tmp_path / "beta", "SENTINEL_BETA_2052")

        def _fake_resolve(slug: str):
            from fastapi import HTTPException
            if "alpha" in slug:
                return alpha_root
            if "beta" in slug:
                return beta_root
            raise HTTPException(status_code=404, detail=f"unknown: {slug}")

        from routers import docs_service
        with patch.object(docs_service, "resolve_clone_root", side_effect=_fake_resolve):
            client = TestClient(_make_app())
            resp_alpha = client.get("/api/brain/panels?project=alpha")
            resp_beta = client.get("/api/brain/panels?project=beta")

        assert resp_alpha.status_code == 200, f"alpha panels failed: {resp_alpha.text}"
        assert resp_beta.status_code == 200, f"beta panels failed: {resp_beta.text}"

        data_alpha = resp_alpha.json()
        data_beta = resp_beta.json()

        # The payloads must NOT be byte-identical (the original cross-bleed bug).
        assert data_alpha != data_beta, (
            "Both projects returned identical payloads — cross-project bleed regression.\n"
            f"alpha: {data_alpha}\n"
            f"beta:  {data_beta}"
        )

    def test_resolvable_project_returns_own_docs_not_commanders(
        self, tmp_path: Path, client
    ):
        """A resolvable project must return its own docs, not Commander's."""
        project_root = _make_docs_tree(tmp_path / "myproject", "XSIG_MYPROJECT_2052")

        from routers import docs_service
        with patch.object(docs_service, "resolve_clone_root", return_value=project_root):
            resp = client.get("/api/brain/panels?project=myproject")

        assert resp.status_code == 200
        data = resp.json()
        decisions = data.get("recent_decisions", [])
        # At least one decision must mention our sentinel — content is from the
        # seeded tree, not from Commander's own docs/decisions/.
        assert len(decisions) >= 1, f"Expected decisions from seeded tree; got: {data}"
        titles_and_decisions = " ".join(
            d.get("title", "") + " " + d.get("decision", "")
            for d in decisions
        )
        assert "XSIG_MYPROJECT_2052" in titles_and_decisions, (
            f"Seeded sentinel missing from decisions — wrong tree was served.\n"
            f"Got: {decisions}"
        )

    def test_search_two_projects_return_different_results(self, tmp_path: Path):
        """Search must also be project-scoped — no cross-project bleed in results."""
        alpha_root = _make_docs_tree(tmp_path / "alpha", "SENTINEL_SEARCH_ALPHA_2052")
        beta_root = _make_docs_tree(tmp_path / "beta", "SENTINEL_SEARCH_BETA_2052")

        def _fake_resolve(slug: str):
            from fastapi import HTTPException
            if "alpha" in slug:
                return alpha_root
            if "beta" in slug:
                return beta_root
            raise HTTPException(status_code=404, detail=f"unknown: {slug}")

        from routers import docs_service
        from fastapi.testclient import TestClient
        with patch.object(docs_service, "resolve_clone_root", side_effect=_fake_resolve):
            c = TestClient(_make_app())
            resp_alpha = c.get("/api/brain/search?q=SENTINEL_SEARCH_ALPHA_2052&project=alpha")
            resp_beta = c.get("/api/brain/search?q=SENTINEL_SEARCH_ALPHA_2052&project=beta")

        assert resp_alpha.status_code == 200
        assert resp_beta.status_code == 200

        hits_alpha = resp_alpha.json()
        hits_beta = resp_beta.json()

        # Alpha sentinel present in alpha results, absent in beta results.
        assert len(hits_alpha) >= 1, "Expected hit for alpha sentinel in alpha project"
        assert len(hits_beta) == 0, (
            f"Alpha sentinel found in beta project — cross-project search bleed.\n"
            f"Beta hits: {hits_beta}"
        )


# ── AC1/AC5b: unresolvable project returns 404, never Commander's docs ────────

class TestUnresolvableProjectReturnsError:
    """An unknown project must get 404, not a silent fallback to Commander's docs."""

    def test_panels_unknown_project_returns_404(self, client):
        """GET /api/brain/panels?project=no-such-project must return 404."""
        from fastapi import HTTPException
        from routers import docs_service
        with patch.object(
            docs_service,
            "resolve_clone_root",
            side_effect=HTTPException(status_code=404, detail="Project 'no-such-project' not found"),
        ):
            resp = client.get("/api/brain/panels?project=no-such-project")

        assert resp.status_code == 404, (
            f"Expected 404 for unknown project, got {resp.status_code}.\n"
            "If this returns 200, the silent Commander-docs fallback has regressed."
        )

    def test_search_unknown_project_returns_404(self, client):
        """GET /api/brain/search?project=no-such returns 404, not Commander results."""
        from fastapi import HTTPException
        from routers import docs_service
        with patch.object(
            docs_service,
            "resolve_clone_root",
            side_effect=HTTPException(status_code=404, detail="Project not found"),
        ):
            resp = client.get("/api/brain/search?q=anything&project=no-such")

        assert resp.status_code == 404, (
            f"Expected 404 for unknown project, got {resp.status_code}."
        )

    def test_no_project_param_uses_server_docs(self, client, tmp_path: Path):
        """Omitting project= must NOT call resolve_clone_root — uses _REPO_ROOT."""
        from routers import docs_service
        call_count = {"n": 0}
        original = docs_service.resolve_clone_root

        def _spy(slug):
            call_count["n"] += 1
            return original(slug)

        with patch.object(docs_service, "resolve_clone_root", side_effect=_spy):
            resp = client.get("/api/brain/panels")

        # resolve_clone_root must NOT be called when project= is absent.
        assert call_count["n"] == 0, (
            f"resolve_clone_root was called {call_count['n']} time(s) without a project param — "
            "the None branch should short-circuit to _REPO_ROOT."
        )
        assert resp.status_code == 200


# ── AC4: resolve_clone_root prefers uat (develop) over main/prd ──────────────

class TestCloneSearchOrder:
    """AC4 — uat is checked before main/prd in the nested layout."""

    def _make_project(self, base: Path, slug: str, sub: str) -> Path:
        """Create a fake nested project with one sub-clone directory."""
        clone = base / slug / sub
        clone.mkdir(parents=True)
        (clone / ".git").mkdir()
        return clone

    def _fake_projects(self, slug: str):
        return [{"repo": f"testowner/{slug}"}]

    def test_uat_preferred_over_main(self, tmp_path: Path):
        """When both uat/ and main/ exist, uat/ is returned."""
        from routers import docs_service

        slug = "alpha-proj"
        uat_clone = self._make_project(tmp_path, slug, "uat")
        main_clone = self._make_project(tmp_path, slug, "main")

        with (
            patch.object(docs_service, "_PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=self._fake_projects(slug)),
        ):
            result = docs_service.resolve_clone_root(slug)

        assert result == uat_clone, (
            f"Expected uat clone ({uat_clone}), got {result}.\n"
            "Brain should prefer develop-branch uat/ over master-branch main/."
        )

    def test_uat_preferred_over_prd(self, tmp_path: Path):
        """When both uat/ and prd/ exist, uat/ is returned."""
        from routers import docs_service

        slug = "beta-proj"
        uat_clone = self._make_project(tmp_path, slug, "uat")
        prd_clone = self._make_project(tmp_path, slug, "prd")

        with (
            patch.object(docs_service, "_PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=self._fake_projects(slug)),
        ):
            result = docs_service.resolve_clone_root(slug)

        assert result == uat_clone, (
            f"Expected uat clone ({uat_clone}), got {result}."
        )

    def test_falls_back_to_main_when_no_uat(self, tmp_path: Path):
        """When only main/ exists, main/ is returned."""
        from routers import docs_service

        slug = "gamma-proj"
        main_clone = self._make_project(tmp_path, slug, "main")

        with (
            patch.object(docs_service, "_PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=self._fake_projects(slug)),
        ):
            result = docs_service.resolve_clone_root(slug)

        assert result == main_clone

    def test_falls_back_to_prd_when_no_uat_or_main(self, tmp_path: Path):
        """When only prd/ exists, prd/ is returned."""
        from routers import docs_service

        slug = "delta-proj"
        prd_clone = self._make_project(tmp_path, slug, "prd")

        with (
            patch.object(docs_service, "_PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=self._fake_projects(slug)),
        ):
            result = docs_service.resolve_clone_root(slug)

        assert result == prd_clone

    def test_owner_repo_format_resolves_same_as_bare_slug(self, tmp_path: Path):
        """owner/repo format must resolve identically to bare slug (AC2)."""
        from routers import docs_service

        slug = "epsilon-proj"
        uat_clone = self._make_project(tmp_path, slug, "uat")

        with (
            patch.object(docs_service, "_PROJECTS_BASE", tmp_path),
            patch("projects.load_projects", return_value=self._fake_projects(slug)),
        ):
            result_slug = docs_service.resolve_clone_root(slug)
            result_full = docs_service.resolve_clone_root(f"testowner/{slug}")

        assert result_slug == uat_clone
        assert result_full == uat_clone, (
            "owner/repo form did not resolve to the same path as bare slug."
        )
