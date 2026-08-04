"""Regression tests for issue #2166: sprint JSON write guard in conftest.py.

AC2: conftest.py-level guard detects sprint JSON files written to the real
     .commander/sprints/ directory and fails the offending test loudly.
AC3: Behavioral test — exercises the real startup._sprint_json_write path;
     no source-regex checks (per issue #1746).

The test imports the guard helpers from conftest and calls
startup._sprint_json_write against a simulated sprints directory to verify
detection without touching the real production directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Import guard helpers exported by conftest — loaded by pytest before tests run.
from conftest import _SPRINT_JSON_RE, _check_leaked_sprint_files  # noqa: E402


def _snapshot(directory: Path) -> frozenset:
    """Return a frozenset of sprint JSON Paths in directory."""
    if not directory.exists():
        return frozenset()
    return frozenset(
        p for p in directory.iterdir()
        if p.is_file() and _SPRINT_JSON_RE.match(p.name)
    )


# ── AC3: behavioral — exercises the real write path ──────────────────────────

def test_guard_detects_unmocked_sprint_json_write(tmp_path):
    """AC3: _check_leaked_sprint_files raises AssertionError on an unredirected write.

    Simulates a test that calls startup._sprint_json_write into a directory that
    represents the production sprints dir (tmp_path here).  The REAL
    _sprint_json_write function is called — no mock.  Then _check_leaked_sprint_files
    is called with the before/after snapshots and must raise AssertionError,
    proving the guard's detection logic is behavioral.
    """
    import startup

    simulated_sprints_dir = tmp_path / ".commander" / "sprints"
    simulated_sprints_dir.mkdir(parents=True)

    before = _snapshot(simulated_sprints_dir)

    # Call the REAL _sprint_json_write — no mocking of the write itself.
    target = simulated_sprints_dir / "sprint-9999.json"
    startup._sprint_json_write(
        target,
        {
            "label": "sprint-9999",
            "goal": "guard behavioral test",
            "project": "owner/repo",
            "status": "pending",
            "tickets": [],
        },
    )

    # Confirm the real write happened (this is what the guard is protecting against).
    assert target.exists(), (
        "startup._sprint_json_write must have written sprint-9999.json — "
        "if it did not, the behavioral test is not exercising the real write path"
    )

    after = _snapshot(simulated_sprints_dir)

    # The guard helper must detect the new file and raise AssertionError.
    with pytest.raises(AssertionError, match="SAFETY ABORT"):
        _check_leaked_sprint_files(before, after)


def test_guard_also_catches_plan_json(tmp_path):
    """AC3: guard detects sprint-*-plan.json files as well as sprint-*.json."""
    import startup

    simulated_sprints_dir = tmp_path / ".commander" / "sprints"
    simulated_sprints_dir.mkdir(parents=True)

    before = _snapshot(simulated_sprints_dir)

    plan_target = simulated_sprints_dir / "sprint-42-plan.json"
    startup._sprint_json_write(plan_target, {"state": "draft"})

    after = _snapshot(simulated_sprints_dir)

    with pytest.raises(AssertionError, match="SAFETY ABORT"):
        _check_leaked_sprint_files(before, after)


# ── AC2: guard is silent when writes go to correctly isolated tmp_path ─────────

def test_guard_silent_when_write_goes_to_isolated_tmp(tmp_path):
    """AC2: guard does NOT trigger when sprint JSON is written to an isolated dir.

    Correctly mocked tests write to their own tmp_path subtree.  A write there
    must not cause _check_leaked_sprint_files to raise when the 'real' sprints
    directory (a different tmp_path subdir here) remains unchanged.
    """
    import startup

    # The "real" sprints dir — stays empty.
    real_sprints_dir = tmp_path / "real" / ".commander" / "sprints"
    real_sprints_dir.mkdir(parents=True)

    # A correctly isolated dir — where the properly mocked test writes.
    isolated_dir = tmp_path / "isolated" / ".commander" / "sprints"
    isolated_dir.mkdir(parents=True)

    before = _snapshot(real_sprints_dir)

    # Write to the ISOLATED dir (correct behavior for a well-isolated test).
    startup._sprint_json_write(
        isolated_dir / "sprint-1.json",
        {"label": "sprint-1", "goal": "isolated", "project": "owner/repo",
         "status": "pending", "tickets": []},
    )

    after = _snapshot(real_sprints_dir)  # snapshot the "real" dir — still empty

    # Guard must NOT raise — the write stayed in the isolated dir.
    _check_leaked_sprint_files(before, after)


def test_guard_silent_when_no_sprint_files_written(tmp_path):
    """AC2: guard is silent when before and after snapshots are identical."""
    simulated_sprints_dir = tmp_path / ".commander" / "sprints"
    simulated_sprints_dir.mkdir(parents=True)

    before = _snapshot(simulated_sprints_dir)
    after = _snapshot(simulated_sprints_dir)  # same — no writes

    # Must not raise.
    _check_leaked_sprint_files(before, after)
