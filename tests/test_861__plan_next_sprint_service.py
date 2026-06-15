"""Service-boundary tests for issue #861 — plan_next_sprint wiring.

Exercises ``sprints_service.plan_next_sprint`` end-to-end with a faked server +
github_client so the GitHub creation path (AC2), the pending-sign-off persistence
(AC9), the milestone filter (AC3), and the empty/conflict guards (AC10/AC11) are
verified without touching real GitHub, the estimator subprocess, or settings DB.
"""
from __future__ import annotations

import json
import re

import pytest

from apps.dashboard.routers import sprints_service


class FakeGH:
    def __init__(self, milestones, issues, sprint_issues=None, sprints=None):
        self.milestones = milestones
        self.issues = issues
        self._sprint_issues = sprint_issues or {}
        self.sprints = sprints or []
        self.created_labels: list[int] = []
        self.applied: dict[int, set] = {}
        self.deleted_labels: list[str] = []

    def list_milestones(self, repo_name=None):
        return self.milestones

    def list_open_issues_for_planning(self, repo_name=None):
        return self.issues

    def classify_issue(self, iss):
        labels = {l["name"] for l in iss.get("labels", [])}
        if iss.get("state") == "closed" or "UAT-approved" in labels:
            return "done"
        if "UAT" in labels:
            return "uat"
        if "SIT" in labels:
            return "sit"
        if "in-progress" in labels:
            return "in-progress"
        return "backlog"

    def list_issues(self, n, repo_name=None):
        return self._sprint_issues.get(n, [])

    def list_sprints(self, repo_name=None):
        return self.sprints

    def create_sprint_label_strict(self, num, repo_name=None):
        self.created_labels.append(num)

    def sprint_label_exists(self, num, repo_name=None):
        return num in self.created_labels

    def update_labels(self, issue, add, remove, repo_name=None):
        s = self.applied.setdefault(issue, set())
        s.update(add)
        s.difference_update(remove)

    def get_issue(self, issue, repo_name=None):
        return {"number": issue,
                "labels": [{"name": n} for n in self.applied.get(issue, set())]}

    def delete_label(self, name, repo_name=None):
        self.deleted_labels.append(name)

    def invalidate(self, prefix):
        pass


class FakeSettingsRepo:
    def __init__(self, stored):
        self._stored = stored

    def get_setting(self, key, project=None):
        return self._stored


class FakeSrv:
    def __init__(self, gh, root, settings):
        self.github_client = gh
        self._root = root
        self._settings = settings
        self.APP_CONFIG_KEY = "app_config"
        self._settings_repo = FakeSettingsRepo(settings)
        self._finished: dict = {}
        self._SPRINT_LABEL_RE = re.compile(r"sprint-(\d+)")

    def build_effective_response(self, stored):
        merged = {"sprint_budget_minutes": 180,
                  "estimation_s_minutes": 5, "estimation_m_minutes": 15,
                  "estimation_l_minutes": 30, "estimation_xl_minutes": 60}
        merged.update(stored or {})
        return merged

    def _project_root_path(self, project):
        return self._root

    def _commander_dir(self, root):
        d = root / ".commander"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _sprint_json_path(self, root, label):
        return self._commander_dir(root) / "sprints" / f"{label}.json"

    def _sprint_json_write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def _finished_sprint_summaries(self, project):
        return self._finished

    def _resolve_issue_estimate(self, iss, estimates_dir):
        names = {l["name"] for l in iss.get("labels", [])}
        size = None
        for s in ("XL", "L", "M", "S"):
            if f"size-{s}" in names:
                size = s
                break
        return {"size": size, "files": [], "estimated": size is not None,
                "source": "label" if size else None}


def _issue(num, milestone, *sizes, state="open"):
    labels = [{"name": s} for s in sizes]
    return {"number": num, "title": f"Issue {num}", "state": state,
            "milestone": {"title": milestone} if milestone else None,
            "labels": labels, "body": ""}


@pytest.fixture
def patch_server(monkeypatch):
    def _install(gh, root, settings):
        srv = FakeSrv(gh, root, settings)
        monkeypatch.setattr(sprints_service, "_server", lambda: srv)
        return srv
    return _install


# ── AC2 + AC3 + AC9: creates a pending-sign-off sprint from milestone backlog ─

def test_plan_creates_pending_signoff_sprint(tmp_path, patch_server):
    gh = FakeGH(
        milestones=[{"title": "M1", "state": "open", "dueOn": "2026-07-01T00:00:00Z"}],
        issues=[
            _issue(1, "M1", "size-M"),
            _issue(2, "M1", "size-M"),
            _issue(3, "M1", "size-L"),
            _issue(4, "M2", "size-S"),   # other milestone — must be excluded (AC3)
        ],
        sprints=[],
    )
    patch_server(gh, tmp_path, {"sprint_budget_minutes": 35})

    res = sprints_service.plan_next_sprint("owner/repo")

    assert res["status"] == "ok"
    assert res["created"] is True
    assert res["sprint_label"] == "sprint-1"
    # capacity 35 = 15 (#1) + 15 (#2); #3 (L=30) overflows → excluded (AC5).
    assert res["tickets"] == [1, 2]
    assert res["total_minutes"] == 30
    # AC2: the sprint label was created and applied to exactly the selected tickets.
    assert gh.created_labels == [1]
    assert "sprint-1" in gh.applied[1] and "sprint-1" in gh.applied[2]
    assert 4 not in gh.applied   # other-milestone ticket never touched (AC3)
    # AC9: persisted plan carries the pending-sign-off status.
    plan = json.loads((tmp_path / ".commander" / "sprints" / "sprint-1.json").read_text())
    assert plan["status"] == "pending-signoff"
    assert plan["tickets"] == [1, 2]
    assert plan["milestone"] == "M1"


