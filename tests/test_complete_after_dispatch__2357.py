"""Behavioral AC tests for complete-after-dispatch (issue #2357)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.complete_after_dispatch import (  # noqa: E402
    complete_after_dispatch,
    find_successful_sprint_pr,
)


def _write_done_dispatch(root: Path, *, pr: int | None, run_id: str = "r1"):
    runtime = root / ".commander" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": run_id,
        "sprint_label": "sprint-1030",
        "tickets": [1, 2],
        "repo": "owner/repo",
        "status": "done",
        "sprint_pr_number": pr,
        "finished_at": "2026-09-02T12:00:00+00:00",
        "started_at": "2026-09-02T11:00:00+00:00",
        "outcomes": [],
        "remaining": [],
    }
    (runtime / f"dispatch-{run_id}.json").write_text(json.dumps(data), encoding="utf-8")


def test_finds_successful_sprint_pr(tmp_path):
    _write_done_dispatch(tmp_path, pr=99)
    info = find_successful_sprint_pr(tmp_path, "sprint-1030", "owner/repo")
    assert info is not None
    assert info["sprint_pr_number"] == 99
    assert info["sprint_pr_url"].endswith("/pull/99")


def test_preview_calls_neither_finish_nor_merge(tmp_path):
    _write_done_dispatch(tmp_path, pr=42)
    calls = {"finish": 0, "merge": 0}

    def finish_fn(**kw):
        calls["finish"] += 1
        return {"ok": True}

    def merge_pr_fn(url, repo):
        calls["merge"] += 1
        return True, "merged"

    result = complete_after_dispatch(
        project_root=tmp_path,
        sprint_label="sprint-1030",
        repo="owner/repo",
        preview=True,
        uat_signoff=True,
        finish_fn=finish_fn,
        merge_pr_fn=merge_pr_fn,
        list_uat_fn=lambda repo_name=None: [
            {"number": 7, "title": "UAT ticket"},
        ],
    )
    assert result["preview"] is True
    assert result["ok"] is True
    assert result["sprint_pr"]["number"] == 42
    assert result["uat_tickets"] == [{"number": 7, "title": "UAT ticket"}]
    assert calls == {"finish": 0, "merge": 0}


def test_uat_signoff_true_calls_finish_not_bare_merge(tmp_path):
    _write_done_dispatch(tmp_path, pr=42)
    order = []

    def finish_fn(**kw):
        order.append(("finish", kw["sprint_pr_url"]))
        return {"closed": 2}

    def merge_pr_fn(url, repo):
        order.append(("merge", url))
        return True, "merged"

    result = complete_after_dispatch(
        project_root=tmp_path,
        sprint_label="sprint-1030",
        repo="owner/repo",
        preview=False,
        uat_signoff=True,
        finish_fn=finish_fn,
        merge_pr_fn=merge_pr_fn,
        list_uat_fn=lambda **k: [],
    )
    assert result["ok"] is True
    assert result["merged"] is True
    assert order == [("finish", "https://github.com/owner/repo/pull/42")]


def test_no_pr_returns_not_ok(tmp_path):
    _write_done_dispatch(tmp_path, pr=None)
    result = complete_after_dispatch(
        project_root=tmp_path,
        sprint_label="sprint-1030",
        repo="owner/repo",
        preview=False,
        uat_signoff=False,
        merge_pr_fn=lambda *a: (True, "x"),
    )
    assert result["ok"] is False
    assert result["reason"] == "no_sprint_pr"
