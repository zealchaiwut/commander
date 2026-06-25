"""Tests for issue #702 — bulk "New sprint" number uses finished-summary high-water mark.

Bug: _resolve_bulk_sprint_label counted only live GitHub sprint-N labels.  When a
sprint finishes, its label is deleted, so the max reset to 0 → "NEW" created
sprint-1 instead of the real next number (e.g. sprint-54).

Fix: _next_new_sprint_number now maxes over BOTH live sprint labels AND finished-
sprint summary numbers (parsed from "Sprint N Executive Summary" issue titles).

AC coverage:
  AC1  — when live labels are empty but summaries have Sprint 53, next number is 54
  AC2  — when live labels have sprint-10 but summaries have Sprint 53, result is 54
  AC3  — when live labels have sprint-53 but summaries have Sprint 40, result is 54
  AC4  — sub-sprint summaries (Sprint 96.2) contribute base number 96
  AC5  — _resolve_bulk_sprint_label("NEW", ...) returns sprint-54 when summary HWM is 53
  AC6  — summary titles that don't match the pattern are skipped
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _summary_issue(num: int, sprint: str) -> dict:
    """Build a minimal summary issue dict as returned by list_summary_issues."""
    return {"number": num, "title": f"Sprint {sprint} Executive Summary", "url": ""}


def _patch_github(srv, live_sprint_nums, summary_issues):
    """Context manager that patches list_sprints, get_repo_for_operation, and
    list_summary_issues together."""
    return (
        patch.object(srv.github_client, "list_sprints", return_value=live_sprint_nums),
        patch.object(srv.github_client, "get_repo_for_operation", return_value="owner/repo"),
        patch.object(srv.github_client, "list_summary_issues", return_value=summary_issues),
    )


# ── Fixture ──────────────────────────────────────────────────────────────────

import pytest


@pytest.fixture()
def srv():
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as _srv
    return _srv


# ── AC1: summaries win when live labels are empty ─────────────────────────────

def test_next_number_uses_summary_when_live_labels_deleted(srv):
    """AC1 — sprint-N label is deleted after finish; summaries carry the HWM."""
    summaries = [_summary_issue(1500, "53")]
    p1, p2, p3 = _patch_github(srv, [], summaries)
    with p1, p2, p3:
        result = srv._next_new_sprint_number("owner/repo")
    assert result == 54


# ── AC2: max of live labels and summaries ─────────────────────────────────────

def test_next_number_takes_max_of_live_and_summaries_summary_wins(srv):
    """AC2 — summaries have higher number than live labels."""
    summaries = [_summary_issue(1500, "53"), _summary_issue(1499, "52")]
    p1, p2, p3 = _patch_github(srv, [1, 2, 10], summaries)
    with p1, p2, p3:
        result = srv._next_new_sprint_number("owner/repo")
    assert result == 54


# ── AC3: live labels win when higher than summaries ───────────────────────────

def test_next_number_takes_max_of_live_and_summaries_live_wins(srv):
    """AC3 — live sprint labels have higher number than summaries."""
    summaries = [_summary_issue(1400, "40")]
    p1, p2, p3 = _patch_github(srv, [1, 53], summaries)
    with p1, p2, p3:
        result = srv._next_new_sprint_number("owner/repo")
    assert result == 54


# ── AC4: sub-sprint labels contribute base sprint number ─────────────────────

def test_next_number_strips_sub_sprint_suffix(srv):
    """AC4 — 'Sprint 96.2 Executive Summary' contributes base number 96."""
    summaries = [
        _summary_issue(1600, "96.2"),
        _summary_issue(1601, "96.1"),
    ]
    p1, p2, p3 = _patch_github(srv, [], summaries)
    with p1, p2, p3:
        result = srv._next_new_sprint_number("owner/repo")
    assert result == 97


# ── AC5: _resolve_bulk_sprint_label("NEW") respects summary HWM ──────────────

def test_resolve_new_sprint_uses_summary_high_water_mark(srv):
    """AC5 — NEW resolves to sprint-54 when live labels are empty but HWM is 53."""
    summaries = [_summary_issue(1500, "53")]
    with patch.object(srv.github_client, "list_sprints", return_value=[]), \
         patch.object(srv.github_client, "get_repo_for_operation", return_value="owner/repo"), \
         patch.object(srv.github_client, "list_summary_issues", return_value=summaries), \
         patch.object(srv.github_client, "ensure_sprint_label") as es:
        result = srv._resolve_bulk_sprint_label("NEW", "owner/repo")
    assert result == "sprint-54"
    es.assert_called_once_with(54, repo_name="owner/repo")


# ── AC6: non-matching summary titles are skipped ─────────────────────────────

def test_next_number_skips_non_summary_titles(srv):
    """AC6 — issues that don't match 'Sprint N Executive Summary' are ignored."""
    summaries = [
        {"number": 1, "title": "Sprint Review Notes", "url": ""},
        {"number": 2, "title": "Some other issue", "url": ""},
        {"number": 3, "title": "Sprint 10 Executive Summary", "url": ""},
    ]
    p1, p2, p3 = _patch_github(srv, [], summaries)
    with p1, p2, p3:
        result = srv._next_new_sprint_number("owner/repo")
    assert result == 11
