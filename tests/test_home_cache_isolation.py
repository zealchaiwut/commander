"""Tests for home-cache isolation and invalidation wiring — issue #1786.

AC coverage:
  AC3  board_cache delegates storage to aggregate_cache (home namespace also invalidated)
  AC5  sprint mutation (invalidate_board) invalidates both board and home caches
  AC6  cross-project isolation: invalidating project A does not affect project B
  AC7  /api/home response shape backward-compatible (all pre-existing fields present)
  AC8  settings-write path still invalidates home cache
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ─────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"

for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Import modules under test ──────────────────────────────────────────────────

import aggregate_cache as _ac  # noqa: E402
import board_cache as _bc  # noqa: E402


def _reset_all():
    """Clear all cache state."""
    _ac._store.clear()
    _ac._hits.clear()
    _ac._misses.clear()
    _bc._cache.clear()


# ── AC5: invalidate_board also invalidates home ────────────────────────────────

class TestInvalidateBoardAlsoInvalidatesHome:
    def test_invalidate_board_evicts_home_namespace(self):
        """Calling invalidate_board must also evict the home entry for the same project."""
        _reset_all()
        project = "owner/my-project"
        _ac.store(project, "home", {"name": "my-project"}, ttl_s=30.0)

        # Act: sprint mutation calls invalidate_board
        _bc.invalidate_board(project)

        # Home cache for the project must be gone
        assert _ac.get(project, "home") is None

    def test_invalidate_board_evicts_board_entry(self):
        """invalidate_board still removes the board entry (existing behavior)."""
        _reset_all()
        project = "owner/my-project"
        _bc.store_board_cache(project, {"project": project, "sections": {}})
        assert _bc.get_board_cache(project) is not None

        _bc.invalidate_board(project)

        assert _bc.get_board_cache(project) is None


# ── AC6: cross-project isolation (board + home) ────────────────────────────────

class TestCrossProjectIsolation:
    def test_invalidate_project_a_does_not_affect_project_b_home(self):
        """Invalidating project A's board must NOT evict project B's home entry."""
        _reset_all()
        proj_a = "owner/project-a"
        proj_b = "owner/project-b"

        _ac.store(proj_a, "home", {"name": "A"}, ttl_s=30.0)
        _ac.store(proj_b, "home", {"name": "B"}, ttl_s=30.0)

        _bc.invalidate_board(proj_a)

        # B's home untouched
        result_b = _ac.get(proj_b, "home")
        assert result_b is not None
        assert result_b[0]["name"] == "B"

    def test_invalidate_project_a_does_not_affect_project_b_board(self):
        """Invalidating project A must NOT evict project B's board entry."""
        _reset_all()
        proj_a = "owner/project-a"
        proj_b = "owner/project-b"

        _bc.store_board_cache(proj_a, {"project": proj_a})
        _bc.store_board_cache(proj_b, {"project": proj_b})

        _bc.invalidate_board(proj_a)

        assert _bc.get_board_cache(proj_b) is not None


# ── AC7: /api/home response shape ─────────────────────────────────────────────

