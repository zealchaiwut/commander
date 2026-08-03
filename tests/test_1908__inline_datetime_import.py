"""Tests for issue #1908: replace inline __import__("datetime") with module-level import.

Acceptance Criteria (from issue body):
- AC1: _sprint_set_conflict_blocked stores a valid ISO-8601 UTC timestamp in the "at" field.
- AC2: The timestamp uses the module-level datetime/timezone import (no inline __import__).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import startup  # noqa: E402


# ── AC1: _sprint_set_conflict_blocked produces a valid ISO-8601 UTC timestamp ──

def test_set_conflict_blocked_stores_iso_timestamp(tmp_path):
    """AC1: the 'at' field is a valid ISO-8601 string with UTC offset."""
    from datetime import datetime

    project_root = tmp_path
    sprint_label = "sprint-9999"
    # Create the .commander/sprints/<label> directory so _write_plan_json can write
    plan_dir = project_root / ".commander" / "sprints" / sprint_label
    plan_dir.mkdir(parents=True)

    startup._sprint_set_conflict_blocked(project_root, sprint_label, ["SCHEMA.md"])

    plan = startup._read_plan_json(project_root, sprint_label)
    assert plan is not None, "plan.json should exist after _sprint_set_conflict_blocked"
    assert "conflict_blocked" in plan
    at_value = plan["conflict_blocked"]["at"]

    # Must parse as a valid datetime
    parsed = datetime.fromisoformat(at_value)
    # Must carry UTC offset information
    assert parsed.tzinfo is not None, "'at' timestamp must be timezone-aware"
    assert parsed.utcoffset().total_seconds() == 0, "'at' timestamp must be UTC (offset 0)"


# ── AC2: no inline __import__ in _sprint_set_conflict_blocked source ──────────

def test_no_inline_import_in_set_conflict_blocked():
    """AC2: _sprint_set_conflict_blocked must not use __import__ for datetime."""
    src = inspect.getsource(startup._sprint_set_conflict_blocked)
    assert "__import__" not in src, (
        "_sprint_set_conflict_blocked must not use inline __import__; "
        "use the module-level datetime/timezone import instead"
    )
