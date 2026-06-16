"""P1: board hides locally signed-off sprints without Executive Summary."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


def test_locally_signed_off_includes_merge_sprint_completed(tmp_path):
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    project_root = tmp_path / "proj"
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    plan = {
        "state": "completed",
        "end_reason": "merge_sprint",
        "tickets": [],
    }
    (sprints_dir / "sprint-79-plan.json").write_text(json.dumps(plan), encoding="utf-8")

    labels = srv._locally_signed_off_sprint_labels(project_root)
    assert "sprint-79" in labels


def test_parse_pr_number_from_url():
    import server as srv

    assert srv._parse_pr_number_from_url("https://github.com/o/r/pull/1159") == 1159
    assert srv._parse_pr_number_from_url(None) is None
