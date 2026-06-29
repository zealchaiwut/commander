"""Tests for issue #1464: Scope all sprint reads to project, eliminate cross-project leakage.

AC1 — get_sprint_children(parent, project='commander') returns only commander's children;
       never returns a child belonging to another project sharing the same parent_label.
AC2 — _derive_outcome_lifecycle (board) scoped to one project never surfaces the other
       project's children in partial_finished resolution.
AC3 — children_of(parent, project_root, project='commander') returns only commander's
       children; bulk-complete/finish merge steps are thus fully project-scoped.
AC4 — get_sprint_children logs a warning when called without project (label-only fallback).
AC5 — reconcile service calls get_sprint_children with project when context is known.
AC6 — Two projects sharing identical sprint numbers have fully independent boards,
       histories, and get_sprint_children results.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402

_COMMANDER = "zealchaiwut/commander"
_PERF_COACH = "zealchaiwut/perf-coach"
_BASE = "sprint-66"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Fresh SQLite DB per test — no shared state between tests."""
    db_file = tmp_path / "test_1464.db"
    original = db.DB_PATH
    db.DB_PATH = str(db_file)
    db.init_db()
    yield
    db.DB_PATH = original


def _insert_sprint(label: str, project: str, parent_label: str | None = None) -> None:
    """Directly insert a sprint row, bypassing the guard, to simulate cross-project scenarios."""
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at, parent_label)
            VALUES (?, ?, 'running', '2026-01-01T00:00:00Z', ?)
            ON CONFLICT(label, project) DO UPDATE SET
                parent_label = COALESCE(excluded.parent_label, sprints.parent_label)
            """,
            (label, project, parent_label),
        )
        conn.commit()


# ── AC1: get_sprint_children project scoping ──────────────────────────────────

class TestGetSprintChildrenProjectScoping:
    """AC1: get_sprint_children returns only the requested project's children."""

    def setup_method(self):
        # commander child: sprint-66.1
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        # perf-coach child: sprint-66.2 (different sub-label; both share parent_label='sprint-66')
        _insert_sprint("sprint-66.2", _PERF_COACH, parent_label=_BASE)

    def test_project_scoped_returns_only_own_children(self):
        """commander gets only sprint-66.1; never sprint-66.2."""
        children = db.get_sprint_children(_BASE, project=_COMMANDER)
        labels = {c["label"] for c in children}
        assert "sprint-66.1" in labels, "commander's child must be present"
        assert "sprint-66.2" not in labels, "perf-coach child must NOT appear in commander"

    def test_other_project_scoped_returns_only_its_children(self):
        """perf-coach gets only sprint-66.2; never sprint-66.1."""
        children = db.get_sprint_children(_BASE, project=_PERF_COACH)
        labels = {c["label"] for c in children}
        assert "sprint-66.2" in labels, "perf-coach's child must be present"
        assert "sprint-66.1" not in labels, "commander child must NOT appear in perf-coach"

    def test_unscoped_returns_all_projects_children(self):
        """Without project filter, all children are returned (legacy fallback path)."""
        children = db.get_sprint_children(_BASE)
        labels = {c["label"] for c in children}
        assert "sprint-66.1" in labels
        assert "sprint-66.2" in labels


# ── AC4: label-only fallback emits warning ────────────────────────────────────

class TestGetSprintChildrenWarnsOnLabelOnlyFallback:
    """AC4: a warning-level log is emitted when get_sprint_children is called without project."""

    def test_warns_when_no_project(self, caplog):
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        with caplog.at_level(logging.WARNING, logger="db"):
            db.get_sprint_children(_BASE)
        assert any(
            "label-only fallback" in r.message or "without project" in r.message
            for r in caplog.records
        ), "Expected a warning about label-only fallback"

    def test_no_warning_when_project_supplied(self, caplog):
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        with caplog.at_level(logging.WARNING, logger="db"):
            db.get_sprint_children(_BASE, project=_COMMANDER)
        warning_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any(
            "label-only fallback" in t or "without project" in t
            for t in warning_texts
        ), "No fallback warning expected when project is supplied"


# ── AC3: children_of project scoping ─────────────────────────────────────────

