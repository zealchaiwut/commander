"""Tests for issue #1453 — split confirm has no rollback on partial failure.

The XL-ticket split flow (`split_xl_service.split_apply`, the descendant of the
ticket's `confirm_split()`) creates child issues on GitHub and then handles the
original (comment → strip sprint label → close). If a child fails midway, or the
original-handling step raises, children may already exist on GitHub with no
recovery info for the user. This ticket adds partial-failure detection,
best-effort compensation (closing orphaned children), and a structured error.

AC coverage:
  AC1: child1 created but child2 fails → error response includes created child number(s).
  AC2: original-handling step fails after both children created → error includes both numbers.
  AC3: error message distinguishes partial failure (some created) vs total failure (none created).
  AC4: on partial failure, best-effort compensation closes orphaned children; response says succeeded/failed.
  AC5: if compensation itself fails, response still includes orphan number(s) + manual-cleanup message.
  AC6: a fully successful split is unaffected — same response shape as before.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from apps.dashboard.routers import split_xl_service  # noqa: E402


# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeGH:
    """A stand-in for github_client that records calls and can fail on demand.

    fail_create_on: 1-based index of the create_issue call that should raise.
    fail_assign / fail_close: make the original-handling steps raise.
    fail_close_on: set of issue numbers whose close_issue() should raise (used to
                   simulate compensation failure while still closing the original).
    """

    def __init__(self, *, fail_create_on=None, fail_assign=False,
                 fail_close=False, fail_close_on=None):
        self.fail_create_on = fail_create_on
        self.fail_assign = fail_assign
        self.fail_close = fail_close
        self.fail_close_on = set(fail_close_on or ())
        self._create_calls = 0
        self._next_number = 5000
        self.created_numbers = []
        self.closed = []
        self.comments = []
        self.assigned = []
        self.labels = []

    def get_issue(self, issue_number, repo_name=None):
        return {"number": issue_number, "labels": []}

    def create_label(self, name, color, description="", repo_name=None):
        self.labels.append(name)

    def create_issue(self, title, body, labels, repo_name=None):
        self._create_calls += 1
        if self.fail_create_on and self._create_calls == self.fail_create_on:
            raise RuntimeError("GitHub rejected the issue (invalid label)")
        self._next_number += 1
        n = self._next_number
        self.created_numbers.append(n)
        return n, f"https://github.com/o/r/issues/{n}"

    def add_comment(self, issue_id, body, repo_name=None):
        self.comments.append(issue_id)

    def assign_sprint(self, issue_id, sprint_num, repo_name=None):
        if self.fail_assign:
            raise RuntimeError("refusing: a sprint is running")
        self.assigned.append(issue_id)

    def close_issue(self, issue_id, repo_name=None, reason=None):
        if self.fail_close and issue_id == self._original_issue:
            raise RuntimeError("close failed")
        if issue_id in self.fail_close_on:
            raise RuntimeError("compensation close failed")
        self.closed.append(issue_id)

    def invalidate(self, prefix=""):
        pass

    # convenience for fail_close to know which id is the original
    _original_issue = None


class FakeServer:
    def __init__(self, gh):
        self.github_client = gh


@pytest.fixture
def patch_server(monkeypatch):
    def _install(gh):
        monkeypatch.setattr(split_xl_service, "_server", lambda: FakeServer(gh))
        return gh
    return _install


def _two_children():
    return [
        {"title": "Child one", "body": "## Acceptance Criteria\n- a"},
        {"title": "Child two", "body": "## Acceptance Criteria\n- b"},
    ]


# ── AC1: partial failure surfaces already-created child numbers ────────────────


def test_ac1_child2_failure_includes_created_child_number(patch_server):
    """AC1: child1 created, child2 create fails → result lists child1's number."""
    patch_server(FakeGH(fail_create_on=2))
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    assert res["ok"] is False
    nums = res.get("created_numbers")
    assert isinstance(nums, list) and len(nums) == 1, (
        "expected exactly one created child number, got %r" % (nums,)
    )
    assert str(nums[0]) in str(res.get("error", "")), (
        "error message must name the created child number for cleanup"
    )


# ── AC2: original-handling failure after both children → both numbers ──────────


def test_ac2_original_handling_failure_includes_both_children(patch_server):
    """AC2: both children created, sprint-assignment raises → both numbers surfaced."""
    patch_server(FakeGH(fail_assign=True))
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    assert res["ok"] is False
    nums = res.get("created_numbers")
    assert isinstance(nums, list) and len(nums) == 2, (
        "both created child numbers must be reported on original-handling failure"
    )
    for n in nums:
        assert str(n) in str(res.get("error", "")), f"child {n} missing from error message"


