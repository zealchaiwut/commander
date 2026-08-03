"""Tests for issue #2048 — DB is authoritative for sprint immediate_parent lineage.

AC1: _immediate_parent_branch prefers DB immediate_parent over plan.json parent.
AC2: _sprint_merge_parent_label prefers DB immediate_parent over plan.json parent.
AC3: Fallback to base sprint warns loudly when immediate_parent is NULL in both DB and plan.json.
AC4: 3-deep rerun chain (sprint-N, N.1, N.2) resolves correctly via DB — N.2 → sprint-N.1, not sprint-N.
AC5: _backfill_immediate_parent_labels populates DB from plan.json and reports the count before writing.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR),
           str(_REPO_ROOT / "services" / "sprint_manager")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db  # noqa: E402
import sprint_manager as sm  # noqa: E402
import startup  # noqa: E402


# ── DB isolation fixture ──────────────────────────────────────────────────────

@pytest.fixture()
def isolated_db(tmp_path):
    """Point db.DB_PATH at a fresh temp DB; restore after test."""
    db_file = tmp_path / "test_2048.db"
    original = db.DB_PATH
    db.DB_PATH = db_file  # Path object — _startup_integrity_check calls .exists()
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.commit()
    yield tmp_path
    db.DB_PATH = original


def _insert_sprint(label: str, project: str, immediate_parent: str | None = None) -> None:
    with db.get_conn() as conn:
        db._create_sprint_lifecycle_tables(conn)
        conn.execute(
            """
            INSERT INTO sprints (label, project, state, created_at, immediate_parent)
            VALUES (?, ?, 'completed', '2026-01-01T00:00:00Z', ?)
            ON CONFLICT(label, project) DO UPDATE SET
                immediate_parent = excluded.immediate_parent
            """,
            (label, project, immediate_parent),
        )
        conn.commit()


def _make_cfg(sprints_dir: Path, worktree_coder: Path, repo_name: str = "owner/repo") -> object:
    return SimpleNamespace(
        sprints_dir=sprints_dir,
        worktree_coder=worktree_coder,
        worktree_tester=worktree_coder,
        tester_app_subdir="",
        repo_name=repo_name,
        api_url="http://localhost:8000",
        logs_dir=worktree_coder,
        worktree_tester_app=worktree_coder,
        max_coder_slots=None,
        max_tester_slots=None,
    )


# ── AC1: _immediate_parent_branch prefers DB over plan.json ──────────────────

class TestImmediateParentBranchPrefersDB:
    """AC1: when DB has immediate_parent, it is used even if plan.json says something different."""

    def test_db_value_preferred_over_plan_json(self, isolated_db):
        """DB immediate_parent wins over plan.json parent."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        # DB says sprint-72.2 → sprint-72.1
        _insert_sprint("sprint-72.2", "owner/repo", immediate_parent="sprint-72.1")
        # plan.json says sprint-72.0 (intentionally wrong — should be ignored)
        (sprints_dir / "sprint-72.2-plan.json").write_text(
            json.dumps({"parent": "sprint-72.0", "label": "sprint-72.2"})
        )
        cfg = _make_cfg(sprints_dir, isolated_db)
        result = sm._immediate_parent_branch("sprint-72.2", cfg=cfg)
        assert result == "sprint/sprint-72.1", (
            f"Expected DB value 'sprint/sprint-72.1' but got {result!r}. "
            "DB immediate_parent must take precedence over plan.json parent."
        )

    def test_db_value_used_when_plan_json_missing(self, isolated_db):
        """DB immediate_parent used even when no plan.json exists."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        _insert_sprint("sprint-5.2", "owner/repo", immediate_parent="sprint-5.1")
        cfg = _make_cfg(sprints_dir, isolated_db)
        result = sm._immediate_parent_branch("sprint-5.2", cfg=cfg)
        assert result == "sprint/sprint-5.1"

    def test_plan_json_used_when_db_has_no_row(self, isolated_db):
        """Falls back to plan.json when DB has no row for the sprint."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        (sprints_dir / "sprint-10.3-plan.json").write_text(
            json.dumps({"parent": "sprint-10.2", "label": "sprint-10.3"})
        )
        cfg = _make_cfg(sprints_dir, isolated_db)
        result = sm._immediate_parent_branch("sprint-10.3", cfg=cfg)
        assert result == "sprint/sprint-10.2"

    def test_plan_json_used_when_db_immediate_parent_null(self, isolated_db):
        """Falls back to plan.json when DB row exists but immediate_parent is NULL."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        # DB row with NULL immediate_parent
        _insert_sprint("sprint-20.1", "owner/repo", immediate_parent=None)
        (sprints_dir / "sprint-20.1-plan.json").write_text(
            json.dumps({"parent": "sprint-20", "label": "sprint-20.1"})
        )
        cfg = _make_cfg(sprints_dir, isolated_db)
        result = sm._immediate_parent_branch("sprint-20.1", cfg=cfg)
        assert result == "sprint/sprint-20"


# ── AC2: _sprint_merge_parent_label prefers DB over plan.json ────────────────

class TestSprintMergeParentLabelPrefersDB:
    """AC2: startup._sprint_merge_parent_label reads DB immediate_parent first."""

    def test_db_value_preferred_over_plan_json(self, isolated_db, tmp_path):
        """DB immediate_parent wins in _sprint_merge_parent_label."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        # DB: sprint-72.2 → sprint-72.1
        _insert_sprint("sprint-72.2", "owner/repo", immediate_parent="sprint-72.1")
        # plan.json: sprint-72.0 (wrong — should be ignored)
        (sprints_dir / "sprint-72.2-plan.json").write_text(
            json.dumps({"parent": "sprint-72.0"})
        )
        result = startup._sprint_merge_parent_label(project_root, "sprint-72.2", project="owner/repo")
        assert result == "sprint-72.1", (
            f"Expected DB value 'sprint-72.1' but got {result!r}. "
            "DB immediate_parent must take precedence over plan.json parent."
        )

    def test_plan_json_used_when_db_immediate_parent_null(self, isolated_db, tmp_path):
        """Falls back to plan.json when DB immediate_parent is NULL."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        sprints_dir = project_root / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True)
        _insert_sprint("sprint-5.3", "owner/repo", immediate_parent=None)
        (sprints_dir / "sprint-5.3-plan.json").write_text(
            json.dumps({"parent": "sprint-5.2"})
        )
        result = startup._sprint_merge_parent_label(project_root, "sprint-5.3", project="owner/repo")
        assert result == "sprint-5.2"

    def test_base_sprint_returns_itself(self, isolated_db, tmp_path):
        """Non-child sprint (sprint-5) returns itself as base."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        result = startup._sprint_merge_parent_label(project_root, "sprint-5", project="owner/repo")
        assert result == "sprint-5"


