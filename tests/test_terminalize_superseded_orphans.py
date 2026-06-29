"""Tests for _terminalize_superseded_orphans — orphan queued rework children.

A rework child written at run-end (plan.json needs_rework/queued) but never
dispatched has no DB row. When a strictly-later sibling in the same lineage has
completed, the orphan is superseded and should be terminalized; otherwise (still
pending re-run, or it actually ran) it must be left alone.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

from apps.dashboard.routers import sprint_reconcile_service as rs  # noqa: E402


def _sprints_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".commander" / "sprints"
    d.mkdir(parents=True)
    return d


def _plan(d: Path, label: str, state: str, end_reason: str) -> None:
    (d / f"{label}-plan.json").write_text(
        json.dumps({"state": state, "end_reason": end_reason, "tickets": [1]}),
        encoding="utf-8",
    )


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def list_sprints_lifecycle(self):
        return self._rows

    def get_sprint(self, label, project=None):
        return next((r for r in self._rows if r["label"] == label), None)


def _run(tmp_path, rows):
    with patch.object(rs, "_db", return_value=_FakeDB(rows)), \
            patch("server._project_root_path", return_value=tmp_path):
        return rs._terminalize_superseded_orphans("o/r")


def test_superseded_orphan_is_terminalized(tmp_path):
    d = _sprints_dir(tmp_path)
    _plan(d, "sprint-90.3", "needs_rework", "queued")
    rows = [{"label": "sprint-90.4", "project": "o/r", "state": "completed"}]
    out = _run(tmp_path, rows)
    assert "sprint-90.3" in out
    data = json.loads((d / "sprint-90.3-plan.json").read_text())
    assert data["state"] == "completed"
    assert data["end_reason"] == "superseded"


def test_pending_orphan_left_alone(tmp_path):
    """91.1 case: no later sibling completed → it's a pending re-run, untouched."""
    d = _sprints_dir(tmp_path)
    _plan(d, "sprint-91.1", "needs_rework", "queued")
    rows = [{"label": "sprint-91", "project": "o/r", "state": "ready_to_merge"}]
    out = _run(tmp_path, rows)
    assert out == []
    data = json.loads((d / "sprint-91.1-plan.json").read_text())
    assert data["state"] == "needs_rework"


def test_orphan_that_actually_ran_is_skipped(tmp_path):
    """A state.json means it dispatched — not an orphan, leave it for DB reconcile."""
    d = _sprints_dir(tmp_path)
    _plan(d, "sprint-90.3", "needs_rework", "queued")
    (d / "sprint-90.3-state.json").write_text("{}", encoding="utf-8")
    rows = [{"label": "sprint-90.4", "project": "o/r", "state": "completed"}]
    out = _run(tmp_path, rows)
    assert out == []
    assert json.loads((d / "sprint-90.3-plan.json").read_text())["state"] == "needs_rework"


def test_earlier_completed_sibling_does_not_supersede(tmp_path):
    """Only a STRICTLY later sibling supersedes; an earlier completed one must not."""
    d = _sprints_dir(tmp_path)
    _plan(d, "sprint-90.3", "needs_rework", "queued")
    rows = [{"label": "sprint-90.2", "project": "o/r", "state": "completed"}]
    out = _run(tmp_path, rows)
    assert out == []


def test_parse_sprint_label():
    assert rs._parse_sprint_label("sprint-90.3") == ("sprint-90", (90, 3))
    assert rs._parse_sprint_label("sprint-90") == ("sprint-90", (90,))
    assert rs._parse_sprint_label("garbage") == ("", ())
