"""Empty/orphan sprint labels must surface on the board (issue #1C-B).

The issues mirror only sees labels attached to an issue, so an EMPTY sprint
label (0 issues — an orphan from a rolled-back create, or a sprint whose tickets
all hot-swapped away) was invisible to `list_sprints` / `list_sprint_labels`.
That hid it from the board and broke New Sprint with "label already exists".

The fix unions the zero-cost mirror labels with a TTL-cached live `gh label list`
so every existing sprint label appears. These tests pin the union behavior.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
os.environ.setdefault("DB_PATH", str(_REPO_ROOT / "commander.db"))

import github_client as gc  # noqa: E402


def test_list_sprints_unions_mirror_and_live(monkeypatch):
    # Mirror sees only labels-on-issues (66, 67); the empty orphan 69 is not.
    monkeypatch.setattr(gc, "_mirror_labels",
                        lambda r: [{"name": "sprint-66", "color": ""},
                                   {"name": "sprint-67", "color": ""}])
    monkeypatch.setattr(gc, "_live_sprint_label_names",
                        lambda r: ["sprint-66", "sprint-67", "sprint-69"])
    nums = gc.list_sprints("owner/repo")
    assert nums == [66, 67, 69]
    assert 69 in nums, "empty orphan label must surface via the live union"


def test_list_sprint_labels_unions_and_sorts(monkeypatch):
    monkeypatch.setattr(gc, "_mirror_labels",
                        lambda r: [{"name": "sprint-67", "color": ""},
                                   {"name": "sprint-67.1", "color": ""}])
    monkeypatch.setattr(gc, "_live_sprint_label_names",
                        lambda r: ["sprint-67", "sprint-69"])
    labels = gc.list_sprint_labels("owner/repo")
    assert "sprint-69" in labels
    # natural sort: base then suffix, no duplicates
    assert labels == sorted(set(labels), key=gc._sprint_label_sort_key_gc)


def test_live_fetch_failure_degrades_to_mirror(monkeypatch):
    # A gh failure must not break enumeration — live returns [], union = mirror.
    monkeypatch.setattr(gc, "_mirror_labels",
                        lambda r: [{"name": "sprint-66", "color": ""}])

    def _boom(*_a):
        raise RuntimeError("gh down")
    monkeypatch.setattr(gc, "_json", _boom)
    gc.invalidate("sprint_labels_live:")  # force a fresh (failing) fetch
    nums = gc.list_sprints("owner/repo")
    assert nums == [66]
