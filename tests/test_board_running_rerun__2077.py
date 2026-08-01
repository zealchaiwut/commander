"""Tests for bug #2077: running rerun sub-sprint invisible on board's Running tab.

AC coverage:
  AC1  running chain in running section  — active member with lifecycle_state=="running"
         routes the chain card into sections.running, not sections.lineage
  AC2  non-running chain unchanged       — a chain whose latest member is NOT running
         still collapses into sections.lineage (existing lineage behavior untouched)
  AC3  behavioral test                   — seeds DB rows with state='running' + parent_label
         (2-member chain), calls assemble_board, asserts card lands in sections.running
"""
from __future__ import annotations

import importlib
import importlib.util
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

_PROJECT = "owner/repo"


# ── Issue factory ─────────────────────────────────────────────────────────────

def _issue(number: int, title: str, labels: list[str], state: str = "open") -> dict:
    return {
        "number": number,
        "title": title,
        "state": state,
        "body": "## Acceptance Criteria\n- [ ] AC",
        "labels": [{"name": l} for l in labels],
        "html_url": f"https://github.com/owner/repo/issues/{number}",
    }


# ── Fixtures: 2-member chain with RUNNING latest ──────────────────────────────

_ISSUES_RUNNING_CHAIN: list[dict] = [
    # Base sprint (sprint-10) — finished needs_rework, triggered rerun
    _issue(100, "Base ticket", ["sprint-10", "needs-rework"]),
    # Rerun sub-sprint (sprint-10.1) — currently running
    _issue(101, "Rerun ticket", ["sprint-10.1", "in-progress"]),
]

_LC_ROWS_RUNNING_CHAIN: list[dict] = [
    {
        "label": "sprint-10",
        "project": _PROJECT,
        "state": "needs_rework",
        "parent_label": None,
        "run_ingested_at": "2026-01-01",
    },
    {
        "label": "sprint-10.1",
        "project": _PROJECT,
        "state": "running",
        "parent_label": "sprint-10",
        "run_ingested_at": None,
    },
]

_LIFECYCLE_RUNNING_CHAIN: dict[str, str] = {
    "sprint-10": "needs_rework",
    "sprint-10.1": "running",
}

# ── Fixtures: 2-member chain with COMPLETED latest (AC2 — lineage unchanged) ──

_ISSUES_FINISHED_CHAIN: list[dict] = [
    _issue(200, "Base ticket", ["sprint-20", "needs-rework"]),
    _issue(201, "Rerun ticket", ["sprint-20.1", "UAT"]),
]

_LC_ROWS_FINISHED_CHAIN: list[dict] = [
    {
        "label": "sprint-20",
        "project": _PROJECT,
        "state": "needs_rework",
        "parent_label": None,
        "run_ingested_at": "2026-01-01",
    },
    {
        "label": "sprint-20.1",
        "project": _PROJECT,
        "state": "needs_rework",
        "parent_label": "sprint-20",
        "run_ingested_at": "2026-01-01",
    },
]

_LIFECYCLE_FINISHED_CHAIN: dict[str, str] = {
    "sprint-20": "needs_rework",
    "sprint-20.1": "needs_rework",
}


# ── Board builder helper ───────────────────────────────────────────────────────

def _build_board(issues: list[dict], lc_rows: list[dict], lifecycle: dict[str, str]) -> dict:
    """Call assemble_board with mocked deps and the given fixture data."""
    mock_db = MagicMock()
    mock_db.list_sprints_lifecycle.return_value = lc_rows
    mock_db.get_sprint.return_value = None

    def _canonical(raw):
        from db import canonical_lifecycle
        return canonical_lifecycle(raw)

    mock_db.canonical_lifecycle.side_effect = _canonical

    mock_gc = MagicMock()
    mock_gc.cached_open_issues_with_body.return_value = list(issues)
    mock_gc.get_repo_for_operation.return_value = _PROJECT

    def _classify(iss):
        label_names = {l["name"] if isinstance(l, dict) else l for l in iss.get("labels", [])}
        if label_names & {"in-progress", "sit", "UAT", "UAT-approved", "done", "released", "needs-rework"}:
            return "done" if "UAT-approved" in label_names or "released" in label_names else "in-progress"
        return "backlog"

    mock_gc.classify_issue.side_effect = _classify

    mock_ss = MagicMock()
    mock_ss.current.side_effect = lambda label, project=None: lifecycle.get(label, "unknown")

    mock_run_stats = MagicMock()
    mock_run_stats.sprint_run_stats.return_value = {
        "label": "x",
        "has_runs": False,
        "split": [],
        "tickets": [],
    }

    mock_server = MagicMock()
    mock_server._DAG_BUILDER_AVAILABLE = False
    mock_server._build_dag = None
    mock_server.build_effective_response.return_value = {}
    mock_server._settings_repo = MagicMock()
    mock_server._settings_repo.get_setting.return_value = {}
    mock_server.APP_CONFIG_KEY = "app_config"

    if "board_service" in sys.modules:
        bs = sys.modules["board_service"]
    else:
        spec = importlib.util.find_spec("board_service")
        bs = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(bs)  # type: ignore[union-attr]
        sys.modules["board_service"] = bs

    with (
        patch.object(bs, "db", mock_db),
        patch.object(bs, "github_client", mock_gc),
        patch.object(bs, "_run_stats_service", return_value=mock_run_stats),
        patch.dict(sys.modules, {"sprint_state": mock_ss}),
        patch.object(bs, "_server", return_value=mock_server),
    ):
        return bs.assemble_board(_PROJECT)


