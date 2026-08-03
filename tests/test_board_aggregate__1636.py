"""Tests for the Board aggregate endpoint — issue #1636.

AC coverage:
  AC9  model shape        — response has all required top-level keys
  AC10 inline fields      — every non-backlog card has the 6 required fields non-null
  AC11 section bucketing  — draft/running/needs_rework/ready_to_merge land in correct sections
  AC12 lineage collapse   — a rerun chain appears as a single entry in sections.lineage
  AC13 zero gh calls      — no subprocess call to gh is made during board assembly
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_SERVICES_ROOT = Path(__file__).resolve().parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_DASHBOARD_ROOT / "routers"), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_PROJECT = "owner/repo"

# Minimal issue factory
def _issue(number: int, title: str, labels: list[str], state: str = "open") -> dict:
    return {
        "number": number,
        "title": title,
        "state": state,
        "body": "## Acceptance Criteria\n- [ ] AC",
        "labels": [{"name": l} for l in labels],
        "html_url": f"https://github.com/owner/repo/issues/{number}",
    }


# Issues fixture: covers draft, running, needs-rework, ready-to-merge, rerun chain,
# and backlog (unassigned).
_ISSUES: list[dict] = [
    # Draft sprint (sprint-1 tickets, sprint is new/unrun)
    _issue(10, "Draft ticket A", ["sprint-1", "enhancement"]),
    _issue(11, "Draft ticket B", ["sprint-1", "size-S"]),
    # Running sprint (sprint-2)
    _issue(20, "Running ticket A", ["sprint-2", "in-progress"]),
    _issue(21, "Running ticket B", ["sprint-2", "in-progress"]),
    # Needs-rework sprint (sprint-3)
    _issue(30, "Rework ticket", ["sprint-3", "needs-rework"]),
    # Ready-to-merge sprint (sprint-4)
    _issue(40, "Merge ticket", ["sprint-4", "UAT"]),
    # Rerun chain — base sprint (sprint-5) + child (sprint-5.1); both finished,
    # so the chain collapses into lineage (non-running chain — tests AC12 intent)
    _issue(50, "Base sprint ticket", ["sprint-5", "needs-rework"]),
    _issue(51, "Child sprint ticket", ["sprint-5.1", "needs-rework"]),
    # Backlog (no sprint label)
    _issue(60, "Unassigned ticket", ["enhancement"]),
    _issue(61, "Another backlog ticket", ["size-M"]),
]

# Lifecycle DB rows
_LIFECYCLE_ROWS: list[dict] = [
    {"label": "sprint-1", "project": _PROJECT, "state": "draft", "parent_label": None, "run_ingested_at": None},
    {"label": "sprint-2", "project": _PROJECT, "state": "running", "parent_label": None, "run_ingested_at": "2026-01-01"},
    {"label": "sprint-3", "project": _PROJECT, "state": "needs_rework", "parent_label": None, "run_ingested_at": "2026-01-01"},
    {"label": "sprint-4", "project": _PROJECT, "state": "ready_to_merge", "parent_label": None, "run_ingested_at": "2026-01-01"},
    {"label": "sprint-5", "project": _PROJECT, "state": "needs_rework", "parent_label": None, "run_ingested_at": "2026-01-01"},
    {"label": "sprint-5.1", "project": _PROJECT, "state": "needs_rework", "parent_label": "sprint-5", "run_ingested_at": "2026-01-01"},
]


def _make_mock_db(rows: list[dict] = _LIFECYCLE_ROWS):
    """Return a mock db module wired to the fixture rows."""
    mock_db = MagicMock()
    mock_db.list_sprints_lifecycle.return_value = rows
    mock_db.get_sprint.return_value = None

    def _canonical(raw):
        from db import canonical_lifecycle  # real function
        return canonical_lifecycle(raw)

    mock_db.canonical_lifecycle.side_effect = _canonical
    return mock_db


def _make_mock_github():
    """Return a mock github_client that returns fixture issues without gh calls."""
    mock_gc = MagicMock()
    mock_gc.cached_open_issues_with_body.return_value = list(_ISSUES)
    mock_gc.get_repo_for_operation.return_value = _PROJECT

    def _classify(iss):
        label_names = {l["name"] if isinstance(l, dict) else l for l in iss.get("labels", [])}
        if label_names & {"in-progress", "sit", "UAT", "UAT-approved", "done", "released", "needs-rework"}:
            return "done" if "UAT-approved" in label_names or "released" in label_names else "in-progress"
        return "backlog"

    mock_gc.classify_issue.side_effect = _classify
    return mock_gc


def _make_mock_sprint_state(lifecycle: dict[str, str]):
    """Return a mock sprint_state module with canned lifecycle answers."""
    mock_ss = MagicMock()
    mock_ss.current.side_effect = lambda label, project=None: lifecycle.get(label, "unknown")
    return mock_ss


_LIFECYCLE: dict[str, str] = {
    "sprint-1": "draft",
    "sprint-2": "running",
    "sprint-3": "needs_rework",
    "sprint-4": "ready_to_merge",
    "sprint-5": "needs_rework",
    "sprint-5.1": "needs_rework",
}


# ── Helper ────────────────────────────────────────────────────────────────────

def _build_board(extra_db_patches: dict | None = None) -> dict:
    """Import board_service fresh and call assemble_board with mocked deps."""
    mock_db = _make_mock_db()
    mock_gc = _make_mock_github()
    mock_ss = _make_mock_sprint_state(_LIFECYCLE)

    patches: dict[str, Any] = {
        "board_service.db": mock_db,
        "board_service.github_client": mock_gc,
        "sprint_state": mock_ss,
    }
    if extra_db_patches:
        patches.update(extra_db_patches)

    # Import (or re-import) fresh
    if "board_service" in sys.modules:
        bs = sys.modules["board_service"]
    else:
        spec = importlib.util.find_spec("board_service")
        bs = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(bs)  # type: ignore[union-attr]
        sys.modules["board_service"] = bs

    mock_run_stats = MagicMock()
    mock_run_stats.sprint_run_stats.return_value = {"label": "x", "has_runs": False, "split": [], "tickets": []}

    mock_server = MagicMock()
    mock_server._DAG_BUILDER_AVAILABLE = False
    mock_server._build_dag = None
    mock_server.build_effective_response.return_value = {}
    mock_server._settings_repo = MagicMock()
    mock_server._settings_repo.get_setting.return_value = {}
    mock_server.APP_CONFIG_KEY = "app_config"

    with (
        patch.object(bs, "db", mock_db),
        patch.object(bs, "github_client", mock_gc),
        patch.object(bs, "_run_stats_service", return_value=mock_run_stats),
        patch.dict(sys.modules, {"sprint_state": mock_ss}),
        patch.object(bs, "_server", return_value=mock_server),
    ):
        return bs.assemble_board(_PROJECT)


# ── AC9: Model shape ──────────────────────────────────────────────────────────

def test_board_model_shape__top_level_keys():
    """Response carries all required top-level keys (AC9)."""
    board = _build_board()
    required = {"project", "generated_at", "sections", "capacity", "summaries"}
    assert required.issubset(board.keys()), f"Missing keys: {required - board.keys()}"


def test_board_model_shape__sections_keys():
    """sections object has all required section keys (AC9)."""
    board = _build_board()
    sections = board["sections"]
    required = {"running", "needs_rework", "ready_to_merge", "draft", "lineage", "backlog"}
    assert required.issubset(sections.keys()), f"Missing section keys: {required - sections.keys()}"


def test_board_model_shape__backlog_shape():
    """backlog section has count and tickets[] (AC9)."""
    board = _build_board()
    backlog = board["sections"]["backlog"]
    assert "count" in backlog
    assert "tickets" in backlog
    assert isinstance(backlog["tickets"], list)


def test_board_model_shape__project_field():
    """project field matches the input project (AC9)."""
    board = _build_board()
    assert board["project"] == _PROJECT


def test_board_model_shape__generated_at_is_iso():
    """generated_at is a valid ISO-8601 timestamp (AC9)."""
    board = _build_board()
    ts = board["generated_at"]
    from datetime import datetime
    datetime.fromisoformat(ts)  # raises if invalid


# ── AC10: Inline fields ───────────────────────────────────────────────────────

_REQUIRED_CARD_FIELDS = {"lifecycle_state", "tickets", "mini_rail", "dep_order", "estimate_hours", "run_stats"}


def _collect_non_backlog_cards(board: dict) -> list[dict]:
    """Return all sprint cards from non-backlog sections."""
    cards: list[dict] = []
    sections = board["sections"]
    for key in ("running", "needs_rework", "ready_to_merge", "draft", "lineage"):
        cards.extend(sections.get(key, []))
    return cards


def test_inline_fields__all_cards_have_required_fields():
    """Every non-backlog card carries all 6 required inline fields (AC10)."""
    board = _build_board()
    cards = _collect_non_backlog_cards(board)
    assert cards, "Expected at least one non-backlog sprint card"
    for card in cards:
        missing = _REQUIRED_CARD_FIELDS - card.keys()
        assert not missing, f"Card {card.get('label')} missing fields: {missing}"


def test_inline_fields__fields_are_not_none():
    """Each required inline field is non-null on every card (AC10)."""
    board = _build_board()
    for card in _collect_non_backlog_cards(board):
        for field in _REQUIRED_CARD_FIELDS:
            assert card[field] is not None, (
                f"Card {card.get('label')} has null {field!r}"
            )


def test_inline_fields__mini_rail_has_levels():
    """mini_rail includes at least a levels key (AC10)."""
    board = _build_board()
    for card in _collect_non_backlog_cards(board):
        assert "levels" in card["mini_rail"], f"mini_rail missing levels on {card.get('label')}"


def test_inline_fields__dep_order_has_dep_hints():
    """dep_order includes a dep_hints key (AC10)."""
    board = _build_board()
    for card in _collect_non_backlog_cards(board):
        assert "dep_hints" in card["dep_order"], f"dep_order missing dep_hints on {card.get('label')}"


# ── AC11: Section bucketing ───────────────────────────────────────────────────

def test_section_bucketing__running_sprint_in_running_section():
    """sprint-2 (running lifecycle) appears in sections.running (AC11)."""
    board = _build_board()
    labels = {c["label"] for c in board["sections"]["running"]}
    assert "sprint-2" in labels, f"sprint-2 not in running; running={labels}"


def test_section_bucketing__needs_rework_sprint_in_rework_section():
    """sprint-3 (needs_rework lifecycle) appears in sections.needs_rework (AC11)."""
    board = _build_board()
    labels = {c["label"] for c in board["sections"]["needs_rework"]}
    assert "sprint-3" in labels, f"sprint-3 not in needs_rework; rework={labels}"


def test_section_bucketing__ready_to_merge_in_correct_section():
    """sprint-4 (ready_to_merge lifecycle) appears in sections.ready_to_merge (AC11)."""
    board = _build_board()
    labels = {c["label"] for c in board["sections"]["ready_to_merge"]}
    assert "sprint-4" in labels, f"sprint-4 not in ready_to_merge; rtm={labels}"


def test_section_bucketing__draft_sprint_in_draft_section():
    """sprint-1 (draft lifecycle) appears in sections.draft (AC11)."""
    board = _build_board()
    labels = {c["label"] for c in board["sections"]["draft"]}
    assert "sprint-1" in labels, f"sprint-1 not in draft; draft={labels}"


def test_section_bucketing__backlog_has_unassigned_issues():
    """Unassigned issues end up in sections.backlog (AC11)."""
    board = _build_board()
    backlog = board["sections"]["backlog"]
    assert backlog["count"] >= 2
    backlog_nums = {t["number"] for t in backlog["tickets"]}
    assert 60 in backlog_nums
    assert 61 in backlog_nums


# ── AC12: Lineage collapse ────────────────────────────────────────────────────

def test_lineage_collapse__rerun_chain_is_single_entry():
    """sprint-5 + sprint-5.1 appear as ONE entry in sections.lineage (AC12)."""
    board = _build_board()
    lineage = board["sections"]["lineage"]
    assert len(lineage) >= 1, "Expected at least one lineage entry"

    # Find the entry covering the sprint-5 chain
    chain_entry = next(
        (e for e in lineage if "sprint-5" in (e.get("chain") or [])),
        None,
    )
    assert chain_entry is not None, (
        f"No lineage entry with sprint-5 in chain; lineage={[e.get('label') for e in lineage]}"
    )
    assert len(chain_entry["chain"]) >= 2, (
        f"Expected ≥2 sprints in chain, got {chain_entry['chain']}"
    )


def test_lineage_collapse__chain_not_in_other_sections():
    """Sprints in a rerun chain do not appear individually in other sections (AC12)."""
    board = _build_board()
    sections = board["sections"]
    chain_labels = {"sprint-5", "sprint-5.1"}
    for section_key in ("running", "needs_rework", "ready_to_merge", "draft"):
        section_labels = {c.get("label") for c in sections.get(section_key, [])}
        overlap = chain_labels & section_labels
        assert not overlap, (
            f"Chain labels {overlap} appeared individually in sections.{section_key}"
        )


def test_lineage_collapse__chain_contains_both_members():
    """The lineage entry's chain[] includes both sprint-5 and sprint-5.1 (AC12)."""
    board = _build_board()
    lineage = board["sections"]["lineage"]
    chain_entry = next(
        (e for e in lineage if "sprint-5" in (e.get("chain") or [])),
        None,
    )
    assert chain_entry is not None
    assert "sprint-5.1" in chain_entry["chain"], (
        f"sprint-5.1 missing from chain: {chain_entry['chain']}"
    )


