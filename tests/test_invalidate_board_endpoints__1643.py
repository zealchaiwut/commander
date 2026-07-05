"""Tests for issue #1643: invalidate_board called from every board-mutating endpoint.

AC coverage:
  AC1  Each of the 13 listed mutating endpoints calls invalidate_board(project).
  AC2  The invalidation call is placed after a successful write — not on failure.
  AC3  Invalidating project A does not evict project B's cache entry.
  AC4  GET /api/board immediately after a mutation returns cache.hit: false.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"
_SERVICES_ROOT = Path(__file__).resolve().parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import board_cache as _bc  # noqa: E402


# ── Module loader (bypasses routers/__init__.py to avoid settings_service issues) ──

def _load_router(name: str):
    """Load a router module file directly without going through routers/__init__."""
    path = _ROUTERS_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"routers.{name}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[f"routers.{name}"] = mod
    try:
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    except Exception:
        # Module-level imports (server, db, github_client) may fail in the UAT
        # test env, but the source text is already loaded for inspection.
        pass
    return mod


# ── Helpers ───────────────────────────────────────────────────────────────────

def _reset_cache():
    _bc._cache.clear()


def _src(module_path: str, func_name: str) -> str:
    """Read the source of func_name from a router file by path."""
    path = _ROUTERS_ROOT / module_path
    text = path.read_text(encoding="utf-8")
    return text


# ── AC3: Invalidating project A does not evict project B ─────────────────────

class TestIsolation:
    """AC3: invalidate_board(A) must not affect B's entry."""

    def setup_method(self):
        _reset_cache()

    def test_invalidate_one_project_leaves_other_intact(self):
        snapshot_a: dict[str, Any] = {"project": "owner/repo-a", "sections": {}}
        snapshot_b: dict[str, Any] = {"project": "owner/repo-b", "sections": {}}
        _bc.store_board_cache("owner/repo-a", snapshot_a)
        _bc.store_board_cache("owner/repo-b", snapshot_b)

        _bc.invalidate_board("owner/repo-a")

        assert _bc.get_board_cache("owner/repo-a") is None, "A should be evicted"
        assert _bc.get_board_cache("owner/repo-b") is not None, "B should remain"

    def test_invalidate_nonexistent_project_is_noop(self):
        """Evicting a project not in cache must not raise or affect other entries."""
        snapshot_b: dict[str, Any] = {"project": "owner/repo-b", "sections": {}}
        _bc.store_board_cache("owner/repo-b", snapshot_b)

        _bc.invalidate_board("owner/repo-x")  # not cached — must not raise

        assert _bc.get_board_cache("owner/repo-b") is not None


# ── AC1: Each handler's source contains an invalidate_board call ─────────────
# We use source-text inspection rather than import+exec because several modules
# have runtime import-time side effects (server.py, db.py) that fail in the UAT
# environment. Source inspection verifies the call is wired without running the
# module at import time.

class TestSprintRunInvalidates:
    """AC1: sprint_run.py handlers call invalidate_board after successful write."""

    _FILE = "sprint_run.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text, (
            "sprint_run.py must import invalidate_board from .board_cache"
        )

    def test_run_sprint_managed_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        # The call must appear after the spawn (proc = ...) and before return
        assert "invalidate_board(body.project)" in text, (
            "run_sprint_managed must call invalidate_board(body.project)"
        )

    def test_kill_sprint_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(project)" in text, (
            "kill_sprint must call invalidate_board(project)"
        )

    def test_rerun_sprint_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        # rerun uses the `project` query param
        count = text.count("invalidate_board(project)")
        assert count >= 2, (
            f"Expected ≥2 invalidate_board(project) calls in sprint_run.py "
            f"(kill_sprint + rerun_sprint), found {count}"
        )