# ── AC10: zero capacity → empty, nothing created ─────────────────────────────

def test_plan_zero_capacity_creates_nothing(tmp_path, patch_server):
    gh = FakeGH(
        milestones=[{"title": "M1", "state": "open"}],
        issues=[_issue(1, "M1", "size-M")],
        sprints=[],
    )
    patch_server(gh, tmp_path, {"sprint_budget_minutes": 0})

    res = sprints_service.plan_next_sprint("owner/repo")

    assert res["status"] == "empty"
    assert res["created"] is False
    assert gh.created_labels == []  # no sprint label created


# ── AC1: no active milestone → no_milestone, nothing created ─────────────────

def test_plan_no_active_milestone(tmp_path, patch_server):
    gh = FakeGH(milestones=[{"title": "Old", "state": "closed"}], issues=[])
    patch_server(gh, tmp_path, {})

    res = sprints_service.plan_next_sprint("owner/repo")

    assert res["status"] == "no_milestone"
    assert res["created"] is False
    assert gh.created_labels == []


# ── AC11: idempotent — existing pending-sign-off draft → conflict prompt ──────

def test_plan_conflict_when_pending_signoff_exists(tmp_path, patch_server):
    gh = FakeGH(
        milestones=[{"title": "M1", "state": "open"}],
        issues=[_issue(1, "M1", "size-M")],
        sprints=[],
    )
    srv = patch_server(gh, tmp_path, {"sprint_budget_minutes": 180})
    # Seed an existing pending-sign-off draft.
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / "sprint-9.json").write_text(json.dumps(
        {"label": "sprint-9", "status": "pending-signoff", "tickets": []}))

    res = sprints_service.plan_next_sprint("owner/repo")

    assert res["status"] == "conflict"
    assert res["created"] is False
    assert res["existing_label"] == "sprint-9"
    assert gh.created_labels == []  # no duplicate sprint created


def test_plan_replace_discards_then_recreates(tmp_path, patch_server):
    """AC11: re-submitting with replace=True bypasses the conflict and plans."""
    gh = FakeGH(
        milestones=[{"title": "M1", "state": "open"}],
        issues=[_issue(1, "M1", "size-S")],
        sprints=[],
    )
    patch_server(gh, tmp_path, {"sprint_budget_minutes": 180})
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / "sprint-9.json").write_text(json.dumps(
        {"label": "sprint-9", "status": "pending-signoff", "tickets": []}))

    res = sprints_service.plan_next_sprint("owner/repo", replace=True)

    assert res["status"] == "ok"
    assert res["created"] is True
    assert res["tickets"] == [1]


# ── AC4: carry-over tickets from the most recent completed sprint come first ──

def test_pending_signoff_sprints_lists_drafts(tmp_path, patch_server):
    """AC9: planned drafts are reported as pending-sign-off for the board."""
    gh = FakeGH(milestones=[], issues=[])
    patch_server(gh, tmp_path, {})
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / "sprint-7.json").write_text(json.dumps(
        {"label": "sprint-7", "status": "pending-signoff"}))
    (sprints_dir / "sprint-6.json").write_text(json.dumps(
        {"label": "sprint-6", "status": "pending"}))  # not a draft

    res = sprints_service.pending_signoff_sprints("owner/repo")
    assert res == {"labels": ["sprint-7"]}


def test_plan_carries_over_unfinished_from_last_sprint(tmp_path, patch_server):
    gh = FakeGH(
        milestones=[{"title": "M1", "state": "open"}],
        issues=[_issue(5, "M1", "size-M")],
        sprint_issues={3: [
            # Sprint 3 (most recent completed): #10 unfinished, #11 done.
            {"number": 10, "state": "open", "labels": [{"name": "size-S"}],
             "milestone": {"title": "M1"}, "body": ""},
            {"number": 11, "state": "closed", "labels": [{"name": "size-S"}],
             "milestone": {"title": "M1"}, "body": ""},
        ]},
        sprints=[1, 2, 3],
    )
    srv = patch_server(gh, tmp_path, {"sprint_budget_minutes": 180})
    srv._finished = {"sprint-3": {"label": "sprint-3"}}

    res = sprints_service.plan_next_sprint("owner/repo")

    assert res["status"] == "ok"
    # #10 (carry-over) is selected first; #11 (done) is not carried; #5 follows.
    assert res["tickets"][0] == 10
    assert 11 not in res["tickets"]
    assert 5 in res["tickets"]
    assert res["sprint_label"] == "sprint-4"  # next after highest existing sprint
