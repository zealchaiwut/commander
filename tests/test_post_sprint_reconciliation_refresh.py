"""Post-sprint reconciliation refresh when GitHub state moves on (issue #856 follow-up)."""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "recon_refresh.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    if "db" in sys.modules:
        del sys.modules["db"]
    import db as db_module
    db_module.init_db()
    return db_module


def test_refresh_clears_stale_unmerged_pr(fresh_db, tmp_path, monkeypatch):
    """When GitHub reports the sprint PR merged, refresh updates stored reconciliation."""
    from routers import sprint_reconcile_service as srs

    label = "sprint-83.1"
    repo = "owner/commander"
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    state_path = sprints_dir / f"{label}-state.json"
    stale_recon = {
        "all_clear": False,
        "fingerprint": "old-fp",
        "checks": [
            {
                "name": "sprint_pr",
                "ok": False,
                "detail": "PR #1293 unmerged — unmerged",
                "pr_number": 1293,
            },
            {"name": "summary_issue", "ok": True, "detail": "ok"},
            {"name": "stale_labels", "ok": True, "detail": "ok"},
        ],
    }
    state_path.write_text(
        json.dumps(
            {
                "sprint_label": label,
                "issues": [{"number": 900, "status": "done"}],
                "reconciliation": stale_recon,
            }
        ),
        encoding="utf-8",
    )

    fresh_db.record_sprint_start(label, project=repo)
    fresh_db.record_sprint_ready_to_merge(label, end_reason="natural")
    with fresh_db.get_conn() as conn:
        conn.execute(
            "UPDATE sprints SET project = ?, pr_number = ?, reconciliation_json = ? WHERE label = ?",
            (repo, 1293, json.dumps(stale_recon), label),
        )
        conn.commit()

    monkeypatch.setattr(
        "server._project_root_path",
        lambda _repo: tmp_path,
    )

    fresh_result = {
        "all_clear": True,
        "fingerprint": "new-fp",
        "checks": [
            {
                "name": "sprint_pr",
                "ok": True,
                "detail": "Sprint PR #1293 merged",
                "pr_number": 1293,
            },
            {"name": "summary_issue", "ok": True, "detail": "ok"},
            {"name": "stale_labels", "ok": True, "detail": "ok"},
        ],
    }

    with patch(
        "services.sprint_manager.reconciliation.gather_inputs_via_gh",
        return_value={"summary_issues": [], "pr_info": {"number": 1293, "merged": True}, "tickets": []},
    ), patch(
        "services.sprint_manager.reconciliation.run_reconciliation",
        return_value=fresh_result,
    ):
        assert srs.refresh_post_sprint_reconciliation(label, repo) is True

    row = fresh_db.get_sprint(label)
    saved = json.loads(row["reconciliation_json"])
    assert saved["all_clear"] is True
    assert saved["checks"][0]["ok"] is True
    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved_state["reconciliation"]["all_clear"] is True


def test_refresh_skips_when_already_clear(fresh_db, tmp_path, monkeypatch):
    from routers import sprint_reconcile_service as srs

    label = "sprint-87"
    clear_recon = {
        "all_clear": True,
        "checks": [{"name": "sprint_pr", "ok": True, "detail": "merged", "pr_number": 1311}],
    }
    fresh_db.record_sprint_start(label, project="owner/commander")
    fresh_db.record_sprint_ready_to_merge(label)
    with fresh_db.get_conn() as conn:
        conn.execute(
            "UPDATE sprints SET reconciliation_json = ? WHERE label = ?",
            (json.dumps(clear_recon), label),
        )
        conn.commit()

    assert srs._reconciliation_needs_refresh(clear_recon) is False
