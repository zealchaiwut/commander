"""Tests for issue #1454 — child label strip set must omit SIT/UAT workflow-state labels.

Follow-up to #1423. `build_child_labels()` copies a parent's labels onto a new
child issue, stripping per-ticket lifecycle/size labels via `_STRIP_LABELS`.
That strip set originally omitted the downstream workflow-state labels `SIT` and
`UAT`, so splitting a ticket already carrying a stage label produced a child
mislabeled into a downstream column. Each test below maps to one acceptance
criterion.
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

from routers.split_ticket_service import _STRIP_LABELS, build_child_labels  # noqa: E402


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
