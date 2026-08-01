"""Behavioral tests for issue #2061: zombie ready_to_merge sprint cards with 0 tickets.

AC coverage:
  AC1  0-ticket post-run sprint gets stale_no_tickets flag (action row suppressed by frontend)
  AC4  draft sprint with 0 tickets remains unaffected (runnable draft must not be suppressed)
  AC5  ready_to_merge with 0 tickets → stale_no_tickets True
  AC5  running sprint with 0 dispatched tickets → not stale
  AC5  ready_to_merge sprint WITH tickets → not stale
  AC5  needs_rework sprint with 0 tickets → stale_no_tickets True
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Path setup ────────────────────────────────────────────────────────────────

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent / "apps" / "dashboard"
_SERVICES_ROOT = Path(__file__).resolve().parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_DASHBOARD_ROOT / "routers"), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Fixtures ──────────────────────────────────────────────────────────────────

_PROJECT = "owner/repo"


def _issue(number: int, title: str, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "body": "## Acceptance Criteria\n- [ ] AC",
        "labels": [{"name": lbl} for lbl in labels],
        "html_url": f"https://github.com/owner/repo/issues/{number}",
    }


def _make_mock_db(rows: list[dict]):
    mock_db = MagicMock()
    mock_db.list_sprints_lifecycle.return_value = rows
    mock_db.get_sprint.return_value = None

    def _canonical(raw):
        from db import canonical_lifecycle  # real function
        return canonical_lifecycle(raw)

    mock_db.canonical_lifecycle.side_effect = _canonical
    return mock_db


def _make_mock_github(issues: list[dict]):
    mock_gc = MagicMock()
    mock_gc.cached_open_issues_with_body.return_value = list(issues)
    mock_gc.get_repo_for_operation.return_value = _PROJECT
    return mock_gc


def _make_mock_sprint_state(lifecycle: dict[str, str]):
    mock_ss = MagicMock()
    mock_ss.current.side_effect = lambda label, project=None: lifecycle.get(label, "unknown")
    return mock_ss


def _build_board(issues: list[dict], lifecycle_rows: list[dict], lifecycle_map: dict[str, str]) -> dict:
    """Import board_service fresh and call assemble_board with mocked deps."""
    mock_db = _make_mock_db(lifecycle_rows)
    mock_gc = _make_mock_github(issues)
    mock_ss = _make_mock_sprint_state(lifecycle_map)

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


# ── AC5: ready_to_merge with 0 tickets → stale_no_tickets True ───────────────

def test_ac5_zero_ticket_ready_to_merge_sprint_is_stale():
    """A ready_to_merge sprint with 0 tickets must carry stale_no_tickets: True (AC5)."""
    # No issues for sprint-4 — zombie state
    issues = [_issue(10, "Other ticket", ["sprint-1"])]
    rows = [
        {"label": "sprint-1", "project": _PROJECT, "state": "draft", "parent_label": None, "run_ingested_at": None},
        {"label": "sprint-4", "project": _PROJECT, "state": "ready_to_merge", "parent_label": None, "run_ingested_at": "2026-01-01"},
    ]
    lifecycle = {"sprint-1": "draft", "sprint-4": "ready_to_merge"}

    board = _build_board(issues, rows, lifecycle)
    rtm_cards = board["sections"]["ready_to_merge"]
    sprint4_cards = [c for c in rtm_cards if c["label"] == "sprint-4"]
    assert sprint4_cards, "sprint-4 must appear in ready_to_merge section"
    card = sprint4_cards[0]
    assert card.get("stale_no_tickets") is True, (
        f"Expected stale_no_tickets=True on sprint-4 (0 tickets, ready_to_merge); got {card.get('stale_no_tickets')!r}"
    )


# ── AC5: running sprint with 0 dispatched tickets is unaffected ──────────────

def test_ac5_running_sprint_zero_tickets_not_stale():
    """A running sprint with 0 tickets must NOT get stale_no_tickets (AC5)."""
    issues: list[dict] = []  # no tickets on sprint-2
    rows = [
        {"label": "sprint-2", "project": _PROJECT, "state": "running", "parent_label": None, "run_ingested_at": "2026-01-01"},
    ]
    lifecycle = {"sprint-2": "running"}

    board = _build_board(issues, rows, lifecycle)
    running_cards = board["sections"]["running"]
    sprint2_cards = [c for c in running_cards if c["label"] == "sprint-2"]
    assert sprint2_cards, "sprint-2 must appear in running section"
    card = sprint2_cards[0]
    assert not card.get("stale_no_tickets"), (
        "Running sprint must NOT have stale_no_tickets set"
    )


# ── AC5: real ready_to_merge sprint with tickets keeps actions ────────────────

def test_ac5_ready_to_merge_sprint_with_tickets_not_stale():
    """A ready_to_merge sprint WITH tickets must NOT get stale_no_tickets (AC5)."""
    issues = [_issue(40, "Merge ticket", ["sprint-4", "UAT"])]
    rows = [
        {"label": "sprint-4", "project": _PROJECT, "state": "ready_to_merge", "parent_label": None, "run_ingested_at": "2026-01-01"},
    ]
    lifecycle = {"sprint-4": "ready_to_merge"}

    board = _build_board(issues, rows, lifecycle)
    rtm_cards = board["sections"]["ready_to_merge"]
    sprint4_cards = [c for c in rtm_cards if c["label"] == "sprint-4"]
    assert sprint4_cards, "sprint-4 must appear in ready_to_merge section"
    card = sprint4_cards[0]
    assert not card.get("stale_no_tickets"), (
        "ready_to_merge sprint WITH tickets must NOT have stale_no_tickets"
    )


# ── AC4: empty but runnable draft sprint must remain unaffected ───────────────

def test_ac4_draft_sprint_zero_tickets_not_stale():
    """An empty draft sprint must NOT get stale_no_tickets — it's runnable (AC4)."""
    # sprint-1 is a draft with 0 tickets
    issues: list[dict] = []
    rows = [
        {"label": "sprint-1", "project": _PROJECT, "state": "draft", "parent_label": None, "run_ingested_at": None},
    ]
    lifecycle = {"sprint-1": "draft"}

    board = _build_board(issues, rows, lifecycle)
    # The board may not show sprint-1 at all (no DB row + no issues → skipped),
    # but if it does appear it must NOT be stale.
    draft_cards = board["sections"]["draft"]
    sprint1_cards = [c for c in draft_cards if c["label"] == "sprint-1"]
    for card in sprint1_cards:
        assert not card.get("stale_no_tickets"), (
            "Empty draft sprint must NOT have stale_no_tickets — it remains runnable"
        )


# ── AC5: needs_rework sprint with 0 tickets → stale_no_tickets True ──────────

def test_ac5_needs_rework_sprint_zero_tickets_is_stale():
    """A needs_rework sprint with 0 tickets must carry stale_no_tickets: True (AC5)."""
    issues: list[dict] = []  # tickets already moved to child sprint
    rows = [
        {"label": "sprint-3", "project": _PROJECT, "state": "needs_rework", "parent_label": None, "run_ingested_at": "2026-01-01"},
    ]
    lifecycle = {"sprint-3": "needs_rework"}

    board = _build_board(issues, rows, lifecycle)
    rework_cards = board["sections"]["needs_rework"]
    sprint3_cards = [c for c in rework_cards if c["label"] == "sprint-3"]
    assert sprint3_cards, "sprint-3 must appear in needs_rework section"
    card = sprint3_cards[0]
    assert card.get("stale_no_tickets") is True, (
        f"Expected stale_no_tickets=True on sprint-3 (0 tickets, needs_rework); got {card.get('stale_no_tickets')!r}"
    )
