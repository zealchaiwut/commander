"""Tests for issue #1454 — child label strip set must omit SIT/UAT workflow-state labels.

Follow-up to #1423. `build_child_labels()` copies a parent's labels onto a new
child issue, stripping per-ticket lifecycle/size labels via `_STRIP_LABELS`.
That strip set originally omitted the downstream workflow-state labels `SIT` and
`UAT`, so splitting a ticket already carrying a stage label produced a child
mislabeled into a downstream column. Each test below maps to one acceptance
criterion.

Issue #1771: re-anchored import from the deleted orphan `split_ticket_service`
to the live `split_xl_service` path; added AC test exercising `split_apply()`.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers.split_xl_service import _STRIP_LABELS, build_child_labels  # noqa: E402
from apps.dashboard.routers import split_xl_service  # noqa: E402


# AC1 — _STRIP_LABELS includes SIT and UAT in addition to the existing entries.
def test_strip_labels_includes_sit_uat_and_existing_entries():
    expected = {"size-S", "size-M", "size-L", "size-XL", "estimated", "in-progress", "SIT", "UAT"}
    assert expected.issubset(set(_STRIP_LABELS))


# AC2 — build_child_labels() does not copy SIT regardless of parent labels.
def test_build_child_labels_strips_sit():
    parent = ["enhancement", "SIT", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")
    assert "SIT" not in child
    assert "enhancement" in child
    assert "sprint-99.1" in child


# AC3 — build_child_labels() does not copy UAT regardless of parent labels.
def test_build_child_labels_strips_uat():
    parent = ["enhancement", "UAT", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")
    assert "UAT" not in child
    assert "enhancement" in child
    assert "sprint-99.1" in child


# AC4 — child from a parent carrying a stage label starts in a clean state
# (only non-stripped labels retained).
def test_child_from_staged_parent_starts_clean():
    parent = ["enhancement", "backend", "SIT", "UAT", "size-XL", "estimated", "in-progress", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")
    assert set(child) == {"enhancement", "backend", "sprint-99.1"}


# AC5 — splitting a backlog parent (no stage labels) is unchanged: the fix is
# additive and does not regress the happy path.
def test_backlog_parent_happy_path_unchanged():
    parent = ["enhancement", "backend", "size-XL", "estimated", "sprint-99.1"]
    child = build_child_labels(parent, "sprint-99.1")
    # size-XL and estimated were already stripped before this fix; the result is
    # identical to pre-fix behaviour because no SIT/UAT was present.
    assert set(child) == {"enhancement", "backend", "sprint-99.1"}
    assert "size-XL" not in child
    assert "SIT" not in child and "UAT" not in child


# ── Live path tests (issue #1771) ────────────────────────────────────────────


class _FakeGH:
    """Minimal github_client fake for testing split_apply() label inheritance."""

    def __init__(self, parent_labels=None):
        self._parent_labels = parent_labels or []
        self._next_number = 7000
        self.created: list[tuple[str, list]] = []  # (title, labels) pairs
        self.closed = []
        self.assigned = []

    def get_issue(self, issue_number, repo_name=None):
        return {
            "number": issue_number,
            "labels": [{"name": lbl} for lbl in self._parent_labels],
        }

    def create_label(self, name, color, description="", repo_name=None):
        pass

    def create_issue(self, title, body, labels, repo_name=None):
        self._next_number += 1
        self.created.append((title, list(labels)))
        return self._next_number, f"https://github.com/o/r/issues/{self._next_number}"

    def add_comment(self, issue_id, body, repo_name=None):
        pass

    def assign_sprint(self, issue_id, sprint_num, repo_name=None):
        self.assigned.append(issue_id)

    def close_issue(self, issue_id, repo_name=None, reason=None):
        self.closed.append(issue_id)

    def invalidate(self, prefix=""):
        pass


class _FakeServer:
    def __init__(self, gh):
        self.github_client = gh


def _two_children():
    return [
        {"title": "Child A", "body": "## AC\n- do A"},
        {"title": "Child B", "body": "## AC\n- do B"},
    ]


# AC6 (live path) — split_apply() does not put SIT on child tickets even when
# the parent issue carries SIT.
def test_split_apply_does_not_inherit_sit(monkeypatch):
    gh = _FakeGH(parent_labels=["enhancement", "SIT", "sprint-99.1"])
    monkeypatch.setattr(split_xl_service, "_server", lambda: _FakeServer(gh))
    result = split_xl_service.split_apply("o/r", "sprint-99.1", 200, _two_children())
    assert result["ok"] is True
    for _title, labels in gh.created:
        assert "SIT" not in labels, f"child ticket must not carry SIT; got labels {labels}"


# AC7 (live path) — split_apply() does not put UAT on child tickets even when
# the parent issue carries UAT.
def test_split_apply_does_not_inherit_uat(monkeypatch):
    gh = _FakeGH(parent_labels=["enhancement", "UAT", "sprint-99.1"])
    monkeypatch.setattr(split_xl_service, "_server", lambda: _FakeServer(gh))
    result = split_xl_service.split_apply("o/r", "sprint-99.1", 200, _two_children())
    assert result["ok"] is True
    for _title, labels in gh.created:
        assert "UAT" not in labels, f"child ticket must not carry UAT; got labels {labels}"


# AC8 (live path) — split_apply() strips both SIT and UAT when parent has both,
# while preserving non-lifecycle labels and adding split-child.
def test_split_apply_strips_sit_and_uat_preserves_custom_labels(monkeypatch):
    gh = _FakeGH(parent_labels=["enhancement", "backend", "SIT", "UAT", "size-XL", "sprint-99.1"])
    monkeypatch.setattr(split_xl_service, "_server", lambda: _FakeServer(gh))
    result = split_xl_service.split_apply("o/r", "sprint-99.1", 200, _two_children())
    assert result["ok"] is True
    for _title, labels in gh.created:
        assert "SIT" not in labels
        assert "UAT" not in labels
        assert "size-XL" not in labels
        assert "split-child" in labels
        assert "sprint-99.1" in labels
        assert "enhancement" in labels
        assert "backend" in labels
