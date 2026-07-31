"""Tests for sweep-driven completion of superseded rerun ancestors.

Bug (hermes-agent sprint-1 lineage): completing the chain tip merged the whole
stacked chain to develop, but ancestors 1/1.1/1.2 stayed needs_rework; the
background reconcile sweep then promoted them to ready_to_merge forever —
advertising a Complete CTA for work already in develop and pinning the lineage
in the active History view.

Fix under test (_github_reconcile_row): with no open rework tickets, a sprint
whose lineage has a strictly-later COMPLETED member AND whose lineage BASE
branch (sprint/<base>) is a merged PR head is completed with
end_reason='superseded', preserving its original ended_at. Everything else
keeps the previous behavior (needs_rework → ready_to_merge promotion only).
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_DASHBOARD_ROOT))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

_PROJ = "owner/hermes-agent"
_OTHER_PROJ = "owner/other-repo"
_ORIG_ENDED = "2026-07-14T16:17:49+00:00"


@pytest.fixture
def fresh_db(tmp_path):
    # Resolve `db` at fixture time, not import time: earlier tests in a full
    # run may replace sys.modules["db"], and sprint_reconcile_service._db()
    # always reads the current instance.
    dbm = importlib.import_module("db")
    db_file = tmp_path / "test_superseded.db"
    original = dbm.DB_PATH
    dbm.DB_PATH = str(db_file)
    dbm.init_db()
    yield dbm
    dbm.DB_PATH = original


def _seed(db, label, state, end_reason=None, ended_at=None, project=_PROJ):
    """Create a sprints row via the real state machine so parent_label heals."""
    db.record_sprint_start(label, project=project)
    if state == "needs_rework":
        db.record_sprint_needs_rework(label, end_reason=end_reason,
                                      ended_at=ended_at, project=project)
    elif state == "ready_to_merge":
        db.record_sprint_ready_to_merge(label, end_reason=end_reason,
                                        ended_at=ended_at, project=project)
    elif state == "completed":
        db.record_sprint_finish(label, end_reason=end_reason,
                                ended_at=ended_at, project=project)


def _patches(merged_branches=frozenset({"sprint/sprint-1"}), has_rework=False,
             merged_raises=False, emit_mock=None):
    out = []
    if merged_raises:
        out.append(patch("github_client.list_merged_sprint_branches",
                         side_effect=RuntimeError("gh down")))
    else:
        out.append(patch("github_client.list_merged_sprint_branches",
                         return_value=set(merged_branches)))
    for mod_name in ("server", "startup"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "_has_rework_tickets"):
            out.append(patch(f"{mod_name}._has_rework_tickets",
                             return_value=has_rework))
        if emit_mock is not None and hasattr(mod, "_emit_dashboard_event"):
            out.append(patch(f"{mod_name}._emit_dashboard_event", emit_mock))
    return out


def _reconcile(label, project=_PROJ, **patch_kw):
    from routers import sprint_reconcile_service as svc
    patches = _patches(**patch_kw)
    for p in patches:
        p.start()
    try:
        return svc.reconcile_sprint_label(label, project)
    finally:
        for p in patches:
            p.stop()


def _row(db, label, project=_PROJ):
    return db.get_sprint(label, project=project)


def test_ready_to_merge_zombie_heals_to_completed_superseded(fresh_db):
    """The live hermes shape: rtm + ticket-failures zombie with a completed tip."""
    _seed(fresh_db, "sprint-1.2", "ready_to_merge",
          end_reason="ticket-failures", ended_at=_ORIG_ENDED)
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    assert _reconcile("sprint-1.2") is True
    row = _row(fresh_db, "sprint-1.2")
    assert fresh_db.canonical_lifecycle(row["state"]) == "completed"
    assert row["end_reason"] == "superseded"
    assert row["ended_at"] == _ORIG_ENDED  # original run-end preserved


def test_needs_rework_ancestor_completes_via_b2_edge(fresh_db):
    """Stored needs_rework goes through the real reconcile-only B2 edge."""
    _seed(fresh_db, "sprint-1.1", "needs_rework",
          end_reason="ticket-failures", ended_at=_ORIG_ENDED)
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    assert _reconcile("sprint-1.1") is True
    row = _row(fresh_db, "sprint-1.1")
    assert fresh_db.canonical_lifecycle(row["state"]) == "completed"
    assert row["end_reason"] == "superseded"


def test_base_label_itself_completes(fresh_db):
    _seed(fresh_db, "sprint-1", "needs_rework", end_reason="ticket-failures")
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    assert _reconcile("sprint-1") is True
    row = _row(fresh_db, "sprint-1")
    assert fresh_db.canonical_lifecycle(row["state"]) == "completed"


def test_no_completed_descendant_falls_back_to_promotion(fresh_db):
    """Merged base alone must NOT complete — merged-branch set is history, not
    state; a later failed rework cycle would otherwise silently auto-complete."""
    _seed(fresh_db, "sprint-1", "needs_rework", end_reason="ticket-failures")

    assert _reconcile("sprint-1") is True
    row = _row(fresh_db, "sprint-1")
    assert fresh_db.canonical_lifecycle(row["state"]) == "ready_to_merge"

    # ready_to_merge without the guards stays put (no patch at all).
    _seed(fresh_db, "sprint-2", "ready_to_merge", end_reason="natural")
    from routers import sprint_reconcile_service as svc
    patches = _patches(merged_branches={"sprint/sprint-2"})
    for p in patches:
        p.start()
    try:
        patch_row = svc._github_reconcile_row(
            "sprint-2", _PROJ, _row(fresh_db, "sprint-2"))
    finally:
        for p in patches:
            p.stop()
    assert patch_row is None


def test_base_branch_not_merged_blocks_completion(fresh_db):
    _seed(fresh_db, "sprint-1.2", "ready_to_merge", end_reason="ticket-failures")
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    _reconcile("sprint-1.2", merged_branches=set())
    row = _row(fresh_db, "sprint-1.2")
    assert fresh_db.canonical_lifecycle(row["state"]) == "ready_to_merge"


def test_gh_error_fails_safe(fresh_db):
    _seed(fresh_db, "sprint-1.2", "ready_to_merge", end_reason="ticket-failures")
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    _reconcile("sprint-1.2", merged_raises=True)
    row = _row(fresh_db, "sprint-1.2")
    assert fresh_db.canonical_lifecycle(row["state"]) == "ready_to_merge"


def test_intermediate_member_keys_on_base_branch(fresh_db):
    """sprint/sprint-1.2 never got its own develop PR — only the BASE branch
    matters (stacked chains merge child→parent→…→develop)."""
    _seed(fresh_db, "sprint-1.2", "needs_rework", end_reason="ticket-failures")
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    assert _reconcile("sprint-1.2",
                      merged_branches={"sprint/sprint-1"}) is True
    row = _row(fresh_db, "sprint-1.2")
    assert fresh_db.canonical_lifecycle(row["state"]) == "completed"


def test_open_rework_blocks_completion(fresh_db):
    _seed(fresh_db, "sprint-1.1", "needs_rework", end_reason="ticket-failures")
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    _reconcile("sprint-1.1", has_rework=True)
    row = _row(fresh_db, "sprint-1.1")
    assert fresh_db.canonical_lifecycle(row["state"]) == "needs_rework"


def test_project_scoping(fresh_db):
    """A completed sibling in project A must not complete project B's sprint."""
    _seed(fresh_db, "sprint-1.1", "needs_rework", end_reason="ticket-failures")
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")
    _seed(fresh_db, "sprint-1.1", "needs_rework",
          end_reason="ticket-failures", project=_OTHER_PROJ)

    assert _reconcile("sprint-1.1", project=_PROJ) is True
    assert fresh_db.canonical_lifecycle(
        _row(fresh_db, "sprint-1.1")["state"]) == "completed"

    _reconcile("sprint-1.1", project=_OTHER_PROJ)
    other = _row(fresh_db, "sprint-1.1", project=_OTHER_PROJ)
    # No completed descendant in B → only the plain promotion applies.
    assert fresh_db.canonical_lifecycle(other["state"]) == "ready_to_merge"