# ── AC3: Loud warning on missing immediate parent ─────────────────────────────

class TestLoudFallbackWarning:
    """AC3: when immediate_parent is NULL in DB and plan.json, warn loudly."""

    def test_immediate_parent_branch_warns_on_missing(self, isolated_db, capsys):
        """_immediate_parent_branch emits a structured_log.warn and stdout message."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        # DB has NULL immediate_parent; no plan.json
        _insert_sprint("sprint-99.2", "owner/repo", immediate_parent=None)
        cfg = _make_cfg(sprints_dir, isolated_db)

        with patch.object(sm, "structured_log") as mock_log:
            result = sm._immediate_parent_branch("sprint-99.2", cfg=cfg)

        # Return value falls back to base
        assert result == "sprint/sprint-99"
        # structured_log.warn must be called
        assert mock_log.warn.called, "structured_log.warn must be called on missing immediate parent"
        warn_call = mock_log.warn.call_args_list[0]
        event_key = warn_call[0][0]
        assert event_key == "immediate_parent_missing", (
            f"Expected event key 'immediate_parent_missing' but got {event_key!r}"
        )
        # stdout also warns
        out = capsys.readouterr().out
        assert "WARNING" in out or "warning" in out.lower() or "⚠" in out, (
            "stdout must contain a visible warning about missing immediate parent"
        )
        assert "sprint-99.2" in out, "Warning must name the affected sprint label"

    def test_sprint_merge_parent_label_warns_on_missing(self, isolated_db, tmp_path, caplog):
        """_sprint_merge_parent_label emits logging.warning on missing immediate parent."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        _insert_sprint("sprint-88.3", "owner/repo", immediate_parent=None)
        with caplog.at_level(logging.WARNING):
            result = startup._sprint_merge_parent_label(
                project_root, "sprint-88.3", project="owner/repo"
            )
        assert result == "sprint-88"
        assert any("sprint-88.3" in r.message for r in caplog.records), (
            "logging.warning must name the sprint label"
        )
        assert any("immediate" in r.message.lower() for r in caplog.records), (
            "logging.warning must mention 'immediate' parent"
        )

    def test_no_warning_when_db_has_parent(self, isolated_db):
        """No warning is emitted when DB has a valid immediate_parent."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        _insert_sprint("sprint-50.2", "owner/repo", immediate_parent="sprint-50.1")
        cfg = _make_cfg(sprints_dir, isolated_db)

        with patch.object(sm, "structured_log") as mock_log:
            result = sm._immediate_parent_branch("sprint-50.2", cfg=cfg)

        assert result == "sprint/sprint-50.1"
        assert not mock_log.warn.called, "No warning expected when DB has valid immediate_parent"


# ── AC4: 3-deep rerun chain resolves correctly via DB ────────────────────────

class TestThreeDeepRerunChain:
    """AC4: sprint-N → N.1 → N.2 → N.3; each resolves to its immediate predecessor."""

    def test_three_deep_chain_resolves_immediate_parent(self, isolated_db):
        """sprint-72.3 → sprint-72.2, sprint-72.2 → sprint-72.1 (not sprint-72)."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        # Set up full 3-deep chain in DB
        _insert_sprint("sprint-72.1", "owner/repo", immediate_parent="sprint-72")
        _insert_sprint("sprint-72.2", "owner/repo", immediate_parent="sprint-72.1")
        _insert_sprint("sprint-72.3", "owner/repo", immediate_parent="sprint-72.2")

        cfg = _make_cfg(sprints_dir, isolated_db)

        r1 = sm._immediate_parent_branch("sprint-72.1", cfg=cfg)
        r2 = sm._immediate_parent_branch("sprint-72.2", cfg=cfg)
        r3 = sm._immediate_parent_branch("sprint-72.3", cfg=cfg)

        assert r1 == "sprint/sprint-72", f"72.1 → 72, got {r1!r}"
        assert r2 == "sprint/sprint-72.1", (
            f"72.2 must point to immediate predecessor sprint-72.1, got {r2!r}. "
            "Without the fix, this would have returned sprint/sprint-72 (wrong base)."
        )
        assert r3 == "sprint/sprint-72.2", (
            f"72.3 must point to immediate predecessor sprint-72.2, got {r3!r}. "
            "Skipping intermediate branches is a silent lost-commits path."
        )

    def test_merge_parent_label_three_deep_chain(self, isolated_db, tmp_path):
        """_sprint_merge_parent_label resolves chain correctly."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        _insert_sprint("sprint-80.1", "owner/repo", immediate_parent="sprint-80")
        _insert_sprint("sprint-80.2", "owner/repo", immediate_parent="sprint-80.1")
        _insert_sprint("sprint-80.3", "owner/repo", immediate_parent="sprint-80.2")

        r1 = startup._sprint_merge_parent_label(project_root, "sprint-80.1", project="owner/repo")
        r2 = startup._sprint_merge_parent_label(project_root, "sprint-80.2", project="owner/repo")
        r3 = startup._sprint_merge_parent_label(project_root, "sprint-80.3", project="owner/repo")

        assert r1 == "sprint-80"
        assert r2 == "sprint-80.1", (
            f"sprint-80.2's merge target must be sprint-80.1, got {r2!r}. "
            "Targeting sprint-80 would bypass intermediate rerun work."
        )
        assert r3 == "sprint-80.2", (
            f"sprint-80.3's merge target must be sprint-80.2, got {r3!r}."
        )

    def test_base_sprint_is_not_a_child(self, isolated_db):
        """Base sprint-72 (no dot) returns itself, not a parent."""
        sprints_dir = isolated_db / "sprints"
        sprints_dir.mkdir()
        cfg = _make_cfg(sprints_dir, isolated_db)
        result = sm._immediate_parent_branch("sprint-72", cfg=cfg)
        # Base sprint falls through entirely (no child detection matches sprint-N without .M)
        # _base_sprint_branch("sprint-72") returns "sprint/sprint-72"
        # This is expected behavior — base sprints don't have immediate parents
        assert result == "sprint/sprint-72"


# ── AC5: Backfill populates immediate_parent from plan.json ──────────────────

class TestBackfillImmediateParent:
    """AC5: _backfill_immediate_parent_labels reads plan.json and fills NULL rows."""

    def _make_sprints_dir(self, tmp_path: Path) -> Path:
        d = tmp_path / "sprints"
        d.mkdir()
        return d

    def test_backfill_populates_from_plan_json(self, isolated_db):
        """Child sprints with NULL immediate_parent get populated from plan.json."""
        sprints_dir = self._make_sprints_dir(isolated_db)
        # Write plan.json files
        (sprints_dir / "sprint-72.1-plan.json").write_text(
            json.dumps({"parent": "sprint-72"})
        )
        (sprints_dir / "sprint-72.2-plan.json").write_text(
            json.dumps({"parent": "sprint-72.1"})
        )
        # Insert rows with NULL immediate_parent
        _insert_sprint("sprint-72.1", "myproject", immediate_parent=None)
        _insert_sprint("sprint-72.2", "myproject", immediate_parent=None)

        with db.get_conn() as conn:
            db._backfill_immediate_parent_labels(
                conn,
                projects_base=isolated_db.parent,
                projects_file=None,
                _sprints_dir_override=sprints_dir,
            )
            conn.commit()

        r1 = db.get_sprint("sprint-72.1")
        r2 = db.get_sprint("sprint-72.2")
        assert r1 and r1.get("immediate_parent") == "sprint-72", (
            f"Expected 'sprint-72', got {r1.get('immediate_parent') if r1 else None!r}"
        )
        assert r2 and r2.get("immediate_parent") == "sprint-72.1", (
            f"Expected 'sprint-72.1', got {r2.get('immediate_parent') if r2 else None!r}"
        )

    def test_backfill_is_idempotent(self, isolated_db):
        """Running backfill twice does not change already-populated rows."""
        sprints_dir = self._make_sprints_dir(isolated_db)
        (sprints_dir / "sprint-5.1-plan.json").write_text(
            json.dumps({"parent": "sprint-5"})
        )
        _insert_sprint("sprint-5.1", "myproject", immediate_parent=None)

        with db.get_conn() as conn:
            db._backfill_immediate_parent_labels(
                conn, _sprints_dir_override=sprints_dir,
            )
            conn.commit()

        r = db.get_sprint("sprint-5.1")
        assert r and r.get("immediate_parent") == "sprint-5"

        # Run again — should not change
        with db.get_conn() as conn:
            db._backfill_immediate_parent_labels(
                conn, _sprints_dir_override=sprints_dir,
            )
            conn.commit()

        r2 = db.get_sprint("sprint-5.1")
        assert r2 and r2.get("immediate_parent") == "sprint-5"

    def test_backfill_logs_count_before_writing(self, isolated_db, caplog):
        """AC5: backfill reports how many rows will change before applying."""
        sprints_dir = self._make_sprints_dir(isolated_db)
        (sprints_dir / "sprint-30.1-plan.json").write_text(
            json.dumps({"parent": "sprint-30"})
        )
        (sprints_dir / "sprint-30.2-plan.json").write_text(
            json.dumps({"parent": "sprint-30.1"})
        )
        _insert_sprint("sprint-30.1", "myproject", immediate_parent=None)
        _insert_sprint("sprint-30.2", "myproject", immediate_parent=None)

        with caplog.at_level(logging.INFO):
            with db.get_conn() as conn:
                db._backfill_immediate_parent_labels(
                    conn, _sprints_dir_override=sprints_dir,
                )
                conn.commit()

        # Must log the count of rows with NULL immediate_parent before writing
        count_logs = [r for r in caplog.records if "2" in r.message and "immediate_parent" in r.message]
        assert count_logs, (
            "Backfill must log the count of affected rows before applying changes (AC5). "
            f"Log records found: {[r.message for r in caplog.records]}"
        )

    def test_backfill_warns_when_no_plan_json_found(self, isolated_db, caplog):
        """Unresolvable child sprint gets a warning, not a crash."""
        _insert_sprint("sprint-99.1", "unknown-project", immediate_parent=None)
        sprints_dir = self._make_sprints_dir(isolated_db)

        with caplog.at_level(logging.WARNING):
            with db.get_conn() as conn:
                db._backfill_immediate_parent_labels(
                    conn, _sprints_dir_override=sprints_dir,
                )
                conn.commit()

        r = db.get_sprint("sprint-99.1")
        assert r and r.get("immediate_parent") is None, "Unresolvable row stays NULL"
        assert any("sprint-99.1" in rec.message for rec in caplog.records), (
            "Warning must name the unresolvable sprint label"
        )

    def test_backfill_does_not_overwrite_existing(self, isolated_db):
        """Rows already having immediate_parent are left untouched."""
        sprints_dir = self._make_sprints_dir(isolated_db)
        (sprints_dir / "sprint-40.1-plan.json").write_text(
            json.dumps({"parent": "sprint-40-WRONG"})
        )
        # DB already has the correct value
        _insert_sprint("sprint-40.1", "myproject", immediate_parent="sprint-40")

        with db.get_conn() as conn:
            db._backfill_immediate_parent_labels(
                conn, _sprints_dir_override=sprints_dir,
            )
            conn.commit()

        r = db.get_sprint("sprint-40.1")
        assert r and r.get("immediate_parent") == "sprint-40", (
            "Existing immediate_parent must not be overwritten by backfill"
        )
