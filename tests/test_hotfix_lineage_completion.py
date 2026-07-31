"""Hotfix tests — lineage parent persistence + auto-complete on merge-to-develop.

Covers two durable fixes from the 2026-06-19 lineage hotfix:

B1  Child sprints (``sprint-N.M``) persist parent_label = base ``sprint-N`` so
    children_of()'s DB-primary lookup resolves the whole lineage instead of
    silently dropping descendants. Verified for both the live writer path and the
    one-time backfill of pre-existing NULL rows.

B2  A superseded ancestor stuck in needs_rework (its own tickets failed, which is
    why a rerun child was spawned) is auto-completed by the reconciler once its
    branch has fully merged into develop — but NOT while any of its work is still
    missing from develop (the half-merged case).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db_module  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path):
    db_file = tmp_path / "test_hotfix.db"
    original = _db_module.DB_PATH
    _db_module.DB_PATH = db_file
    _db_module.init_db()
    yield _db_module
    _db_module.DB_PATH = original


# ── B1: parent_label persistence ─────────────────────────────────────────────

def test_b1_child_start_persists_base_parent_label(fresh_db):
    """A child-sprint running write derives parent_label = base when absent."""
    fresh_db.record_sprint_start("sprint-68.2", project="zealchaiwut/perf-coach")
    row = fresh_db.get_sprint("sprint-68.2")
    assert row["parent_label"] == "sprint-68"


def test_b1_base_sprint_has_no_parent_label(fresh_db):
    fresh_db.record_sprint_start("sprint-68", project="zealchaiwut/perf-coach")
    row = fresh_db.get_sprint("sprint-68")
    assert not row["parent_label"]


def test_b1_explicit_parent_label_is_respected(fresh_db):
    """An explicitly supplied parent_label is never overwritten by the derivation."""
    fresh_db.record_sprint_start(
        "sprint-68.2", project="zealchaiwut/perf-coach", parent_label="sprint-68",
    )
    assert fresh_db.get_sprint("sprint-68.2")["parent_label"] == "sprint-68"


def test_b1_children_of_resolves_full_lineage_via_db(fresh_db):
    """children_of(base) returns every sub-sprint once parent_label is persisted."""
    fresh_db.record_sprint_start("sprint-68", project="zealchaiwut/perf-coach")
    fresh_db.record_sprint_start("sprint-68.1", project="zealchaiwut/perf-coach")
    fresh_db.record_sprint_start("sprint-68.2", project="zealchaiwut/perf-coach")
    kids = {r["label"] for r in fresh_db.get_sprint_children("sprint-68")}
    assert kids == {"sprint-68.1", "sprint-68.2"}


def test_b1_backfill_heals_legacy_null_rows(fresh_db):
    """The startup backfill sets parent_label=base for NULL child rows."""
    with fresh_db.get_conn() as conn:
        for label in ("sprint-66", "sprint-66.1", "sprint-66.2"):
            conn.execute(
                "INSERT INTO sprints(label, project, state, parent_label) "
                "VALUES (?, 'zealchaiwut/perf-coach', 'needs_rework', NULL)",
                (label,),
            )
        conn.commit()
        fresh_db._backfill_child_parent_labels(conn)
        conn.commit()
    assert fresh_db.get_sprint("sprint-66.1")["parent_label"] == "sprint-66"
    assert fresh_db.get_sprint("sprint-66.2")["parent_label"] == "sprint-66"
    assert not fresh_db.get_sprint("sprint-66")["parent_label"]


# ── B2: lineage auto-complete on merge-to-develop ────────────────────────────

@pytest.fixture
def reconcile(fresh_db, monkeypatch):
    """Import the reconcile service against the isolated DB."""
    # Append (don't prepend) the routers dir so importing the reconcile module
    # directly does not shadow apps/dashboard modules — e.g. `import projects`
    # inside startup must resolve to the real projects module, not routers/projects.py.
    sys.path.append(str(DASHBOARD_DIR / "routers"))
    import sprint_reconcile_service as rc
    monkeypatch.setattr(rc, "_db", lambda: fresh_db)
    return rc


def _seed_lineage(db):
    db.record_sprint_start("sprint-68", project="zealchaiwut/perf-coach")
    db.record_sprint_start("sprint-68.1", project="zealchaiwut/perf-coach")
    db.record_sprint_start("sprint-68.2", project="zealchaiwut/perf-coach")


def test_b2_does_not_auto_complete_ancestor_when_branch_in_develop(fresh_db, reconcile, monkeypatch):
    """needs_rework base whose branch is in develop must not flip to completed."""
    _seed_lineage(fresh_db)
    import server as srv
    monkeypatch.setattr(srv, "_branch_has_unmerged_commits", lambda *a, **k: False)
    monkeypatch.setattr(srv, "_has_rework_tickets", lambda *a, **k: True)
    fresh_db.record_sprint_needs_rework("sprint-68", end_reason="ticket-failures")
    patch = reconcile._github_reconcile_row(
        "sprint-68", "zealchaiwut/perf-coach",
        fresh_db.get_sprint("sprint-68"),
    )
    assert patch is None or patch.get("state") != "completed"
    assert fresh_db.get_sprint("sprint-68")["state"] == "needs_rework"


def test_b2_bulk_complete_refuses_half_merged_chain(fresh_db, monkeypatch):
    """Bulk complete's own chain scan refuses when any hop is unmerged (issue #1694).

    _lineage_fully_in_develop (a single-label, sweep-only helper with zero
    production callers) was deleted — the real guard bulk-complete relies on
    is startup._sprint_merge_chain_pending, which checks every hop of the
    child->parent->develop chain, not just one label against develop.
    """
    sys.path.append(str(DASHBOARD_DIR))
    import startup

    _seed_lineage(fresh_db)
    monkeypatch.setattr(startup, "children_of", lambda *a, **k: ["sprint-68.1", "sprint-68.2"])
    monkeypatch.setattr(startup, "_branch_has_unmerged_commits", lambda repo, head, base: base == "develop")
    pending = startup._sprint_merge_chain_pending(Path("/tmp"), "zealchaiwut/perf-coach", "sprint-68")
    assert any(p.endswith("→ develop") for p in pending)


def test_b2_needs_rework_to_completed_edge_still_requires_reconcile_actor(fresh_db):
    """Whatever calls the completing write, only actor='reconcile' may use the B2 edge."""
    fresh_db.record_sprint_start("sprint-70", project="zealchaiwut/perf-coach")
    fresh_db.record_sprint_needs_rework("sprint-70", end_reason="ticket-failures")
    rejected = fresh_db.transition_sprint_state(
        "sprint-70", "completed", actor="manager", end_reason="lineage-merged",
    )
    assert rejected.accepted is False
    assert fresh_db.get_sprint("sprint-70")["state"] == "needs_rework"


def test_b2_needs_rework_to_completed_edge_is_legal_for_reconcile(fresh_db):
    """transition_sprint_state must accept needs_rework→completed by reconcile."""
    fresh_db.record_sprint_start("sprint-68", project="zealchaiwut/perf-coach")
    fresh_db.record_sprint_needs_rework("sprint-68", end_reason="ticket-failures")
    res = fresh_db.transition_sprint_state(
        "sprint-68", "completed", actor="reconcile", end_reason="lineage-merged",
    )
    assert res.accepted is True
    assert fresh_db.get_sprint("sprint-68")["state"] == "completed"