# ── AC13: Zero gh calls ───────────────────────────────────────────────────────

def test_zero_gh_calls__no_subprocess_run_called():
    """subprocess.run is never called during board assembly (AC13).

    This guards against accidental ``gh`` invocations anywhere in the call stack
    (board_service, run_stats_service, or anything they import).
    """
    import board_service as bs

    mock_db = _make_mock_db()
    mock_gc = _make_mock_github()
    mock_ss = _make_mock_sprint_state(_LIFECYCLE)
    mock_run_stats = MagicMock()
    mock_run_stats.sprint_run_stats.return_value = {"label": "x", "has_runs": False, "split": [], "tickets": []}
    mock_server = MagicMock()
    mock_server._DAG_BUILDER_AVAILABLE = False
    mock_server._build_dag = None
    mock_server.build_effective_response.return_value = {}
    mock_server._settings_repo = MagicMock()
    mock_server._settings_repo.get_setting.return_value = {}
    mock_server.APP_CONFIG_KEY = "app_config"

    with (
        patch.object(bs, "db", mock_db),
        patch.object(bs, "github_client", mock_gc),
        patch.object(bs, "_run_stats_service", return_value=mock_run_stats),
        patch.dict(sys.modules, {"sprint_state": mock_ss}),
        patch.object(bs, "_server", return_value=mock_server),
        patch("subprocess.run") as mock_sp_run,
        patch("subprocess.check_output") as mock_sp_co,
    ):
        bs.assemble_board(_PROJECT)

    assert mock_sp_run.call_count == 0, (
        f"subprocess.run called {mock_sp_run.call_count} time(s) — expected 0 gh calls"
    )
    assert mock_sp_co.call_count == 0, (
        f"subprocess.check_output called {mock_sp_co.call_count} time(s) — expected 0 gh calls"
    )
