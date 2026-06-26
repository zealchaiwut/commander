"""PR-B: History active_only view + completed-sprint title backfill.

- active_only returns actionable sprints + the N most-recent closed ones.
- Issue rows synthesized from agent_runs carry no title; a title_map sourced
  from the local issues mirror backfills blanks so completed sprints show text
  (without overwriting titles that are already present).
- The agent_runs-heavy issue enrichment runs on the visible window only, while
  the lineage rollup still sees every record.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from routers import sprint_history_service as h  # noqa: E402


def test_filter_active_keeps_actionable_only_by_default():
    recs = [
        {"label": "sprint-1", "lifecycle_state": "draft", "_sort_key": "2026-06-01"},
        {"label": "sprint-2", "lifecycle_state": "needs_rework", "_sort_key": "2026-06-02"},
        {"label": "sprint-3", "lifecycle_state": "completed", "_sort_key": "2026-06-03"},
        {"label": "sprint-4", "lifecycle_state": "completed", "_sort_key": "2026-06-04"},
    ]
    out = h._filter_active_records(recs)
    labels = {r["label"] for r in out}
    assert labels == {"sprint-2"}


def test_filter_active_optional_completed_tail():
    recs = [
        {"label": "sprint-2", "lifecycle_state": "needs_rework", "_sort_key": "2026-06-02"},
        {"label": "sprint-3", "lifecycle_state": "completed", "_sort_key": "2026-06-03"},
        {"label": "sprint-4", "lifecycle_state": "completed", "_sort_key": "2026-06-04"},
        {"label": "sprint-5", "lifecycle_state": "completed", "_sort_key": "2026-06-05"},
        {"label": "sprint-6", "lifecycle_state": "deleted", "_sort_key": "2026-06-06"},
    ]
    out = h._filter_active_records(recs, keep_completed=3)
    labels = {r["label"] for r in out}
    assert "sprint-2" in labels
    assert {"sprint-6", "sprint-5", "sprint-4"} <= labels
    assert "sprint-3" not in labels
    assert len(out) == 4


def test_title_backfill_fills_blanks_only(tmp_path, monkeypatch):
    rec = {
        "label": "sprint-9", "project": "p", "lifecycle_state": "completed",
        "issues": [
            {"ticket_id": 1247, "state": "merged"},                    # blank title
            {"ticket_id": 1248, "state": "merged", "title": "keep me"},  # already titled
        ],
    }

    class FakeDb:
        def get_mirrored_issues(self, repo):
            return []

        def get_mirrored_issue(self, repo, num):
            return None

    monkeypatch.setattr(h, "_db", lambda: FakeDb())
    h._finalize_issues([rec], tmp_path, title_map={1247: "Backfilled", 1248: "OVERWRITE?"})
    by = {i["ticket_id"]: i for i in rec["issues"]}
    assert by[1247].get("title") == "Backfilled"   # blank filled from mirror
    assert by[1248].get("title") == "keep me"       # existing title preserved


def test_title_backfill_uses_per_ticket_mirror_lookup(tmp_path, monkeypatch):
    rec = {
        "label": "sprint-83", "project": "zealchaiwut/perf-coach",
        "lifecycle_state": "ready_to_merge",
        "issues": [{"ticket_id": 910, "state": "merged", "title": ""}],
    }

    class FakeDb:
        def get_mirrored_issue(self, repo, num):
            assert repo == "zealchaiwut/perf-coach"
            assert num == 910
            return {"number": 910, "title": "Mirror title for 910"}

    monkeypatch.setattr(h, "_db", lambda: FakeDb())
    h._finalize_issues([rec], tmp_path, title_map={})
    assert rec["issues"][0]["title"] == "Mirror title for 910"


def test_finalize_records_wrapper_still_runs_both_passes(tmp_path):
    # Back-compat: the wrapper used by tests must apply lineage + issues together.
    parent = {"label": "sprint-7", "lifecycle_state": "needs_rework", "issues": []}
    child = {"label": "sprint-7.1", "lifecycle_state": "completed", "issues": []}
    h._finalize_records([parent, child], tmp_path)
    # superseded parent whose only child completed → rolled up to completed
    assert parent["lifecycle_state"] == "completed"
    assert parent.get("has_rerun_child") is True
