"""Honest completed-vs-needs_rework (Bug 1).

A sprint shown completed while its own open work tickets are still SIT / needs-
rework is not done. Display must downgrade it to needs_rework (so the card shows
Re-run, not Complete), and the complete endpoints must refuse it.
"""
from __future__ import annotations

import sys
import types

# sys.path is configured by tests/conftest.py (apps/dashboard first), so `config`
# and `startup`/`server` resolve correctly. Don't re-order it here.


# ── the predicate the fix relies on: SIT / needs-rework / non-DONE → rework ──

def test_has_rework_tickets_treats_sit_and_rework_as_rework(monkeypatch):
    import startup  # _has_rework_tickets is defined here  # noqa: PLC0415

    cases = {
        # label -> issues (each issue = {labels:[{name}]})
        "rework": [{"labels": [{"name": "needs-rework"}]}],
        "sit": [{"labels": [{"name": "SIT"}]}],
        "in_progress": [{"labels": [{"name": "in-progress"}]}],  # never reached DONE
        "done": [{"labels": [{"name": "UAT"}]}],
        "summary_only": [{"labels": [{"name": "sprint-summary"}]}],
    }

    # _has_rework_tickets resolves _get_sprint_issues from its own module globals.
    monkeypatch.setattr(startup, "_get_sprint_issues",
                        lambda project, label: cases.get(label, []))
    assert startup._has_rework_tickets("rework", "o/r") is True
    assert startup._has_rework_tickets("sit", "o/r") is True
    assert startup._has_rework_tickets("in_progress", "o/r") is True
    assert startup._has_rework_tickets("done", "o/r") is False
    assert startup._has_rework_tickets("summary_only", "o/r") is False


# ── display downgrade in _finalize_issues ──

def _stub_finalize_deps(monkeypatch, shs, has_rework_for: set[str]):
    """No-op the heavy per-record helpers + inject a fake server with the predicate."""
    for name in (
        "_fill_missing_links",
        "_reconcile_issue_outcomes_with_agent_runs",
        "_attribute_issues_to_runs",
        "_drop_cross_project_issues",
    ):
        monkeypatch.setattr(shs, name, lambda *a, **k: None)
    monkeypatch.setattr(shs, "_issues_from_agent_runs", lambda *a, **k: [])
    monkeypatch.setattr(shs, "_db", lambda: types.SimpleNamespace(
        get_mirrored_issue=lambda *a, **k: None))
    fake_server = types.ModuleType("server")
    fake_server._has_rework_tickets = lambda label, project: label in has_rework_for
    monkeypatch.setitem(sys.modules, "server", fake_server)


def test_completed_with_rework_downgraded_to_needs_rework(monkeypatch, tmp_path):
    from apps.dashboard.routers import sprint_history_service as shs  # noqa: PLC0415
    _stub_finalize_deps(monkeypatch, shs, has_rework_for={"sprint-92"})
    rec = {"label": "sprint-92", "project": "o/r",
           "lifecycle_state": "completed", "issues": [{"ticket_id": 1}]}
    shs._finalize_issues([rec], tmp_path)
    assert rec["lifecycle_state"] == "needs_rework"
    assert rec.get("end_reason") == "ticket-rework"


def test_completed_without_rework_stays_completed(monkeypatch, tmp_path):
    from apps.dashboard.routers import sprint_history_service as shs  # noqa: PLC0415
    _stub_finalize_deps(monkeypatch, shs, has_rework_for=set())
    rec = {"label": "sprint-93", "project": "o/r",
           "lifecycle_state": "completed", "issues": [{"ticket_id": 1}]}
    shs._finalize_issues([rec], tmp_path)
    assert rec["lifecycle_state"] == "completed"