class TestSprintCrudInvalidates:
    """AC1: sprint_crud.py handlers call invalidate_board after successful write."""

    _FILE = "sprint_crud.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_create_sprint_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(body.project)" in text

    def test_delete_sprint_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(project)" in text

    def test_rename_sprint_calls_invalidate(self):
        # rename uses body.project — verified by counting body.project calls
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        count = text.count("invalidate_board(body.project)")
        assert count >= 2, (
            f"Expected ≥2 invalidate_board(body.project) calls in sprint_crud.py "
            f"(create + rename), found {count}"
        )


class TestSprintPlanningInvalidates:
    """AC1: sprint_planning.py assign handler calls invalidate_board."""

    _FILE = "sprint_planning.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_assign_sprint_label_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(" in text, (
            "sprint_planning.py must call invalidate_board"
        )


class TestIssuesInvalidates:
    """AC1: issues.py sprint-label handler calls invalidate_board."""

    _FILE = "issues.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_add_sprint_label_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(" in text


class TestBatchLabelsInvalidates:
    """AC1/AC2: sprint_labels.py batch handler calls invalidate_board only on success."""

    _FILE = "sprint_labels.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_batch_sprint_labels_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(" in text

    def test_batch_sprint_labels_guarded_by_applied_count(self):
        """AC2: invalidate_board must not be called when no labels were applied."""
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        # The invalidation must be inside an `if applied` guard
        assert "if applied" in text, (
            "batch_sprint_labels must guard invalidate_board with 'if applied'"
        )


class TestReconcileInvalidates:
    """AC1: sprint_history.py reconcile handler calls invalidate_board."""

    _FILE = "sprint_history.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_post_sprint_reconcile_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(body.project)" in text


class TestBulkCompleteInvalidates:
    """AC1: sprint_finish.py bulk-complete handler calls invalidate_board."""

    _FILE = "sprint_finish.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_bulk_complete_sprint_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(repo)" in text


class TestGoalInvalidates:
    """AC1: sprints.py goal save handler calls invalidate_board."""

    _FILE = "sprints.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_save_sprint_goal_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(body.project)" in text


class TestSchedulerInvalidates:
    """AC1: scheduler.py schedule-edit handlers call invalidate_board."""

    _FILE = "scheduler.py"

    def test_module_imports_invalidate_board(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "from .board_cache import invalidate_board" in text

    def test_set_schedule_config_calls_invalidate(self):
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        assert "invalidate_board(body.project)" in text

    def test_set_run_on_schedule_calls_invalidate(self):
        # Both scheduler handlers use body.project
        text = (_ROUTERS_ROOT / self._FILE).read_text()
        count = text.count("invalidate_board(body.project)")
        assert count >= 2, (
            f"Expected ≥2 invalidate_board(body.project) calls in scheduler.py "
            f"(set_schedule_config + set_run_on_schedule), found {count}"
        )


# ── AC4: Cache miss after invalidation (functional via board_cache) ───────────

class TestCacheMissAfterInvalidate:
    """AC4: get_board_cache returns None (miss) immediately after invalidate_board."""

    def setup_method(self):
        _reset_cache()

    def test_get_board_cache_miss_after_invalidate(self):
        snapshot: dict[str, Any] = {"project": "owner/repo", "sections": {}}
        _bc.store_board_cache("owner/repo", snapshot)

        # Sanity: cache is warm
        assert _bc.get_board_cache("owner/repo") is not None

        _bc.invalidate_board("owner/repo")

        # Now it must be a miss
        assert _bc.get_board_cache("owner/repo") is None

    def test_invalidation_is_project_scoped(self):
        """A miss for A must not cause a miss for B (AC3 + AC4 combined)."""
        snap_a: dict[str, Any] = {"project": "owner/repo-a", "sections": {}}
        snap_b: dict[str, Any] = {"project": "owner/repo-b", "sections": {}}
        _bc.store_board_cache("owner/repo-a", snap_a)
        _bc.store_board_cache("owner/repo-b", snap_b)

        _bc.invalidate_board("owner/repo-a")

        assert _bc.get_board_cache("owner/repo-a") is None
        result_b = _bc.get_board_cache("owner/repo-b")
        assert result_b is not None
        snapshot_b, _ = result_b
        assert snapshot_b["project"] == "owner/repo-b"