def test_ac2_original_close_failure_includes_both_children(patch_server):
    """AC2 variant: close_issue on the original raises after both children created."""
    gh = FakeGH(fail_close=True)
    gh._original_issue = 100
    patch_server(gh)
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    assert res["ok"] is False
    assert len(res.get("created_numbers", [])) == 2


# ── AC3: partial vs total failure are distinguished ────────────────────────────


def test_ac3_total_failure_when_no_child_created(patch_server):
    """AC3: first child create fails → total failure, partial flag False, no orphans."""
    patch_server(FakeGH(fail_create_on=1))
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    assert res["ok"] is False
    assert res.get("partial") is False, "no children created ⇒ not a partial failure"
    assert res.get("created_numbers") == []


def test_ac3_partial_flag_true_when_some_created(monkeypatch):
    """AC3: a child exists ⇒ partial flag True and message differs from total failure."""
    monkeypatch.setattr(
        split_xl_service, "_server", lambda: FakeServer(FakeGH(fail_create_on=2))
    )
    partial = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())
    assert partial.get("partial") is True

    # total-failure message must read differently from the partial one
    monkeypatch.setattr(
        split_xl_service, "_server", lambda: FakeServer(FakeGH(fail_create_on=1))
    )
    total = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())
    assert partial.get("error") != total.get("error"), (
        "partial and total failure messages must be distinguishable"
    )


# ── AC4: best-effort compensation closes orphaned children ─────────────────────


def test_ac4_compensation_closes_orphaned_child(patch_server):
    """AC4: on partial failure the created child is closed; result says compensation ok."""
    gh = patch_server(FakeGH(fail_create_on=2))
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    orphan = res["created_numbers"][0]
    assert orphan in gh.closed, "orphaned child must be closed as compensation"
    comp = res.get("compensation") or {}
    assert comp.get("attempted") is True
    assert comp.get("succeeded") is True
    assert orphan in comp.get("closed", [])


# ── AC5: compensation failure still surfaces orphan + manual-cleanup message ───


def test_ac5_compensation_failure_reports_manual_cleanup(patch_server):
    """AC5: if closing the orphan also fails, result names it + says manual cleanup."""
    # child2 create fails; closing child1 (the orphan) also fails.
    gh = FakeGH(fail_create_on=2)
    # the orphan will be the first created number; FakeGH starts at 5001
    gh.fail_close_on = {5001}
    patch_server(gh)
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    assert res["ok"] is False
    comp = res.get("compensation") or {}
    assert comp.get("succeeded") is False
    assert 5001 in comp.get("failed", [])
    assert comp.get("manual_cleanup_required") is True
    msg = str(res.get("error", "")).lower()
    assert "manual cleanup" in msg, "message must instruct manual cleanup on GitHub"
    assert "5001" in str(res.get("error", "")), "orphan number must remain in the message"


# ── AC6: happy path is unaffected ──────────────────────────────────────────────


def test_ac6_happy_path_response_unchanged(patch_server):
    """AC6: a clean split returns ok with closed + children, no failure keys."""
    gh = patch_server(FakeGH())
    res = split_xl_service.split_apply("o/r", "sprint-99.1", 100, _two_children())

    assert res["ok"] is True
    assert res["closed"] == 100
    assert [c["number"] for c in res["children"]] == gh.created_numbers
    # The original is properly handled on the happy path.
    assert 100 in gh.closed
    assert 100 in gh.assigned
    # No partial-failure machinery leaks into a success.
    assert "partial" not in res
    assert "compensation" not in res


# ── Router: failure becomes HTTP 500 carrying the structured detail ────────────


def test_router_partial_failure_raises_500_with_created_numbers(monkeypatch):
    """The apply endpoint turns a not-ok result into HTTP 500 with created numbers."""
    from fastapi import HTTPException

    from apps.dashboard.routers import sprint_history

    fail_result = {
        "ok": False,
        "partial": True,
        "error": "Split failed: ... children [5001] created and closed.",
        "created": [{"number": 5001, "url": "u", "title": "t"}],
        "created_numbers": [5001],
        "compensation": {"attempted": True, "closed": [5001], "failed": [],
                         "succeeded": True, "manual_cleanup_required": False},
    }
    monkeypatch.setattr(
        split_xl_service, "split_apply", lambda *a, **k: fail_result
    )

    body = sprint_history.SplitXlApplyBody(project="o/r", children=_two_children())
    with pytest.raises(HTTPException) as ei:
        asyncio.run(sprint_history.split_xl_apply("sprint-99.1", 100, body))

    assert ei.value.status_code == 500
    detail = ei.value.detail
    # structured detail must carry the created numbers so the UI can show them
    assert "5001" in str(detail)
    assert 5001 in (detail.get("created") if isinstance(detail, dict) else [])