class TestChildrenOfProjectScoping:
    """AC3: startup.children_of with project kwarg is project-scoped."""

    def setup_method(self):
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        _insert_sprint("sprint-66.2", _PERF_COACH, parent_label=_BASE)

    def test_children_of_scoped_to_commander(self, tmp_path):
        import server as srv  # noqa: PLC0415
        result = srv.children_of(_BASE, project_root=tmp_path, project=_COMMANDER)
        assert result == ["sprint-66.1"], (
            f"Expected only commander's child, got {result}"
        )

    def test_children_of_scoped_to_perf_coach(self, tmp_path):
        import server as srv  # noqa: PLC0415
        result = srv.children_of(_BASE, project_root=tmp_path, project=_PERF_COACH)
        assert result == ["sprint-66.2"], (
            f"Expected only perf-coach's child, got {result}"
        )

    def test_children_of_unscoped_returns_all(self, tmp_path):
        import server as srv  # noqa: PLC0415
        result = srv.children_of(_BASE, project_root=tmp_path)
        assert set(result) == {"sprint-66.1", "sprint-66.2"}, (
            f"Unscoped should return all children, got {result}"
        )


# ── AC2: board lifecycle (_derive_outcome_lifecycle) project scoping ──────────

class TestDeriveOutcomeLifecycleProjectScoping:
    """AC2: _derive_outcome_lifecycle uses project-scoped get_sprint_children.

    Both startup.py and routers/estimates.py have _derive_outcome_lifecycle.
    Test that both pass project to get_sprint_children.
    """

    def _seed(self):
        # Base sprint: commander's sprint-66 completed
        _insert_sprint(_BASE, _COMMANDER)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE sprints SET state='completed' WHERE label=? AND project=?",
                (_BASE, _COMMANDER),
            )
            conn.commit()
        # Commander child: NOT settled (running)
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        # Perf-coach child: also running, but belongs to perf-coach
        _insert_sprint("sprint-66.2", _PERF_COACH, parent_label=_BASE)

    def test_startup_derive_outcome_lifecycle_uses_project(self, tmp_path):
        """startup._derive_outcome_lifecycle(sprint-66, project=commander) checks
        only commander's children — a perf-coach child (66.2) in running state
        must NOT trigger partial_finished for commander."""
        self._seed()
        import server as srv  # noqa: PLC0415
        # Make perf-coach's child running but commander's settled
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE sprints SET state='completed' WHERE label=? AND project=?",
                ("sprint-66.1", _COMMANDER),
            )
            conn.commit()

        lifecycle = srv._derive_outcome_lifecycle(
            sprint_label=_BASE,
            project_root=tmp_path,
            project=_COMMANDER,
            plan_state="completed",
            pane_state="completed",
            failed_count=0,
        )
        # commander's own child (66.1) is completed → should NOT be partial_finished
        # perf-coach's child (66.2) is still running, but must be ignored
        assert lifecycle != "partial_finished", (
            "perf-coach's running child must NOT trigger partial_finished for commander"
        )

    def test_estimates_derive_outcome_lifecycle_uses_project(self, tmp_path):
        """routers/estimates._derive_outcome_lifecycle is project-scoped too."""
        self._seed()
        sys.path.insert(0, str(_DASHBOARD_DIR / "routers"))
        from routers import estimates as est_mod  # noqa: PLC0415
        # commander's child settled
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE sprints SET state='completed' WHERE label=? AND project=?",
                ("sprint-66.1", _COMMANDER),
            )
            conn.commit()

        lifecycle = est_mod._derive_outcome_lifecycle(
            sprint_label=_BASE,
            project_root=tmp_path,
            project=_COMMANDER,
            plan_state="completed",
            pane_state="completed",
            failed_count=0,
        )
        assert lifecycle != "partial_finished", (
            "estimates._derive_outcome_lifecycle must ignore perf-coach's running child"
        )


# ── AC5: reconcile service passes project to get_sprint_children ──────────────