def test_sweep_reexamines_ready_to_merge_rows(fresh_db, tmp_path):
    """End-to-end reconcile_project: rtm zombies are in the scan window and heal."""
    from routers import sprint_reconcile_service as svc
    for lbl in ("sprint-1", "sprint-1.1", "sprint-1.2"):
        _seed(fresh_db, lbl, "ready_to_merge",
              end_reason="ticket-failures", ended_at=_ORIG_ENDED)
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    patches = _patches()
    patches.extend(
        patch(f"{m}._project_root_path", return_value=tmp_path)
        for m in ("server", "startup")
        if hasattr(importlib.import_module(m), "_project_root_path")
    )
    for p in patches:
        p.start()
    try:
        updated = svc.reconcile_project(_PROJ)
    finally:
        for p in patches:
            p.stop()

    assert {"sprint-1", "sprint-1.1", "sprint-1.2"} <= set(updated)
    for lbl in ("sprint-1", "sprint-1.1", "sprint-1.2"):
        row = _row(fresh_db, lbl)
        assert fresh_db.canonical_lifecycle(row["state"]) == "completed", lbl
        assert row["end_reason"] == "superseded", lbl
        assert row["ended_at"] == _ORIG_ENDED, lbl
    tip = _row(fresh_db, "sprint-1.3")
    assert tip["end_reason"] == "merge_sprint"  # tip untouched


def test_sweep_completion_emits_event_and_preview_does_not(fresh_db):
    from routers import sprint_reconcile_service as svc
    _seed(fresh_db, "sprint-1.2", "ready_to_merge",
          end_reason="ticket-failures", ended_at=_ORIG_ENDED)
    _seed(fresh_db, "sprint-1.3", "completed", end_reason="merge_sprint")

    # Dry-run preview: reports the divergence, writes nothing, emits nothing.
    emit = MagicMock()
    patches = _patches(emit_mock=emit)
    for p in patches:
        p.start()
    try:
        preview = svc.reconcile_preview("sprint-1.2", _PROJ)
    finally:
        for p in patches:
            p.stop()
    assert preview["would_change"] is True
    assert preview["github_state"] == "completed"
    assert preview["reason"] == "superseded"
    row = _row(fresh_db, "sprint-1.2")
    assert fresh_db.canonical_lifecycle(row["state"]) == "ready_to_merge"
    assert not emit.called

    # Apply: writes the completion and leaves an audit event.
    emit = MagicMock()
    patches = _patches(emit_mock=emit)
    for p in patches:
        p.start()
    try:
        assert svc.reconcile_sprint_label("sprint-1.2", _PROJ) is True
    finally:
        for p in patches:
            p.stop()
    assert emit.called
    kwargs = emit.call_args.kwargs
    assert kwargs["type"] == "sprint_lineage_superseded"
    assert kwargs["target"] == "sprint-1.2"
    assert kwargs["detail"]["trigger"] == "reconcile_sweep"
    assert kwargs["detail"]["from_state"] == "ready_to_merge"
