"""Test github_client.list_merged_sprint_branches (board finished-via-merge signal)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))

import github_client as gc  # noqa: E402


def test_returns_merged_head_ref_names(monkeypatch):
    gc._cache.clear()
    monkeypatch.setattr(gc, "_json", lambda *a: [
        {"headRefName": "sprint/sprint-9"},
        {"headRefName": "sprint/sprint-10.1"},
        {"headRefName": "feature/x"},
    ])
    heads = gc.list_merged_sprint_branches("owner/repo")
    assert heads == {"sprint/sprint-9", "sprint/sprint-10.1", "feature/x"}
    assert "sprint/sprint-9" in heads


def test_empty_set_on_gh_error(monkeypatch):
    gc._cache.clear()

    def boom(*a):
        raise RuntimeError("gh failed")

    monkeypatch.setattr(gc, "_json", boom)
    assert gc.list_merged_sprint_branches("owner/repo") == set()


def test_result_is_cached(monkeypatch):
    gc._cache.clear()
    calls = {"n": 0}

    def counting(*a):
        calls["n"] += 1
        return [{"headRefName": "sprint/sprint-1"}]

    monkeypatch.setattr(gc, "_json", counting)
    gc.list_merged_sprint_branches("owner/repo")
    gc.list_merged_sprint_branches("owner/repo")
    assert calls["n"] == 1  # second call served from cache