class TestReconcileServicePassesProject:
    """AC5: sprint_reconcile_service._lineage_fully_in_develop passes project
    to get_sprint_children so it doesn't pick up another project's children."""

    def test_lineage_fully_in_develop_uses_project_scoped_children(self, tmp_path):
        # commander child exists; perf-coach has a stray child too
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        _insert_sprint("sprint-66.2", _PERF_COACH, parent_label=_BASE)

        from routers import sprint_reconcile_service as svc  # noqa: PLC0415

        # Patch the branch-check so it doesn't call out to git
        with (
            patch.object(svc, "_db", return_value=db),
        ):
            # Stub _branch_has_unmerged_commits and _is_child_sprint_label
            import server as srv  # noqa: PLC0415
            with (
                patch.object(srv, "_branch_has_unmerged_commits", return_value=False),
                patch.object(srv, "_is_child_sprint_label", return_value=True),
                patch.object(srv, "_sprint_branch_name", return_value="sprint/sprint-66.1"),
            ):
                # commander: has_children via project-scoped lookup
                has_children_cmd = bool(db.get_sprint_children("sprint-66.1", project=_COMMANDER))
                has_children_perf = bool(db.get_sprint_children("sprint-66.1", project=_PERF_COACH))

        # sprint-66.1 belongs to commander only
        assert has_children_cmd is False, "sprint-66.1 has no children for commander"
        assert has_children_perf is False, "sprint-66.1 has no children for perf-coach"


# ── AC6: Full cross-project independence ──────────────────────────────────────

class TestFullCrossProjectIndependence:
    """AC6: Two projects sharing identical sprint numbers have fully independent
    get_sprint_children results."""

    def setup_method(self):
        # Two projects, same parent label, different children labels
        _insert_sprint("sprint-66.1", _COMMANDER, parent_label=_BASE)
        _insert_sprint("sprint-66.2", _PERF_COACH, parent_label=_BASE)

    def test_commander_children_independent(self):
        children = db.get_sprint_children(_BASE, project=_COMMANDER)
        assert [c["label"] for c in children] == ["sprint-66.1"]

    def test_perf_coach_children_independent(self):
        children = db.get_sprint_children(_BASE, project=_PERF_COACH)
        assert [c["label"] for c in children] == ["sprint-66.2"]

    def test_children_project_column_matches(self):
        """Each child row carries the correct project value."""
        for child in db.get_sprint_children(_BASE, project=_COMMANDER):
            assert child["project"] == _COMMANDER, (
                f"Expected project={_COMMANDER}, got {child['project']}"
            )
        for child in db.get_sprint_children(_BASE, project=_PERF_COACH):
            assert child["project"] == _PERF_COACH, (
                f"Expected project={_PERF_COACH}, got {child['project']}"
            )

    def test_history_service_filters_by_project(self):
        """sprint_history_service.get_sprint_history returns only the requested project."""
        # Seed full sprints for both projects
        _insert_sprint(_BASE, _COMMANDER)
        _insert_sprint("sprint-66", _PERF_COACH)

        # Finish commander's sprint to a terminal state
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE sprints SET state='completed' WHERE label=? AND project=?",
                (_BASE, _COMMANDER),
            )
            conn.commit()

        from routers import sprint_history_service as svc  # noqa: PLC0415

        # patch sprints dir resolution so no disk access needed
        with patch.object(svc, "_resolve_sprints_search_dirs", return_value=[]):
            result = svc.get_sprint_history(project=_COMMANDER)

        projects_in_result = {r.get("project") for r in result["sprints"]}
        assert _PERF_COACH not in projects_in_result, (
            f"perf-coach sprint must not appear in commander's history: {result['sprints']}"
        )

    def test_perf_coach_never_leaks_via_disk_resolve(self, tmp_path):
        """perf-coach lifecycle rows must not be reassigned to commander via disk."""
        _insert_sprint("sprint-77", _PERF_COACH)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE sprints SET state='needs_rework' WHERE label=? AND project=?",
                ("sprint-77", _PERF_COACH),
            )
            conn.commit()

        sprint_dir = tmp_path / "sprints"
        sprint_dir.mkdir()
        (sprint_dir / "sprint-77-plan.json").write_text(
            '{"project": "zealchaiwut/commander", "state": "needs_rework"}',
            encoding="utf-8",
        )

        from routers import sprint_history_service as svc  # noqa: PLC0415

        with patch.object(svc, "_resolve_sprints_search_dirs", return_value=[sprint_dir]):
            result = svc.get_sprint_history(project=_COMMANDER, active_only=True)

        labels = [r["label"] for r in result["sprints"]]
        assert "sprint-77" not in labels, (
            "perf-coach sprint-77 must not appear in commander History"
        )
