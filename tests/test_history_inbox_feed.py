"""History action inbox feed (_filter_active_records)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from routers import sprint_history_service as h  # noqa: E402


def test_inbox_excludes_draft_planned_running():
    recs = [
        {"label": "sprint-1", "lifecycle_state": "draft", "_sort_key": "2026-06-01"},
        {"label": "sprint-2", "lifecycle_state": "planned", "_sort_key": "2026-06-02"},
        {"label": "sprint-86", "lifecycle_state": "running", "_sort_key": "2026-06-03"},
        {"label": "sprint-3", "lifecycle_state": "needs_rework", "_sort_key": "2026-06-04"},
    ]
    labels = {r["label"] for r in h._filter_active_records(recs)}
    assert labels == {"sprint-3"}


def test_inbox_includes_partial_finished_and_lineage_siblings():
    recs = [
        {"label": "sprint-77", "lifecycle_state": "partial_finished", "_sort_key": "2026-06-10"},
        {"label": "sprint-77.1", "lifecycle_state": "ready_to_merge", "_sort_key": "2026-06-11"},
        {"label": "sprint-77.2", "lifecycle_state": "completed", "_sort_key": "2026-06-09"},
        {"label": "sprint-98", "lifecycle_state": "partial_finished", "_sort_key": "2026-06-12"},
        {"label": "sprint-98.1", "lifecycle_state": "needs_rework", "_sort_key": "2026-06-13"},
    ]
    labels = {r["label"] for r in h._filter_active_records(recs)}
    assert {"sprint-77", "sprint-77.1", "sprint-77.2"} <= labels
    assert {"sprint-98", "sprint-98.1"} <= labels
    assert "sprint-86" not in labels


def test_inbox_pulls_parent_when_only_child_actionable():
    recs = [
        {"label": "sprint-97", "lifecycle_state": "completed", "_sort_key": "2026-06-01"},
        {"label": "sprint-97.5", "lifecycle_state": "failed", "_sort_key": "2026-06-15"},
    ]
    labels = {r["label"] for r in h._filter_active_records(recs)}
    assert labels == {"sprint-97", "sprint-97.5"}


def test_closed_tail_never_includes_running():
    recs = [
        {"label": "sprint-1", "lifecycle_state": "running", "_sort_key": "2026-06-05"},
        {"label": "sprint-2", "lifecycle_state": "completed", "_sort_key": "2026-06-04"},
        {"label": "sprint-3", "lifecycle_state": "completed", "_sort_key": "2026-06-03"},
    ]
    labels = {r["label"] for r in h._filter_active_records(recs, keep_completed=2)}
    assert "sprint-1" not in labels
    assert {"sprint-2", "sprint-3"} <= labels


def test_resolve_sprint_project_never_cross_assigns():
    scope = "zealchaiwut/commander"
    resolved = h._resolve_sprint_project(
        "sprint-77",
        "zealchaiwut/perf-coach",
        [],
        h._db(),
        scope_project=scope,
    )
    assert resolved == ""


def test_get_sprint_history_excludes_other_project_rows(tmp_path, monkeypatch):
    import db as db_mod  # noqa: E402

    db_mod.init_db()
    with db_mod.get_conn() as conn:
        db_mod._create_sprint_lifecycle_tables(conn)
        conn.execute(
            "INSERT INTO sprints (label, project, state, created_at) VALUES (?, ?, ?, ?)",
            ("sprint-77", "zealchaiwut/perf-coach", "needs_rework", "2026-06-01T00:00:00Z"),
        )
        conn.commit()

    sprint_dir = tmp_path / "sprints"
    sprint_dir.mkdir()
    (sprint_dir / "sprint-77-plan.json").write_text(
        '{"project": "zealchaiwut/commander", "state": "needs_rework"}',
        encoding="utf-8",
    )

    monkeypatch.setattr(h, "_resolve_sprints_search_dirs", lambda _p: [sprint_dir])
    result = h.get_sprint_history(project="zealchaiwut/commander", active_only=True)
    assert "sprint-77" not in {r["label"] for r in result["sprints"]}