# ── AC3 / AC1: Running chain card lands in sections.running ──────────────────

def test_running_rerun_chain_appears_in_running_section():
    """2-member chain with running latest appears in sections.running (AC1, AC3)."""
    board = _build_board(
        _ISSUES_RUNNING_CHAIN, _LC_ROWS_RUNNING_CHAIN, _LIFECYCLE_RUNNING_CHAIN
    )
    running_labels = {c["label"] for c in board["sections"]["running"]}
    assert "sprint-10.1" in running_labels, (
        f"sprint-10.1 (running rerun) not in sections.running; "
        f"running={running_labels}, "
        f"lineage={[c.get('label') for c in board['sections']['lineage']]}"
    )


def test_running_rerun_chain_card_has_chain_field():
    """Card in sections.running for a running rerun chain still carries the chain[] field (AC1)."""
    board = _build_board(
        _ISSUES_RUNNING_CHAIN, _LC_ROWS_RUNNING_CHAIN, _LIFECYCLE_RUNNING_CHAIN
    )
    running = board["sections"]["running"]
    chain_card = next((c for c in running if c.get("label") == "sprint-10.1"), None)
    assert chain_card is not None, "Expected sprint-10.1 card in sections.running"
    assert "chain" in chain_card, "Running rerun chain card is missing chain[] field"
    assert "sprint-10" in chain_card["chain"], (
        f"Parent sprint-10 missing from chain field: {chain_card['chain']}"
    )
    assert "sprint-10.1" in chain_card["chain"], (
        f"sprint-10.1 missing from chain field: {chain_card['chain']}"
    )


def test_running_rerun_chain_not_in_lineage():
    """A chain whose latest member is running does NOT appear in sections.lineage (AC1)."""
    board = _build_board(
        _ISSUES_RUNNING_CHAIN, _LC_ROWS_RUNNING_CHAIN, _LIFECYCLE_RUNNING_CHAIN
    )
    lineage_labels = {c["label"] for c in board["sections"]["lineage"]}
    assert "sprint-10.1" not in lineage_labels, (
        f"sprint-10.1 (running) should not be in lineage; lineage={lineage_labels}"
    )


def test_running_rerun_card_has_running_lifecycle_state():
    """The running rerun chain card in sections.running has lifecycle_state=='running' (AC1)."""
    board = _build_board(
        _ISSUES_RUNNING_CHAIN, _LC_ROWS_RUNNING_CHAIN, _LIFECYCLE_RUNNING_CHAIN
    )
    running = board["sections"]["running"]
    chain_card = next((c for c in running if c.get("label") == "sprint-10.1"), None)
    assert chain_card is not None
    assert chain_card["lifecycle_state"] == "running", (
        f"Expected lifecycle_state='running', got {chain_card.get('lifecycle_state')!r}"
    )


# ── AC2: Non-running chain collapses into lineage unchanged ──────────────────

def test_non_running_chain_stays_in_lineage():
    """A 2-member chain whose latest is NOT running still collapses into sections.lineage (AC2)."""
    board = _build_board(
        _ISSUES_FINISHED_CHAIN, _LC_ROWS_FINISHED_CHAIN, _LIFECYCLE_FINISHED_CHAIN
    )
    lineage_labels = {c["label"] for c in board["sections"]["lineage"]}
    # The latest active member sprint-20.1 should be the card label in lineage
    assert "sprint-20.1" in lineage_labels, (
        f"sprint-20.1 (non-running rerun) should be in lineage; lineage={lineage_labels}"
    )


def test_non_running_chain_not_in_running_section():
    """A finished rerun chain does NOT appear in sections.running (AC2)."""
    board = _build_board(
        _ISSUES_FINISHED_CHAIN, _LC_ROWS_FINISHED_CHAIN, _LIFECYCLE_FINISHED_CHAIN
    )
    running_labels = {c["label"] for c in board["sections"]["running"]}
    assert "sprint-20" not in running_labels, (
        f"sprint-20 (non-running) should not be in running; running={running_labels}"
    )
    assert "sprint-20.1" not in running_labels, (
        f"sprint-20.1 (non-running) should not be in running; running={running_labels}"
    )


def test_non_running_chain_lineage_card_has_chain_field():
    """The lineage entry for a non-running chain still carries chain[] with both members (AC2)."""
    board = _build_board(
        _ISSUES_FINISHED_CHAIN, _LC_ROWS_FINISHED_CHAIN, _LIFECYCLE_FINISHED_CHAIN
    )
    lineage = board["sections"]["lineage"]
    chain_card = next((c for c in lineage if "sprint-20" in (c.get("chain") or [])), None)
    assert chain_card is not None, "Expected lineage entry for sprint-20 chain"
    assert "sprint-20" in chain_card["chain"]
    assert "sprint-20.1" in chain_card["chain"]