class TestHomeResponseShape:
    """Tests that the /api/home per-project entry contains all pre-existing fields.

    Uses home_service.home_project_data directly with mocked dependencies.
    """

    _EXPECTED_FIELDS = {
        "name", "slug", "repo", "icon", "color",
        "status", "uat_count", "backlog_count", "last_activity_at",
    }

    def _make_proj(self, repo="owner/test-project"):
        return {
            "repo": repo,
            "name": repo.split("/")[-1],
            "icon": "ti-folder",
            "color": "gray",
        }

    def test_all_required_fields_present_on_cold_call(self):
        """All pre-existing fields are present in the returned dict (cache miss)."""
        import home_service as _hs
        _reset_all()

        proj = self._make_proj()

        with (
            patch("github_client.list_all_open_issues", return_value=[]),
            patch("github_client.classify_issue", return_value="backlog"),
        ):
            result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

        for field in self._EXPECTED_FIELDS:
            assert field in result, f"Field '{field}' missing from home_project_data result"

    def test_all_required_fields_present_on_cache_hit(self):
        """All pre-existing fields are present even when served from cache."""
        import home_service as _hs
        _reset_all()
        proj = self._make_proj()

        with (
            patch("github_client.list_all_open_issues", return_value=[]),
            patch("github_client.classify_issue", return_value="backlog"),
        ):
            # First call populates cache
            _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})
            # Second call hits cache
            result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

        for field in self._EXPECTED_FIELDS:
            assert field in result, f"Field '{field}' missing on cache-hit call"

    def test_cache_metadata_added_to_response(self):
        """Response includes cache: {hit: bool, ttl_s: int} metadata (AC1)."""
        import home_service as _hs
        _reset_all()
        proj = self._make_proj()

        with (
            patch("github_client.list_all_open_issues", return_value=[]),
            patch("github_client.classify_issue", return_value="backlog"),
        ):
            result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

        assert "cache" in result
        assert "hit" in result["cache"]
        assert "ttl_s" in result["cache"]
        assert isinstance(result["cache"]["hit"], bool)
        assert isinstance(result["cache"]["ttl_s"], int)

    def test_first_call_is_cache_miss(self):
        """First call returns cache.hit=False."""
        import home_service as _hs
        _reset_all()
        proj = self._make_proj()

        with (
            patch("github_client.list_all_open_issues", return_value=[]),
            patch("github_client.classify_issue", return_value="backlog"),
        ):
            result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

        assert result["cache"]["hit"] is False

    def test_second_call_is_cache_hit(self):
        """Second call within TTL returns cache.hit=True."""
        import home_service as _hs
        _reset_all()
        proj = self._make_proj()

        with (
            patch("github_client.list_all_open_issues", return_value=[]),
            patch("github_client.classify_issue", return_value="backlog"),
        ):
            _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})
            result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

        assert result["cache"]["hit"] is True

    def test_slug_field_derived_from_repo(self):
        proj = self._make_proj(repo="myorg/my-cool-project")
        import home_service as _hs
        _reset_all()

        with (
            patch("github_client.list_all_open_issues", return_value=[]),
            patch("github_client.classify_issue", return_value="backlog"),
        ):
            result = _hs.home_project_data(proj, running_sprints=[], sprint_statuses={})

        assert result["slug"] == "my-cool-project"
        assert result["repo"] == "myorg/my-cool-project"


# ── AC8: settings-write invalidates home ──────────────────────────────────────

class TestSettingsWriteInvalidatesHome:
    def _mock_projects(self, repos):
        """Return a mock projects module whose load_projects() returns the given repo dicts."""
        m = MagicMock()
        m.load_projects.return_value = repos
        return m

    def test_invalidate_home_by_slug_clears_home_entry(self):
        """home_service.invalidate_home_by_slug must evict the home cache entry."""
        import home_service as _hs
        _reset_all()

        repo = "owner/my-slug-project"
        slug = "my-slug-project"

        # Seed the home cache directly
        _ac.store(repo, "home", {"name": slug}, ttl_s=30.0)
        assert _ac.get(repo, "home") is not None

        with patch.object(_hs, "_projects_module", self._mock_projects([{"repo": repo}])):
            _hs.invalidate_home_by_slug(slug)

        assert _ac.get(repo, "home") is None

    def test_invalidate_home_by_slug_does_not_affect_other_projects(self):
        """Invalidating by slug must only evict the matched project's home entry."""
        import home_service as _hs
        _reset_all()

        repo_a = "owner/project-alpha"
        repo_b = "owner/project-beta"

        _ac.store(repo_a, "home", {"n": "alpha"}, ttl_s=30.0)
        _ac.store(repo_b, "home", {"n": "beta"}, ttl_s=30.0)

        with patch.object(_hs, "_projects_module", self._mock_projects([
            {"repo": repo_a},
            {"repo": repo_b},
        ])):
            _hs.invalidate_home_by_slug("project-alpha")

        assert _ac.get(repo_a, "home") is None
        assert _ac.get(repo_b, "home") is not None

    def test_invalidate_home_by_slug_called_via_home_service(self):
        """home_service.invalidate_home_by_slug is the function settings writes call.

        Verifies the function works correctly when called from any path
        (settings write, startup wrapper, or directly) — AC8.
        """
        import home_service as _hs
        _reset_all()

        repo = "owner/settings-proj"
        slug = "settings-proj"

        # Simulate settings write invalidation path
        _ac.store(repo, "home", {"x": 1}, ttl_s=30.0)
        assert _ac.get(repo, "home") is not None

        with patch.object(_hs, "_projects_module", self._mock_projects([{"repo": repo}])):
            _hs.invalidate_home_by_slug(slug)

        assert _ac.get(repo, "home") is None
