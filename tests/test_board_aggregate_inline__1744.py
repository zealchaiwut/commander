"""Tests for issue #1744: Board aggregate flag — inline remaining per-sprint fan-out.

AC coverage:
  AC1  — _build_sprint_card returns cards with outcome, goal, conflicts, finish_card,
          branch_status fields inline
  AC2  — With flag ON, the four per-sprint loaders consume inline data (zero fetch)
  AC3  — With flag OFF, behavior unchanged (legacy fields only)
  AC4  — Behavioral: assemble_board returns all inline fields; branch_status makes
          zero subprocess calls (gh); per-sprint handlers not invoked by board service
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
_BOARD_RENDER = _DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js"
_BUNDLE = _DASHBOARD_ROOT / "static" / "dist" / "bundle.js"

for _p in (str(_DASHBOARD_ROOT), str(_DASHBOARD_ROOT / "routers"), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Fixture data ──────────────────────────────────────────────────────────────

_PROJECT = "owner/testrepo"


def _issue(number: int, title: str, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "body": "## Acceptance Criteria\n- [ ] AC",
        "labels": [{"name": l} for l in labels],
        "html_url": f"https://github.com/owner/testrepo/issues/{number}",
    }


_ISSUES: list[dict] = [
    # Draft sprint with two backlog tickets (will generate a conflict if they share files)
    _issue(10, "Ticket A", ["sprint-1", "enhancement"]),
    _issue(11, "Ticket B", ["sprint-1", "size-S"]),
    # Running sprint
    _issue(20, "Running ticket", ["sprint-2", "in-progress"]),
    # Needs-rework sprint (has run)
    _issue(30, "Rework ticket", ["sprint-3", "needs-rework"]),
    # Backlog (no sprint)
    _issue(60, "Backlog ticket", ["enhancement"]),
]

_LIFECYCLE_ROWS: list[dict] = [
    {"label": "sprint-1", "project": _PROJECT, "state": "draft",        "parent_label": None, "run_ingested_at": None},
    {"label": "sprint-2", "project": _PROJECT, "state": "running",      "parent_label": None, "run_ingested_at": "2026-01-01T00:00:00"},
    {"label": "sprint-3", "project": _PROJECT, "state": "needs_rework", "parent_label": None, "run_ingested_at": "2026-01-01T00:00:00"},
]

_LIFECYCLE: dict[str, str] = {
    "sprint-1": "draft",
    "sprint-2": "running",
    "sprint-3": "needs_rework",
}

# Canned DB row for sprint-3 (has run)
_SPRINT_3_DB_ROW = {
    "label": "sprint-3",
    "project": _PROJECT,
    "state": "needs_rework",
    "run_ingested_at": "2026-01-01T00:00:00",
    "end_reason": "stopped_by_user",
    "issues_json": json.dumps([
        {"ticket_id": 30, "title": "Rework ticket", "state": "open", "agent_status": "failed", "failure_reason": "timeout"},
    ]),
    "wall_clock_secs": 120,
    "summary_issue_url": None,
    "summary_issue_num": None,
    "pr_number": None,
    "parent_label": None,
}


# ── Mock factories ─────────────────────────────────────────────────────────────

def _make_mock_db(sprint3_row=_SPRINT_3_DB_ROW):
    mock_db = MagicMock()
    mock_db.list_sprints_lifecycle.return_value = list(_LIFECYCLE_ROWS)

    def _get_sprint(label, project=None):
        if label == "sprint-3":
            return sprint3_row
        return None

    mock_db.get_sprint.side_effect = _get_sprint

    def _canonical(raw):
        from db import canonical_lifecycle
        return canonical_lifecycle(raw)

    mock_db.canonical_lifecycle.side_effect = _canonical
    return mock_db


def _make_mock_github():
    mock_gc = MagicMock()
    mock_gc.cached_open_issues_with_body.return_value = list(_ISSUES)

    def _classify(iss):
        label_names = {l["name"] if isinstance(l, dict) else l for l in iss.get("labels", [])}
        if label_names & {"in-progress", "sit", "UAT", "UAT-approved", "done", "needs-rework"}:
            return "in-progress"
        return "backlog"

    mock_gc.classify_issue.side_effect = _classify
    return mock_gc


def _make_mock_sprint_state():
    mock_ss = MagicMock()
    mock_ss.current.side_effect = lambda label, project=None: _LIFECYCLE.get(label, "unknown")
    return mock_ss


def _build_board(tmp_path: Path | None = None) -> dict:
    """Import board_service fresh and call assemble_board with mocked deps."""
    mock_db = _make_mock_db()
    mock_gc = _make_mock_github()
    mock_ss = _make_mock_sprint_state()

    if "board_service" in sys.modules:
        bs = sys.modules["board_service"]
    else:
        spec = importlib.util.find_spec("board_service")
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        sys.modules["board_service"] = bs

    mock_run_stats = MagicMock()
    mock_run_stats.sprint_run_stats.return_value = {
        "label": "x", "has_runs": False, "split": [], "tickets": [],
    }

    mock_server = MagicMock()
    mock_server._DAG_BUILDER_AVAILABLE = False
    mock_server._build_dag = None
    mock_server.build_effective_response.return_value = {}
    mock_server._settings_repo = MagicMock()
    mock_server._settings_repo.get_setting.return_value = {}
    mock_server.APP_CONFIG_KEY = "app_config"

    # Use a temp sprints dir so goal reads don't touch the filesystem unpredictably
    sprints_dir = tmp_path / ".commander" / "sprints" if tmp_path else None

    with (
        patch.object(bs, "db", mock_db),
        patch.object(bs, "github_client", mock_gc),
        patch.object(bs, "_run_stats_service", return_value=mock_run_stats),
        patch.dict(sys.modules, {"sprint_state": mock_ss}),
        patch.object(bs, "_server", return_value=mock_server),
        patch.object(bs, "_sprints_dir", return_value=sprints_dir or Path("/nonexistent/sprints")),
    ):
        return bs.assemble_board(_PROJECT)


def _collect_sprint_cards(board: dict) -> list[dict]:
    """Return all non-backlog sprint cards."""
    cards: list[dict] = []
    for key in ("running", "needs_rework", "ready_to_merge", "draft", "lineage"):
        cards.extend(board["sections"].get(key, []))
    return cards


# ── AC1: _build_sprint_card returns all new inline fields ─────────────────────

def test_ac1_sprint_cards_have_goal_field(tmp_path):
    """Every sprint card must include a 'goal' field (AC1)."""
    board = _build_board(tmp_path)
    cards = _collect_sprint_cards(board)
    assert cards, "Expected at least one sprint card"
    for card in cards:
        assert "goal" in card, f"Card {card.get('label')} missing 'goal' field"


def test_ac1_sprint_cards_have_outcome_field(tmp_path):
    """Every sprint card must include an 'outcome' field (AC1)."""
    board = _build_board(tmp_path)
    cards = _collect_sprint_cards(board)
    assert cards
    for card in cards:
        assert "outcome" in card, f"Card {card.get('label')} missing 'outcome' field"


def test_ac1_sprint_cards_have_conflicts_field(tmp_path):
    """Every sprint card must include a 'conflicts' field (AC1)."""
    board = _build_board(tmp_path)
    cards = _collect_sprint_cards(board)
    assert cards
    for card in cards:
        assert "conflicts" in card, f"Card {card.get('label')} missing 'conflicts' field"


def test_ac1_sprint_cards_have_finish_card_field(tmp_path):
    """Every sprint card must include a 'finish_card' field (AC1)."""
    board = _build_board(tmp_path)
    cards = _collect_sprint_cards(board)
    assert cards
    for card in cards:
        assert "finish_card" in card, f"Card {card.get('label')} missing 'finish_card' field"


def test_ac1_sprint_cards_have_branch_status_field(tmp_path):
    """Every sprint card must include a 'branch_status' field (AC1)."""
    board = _build_board(tmp_path)
    cards = _collect_sprint_cards(board)
    assert cards
    for card in cards:
        assert "branch_status" in card, f"Card {card.get('label')} missing 'branch_status' field"


def test_ac1_goal_is_empty_string_when_no_file(tmp_path):
    """goal is '' when .commander/sprints/<label>-goal.txt doesn't exist (AC1)."""
    board = _build_board(tmp_path)
    for card in _collect_sprint_cards(board):
        assert card["goal"] == "", f"Expected empty goal for {card.get('label')}"


def test_ac1_goal_reads_from_file(tmp_path):
    """goal is populated from the goal file when it exists (AC1)."""
    import importlib, importlib.util
    mock_db = _make_mock_db()
    mock_gc = _make_mock_github()
    mock_ss = _make_mock_sprint_state()

    if "board_service" in sys.modules:
        bs = sys.modules["board_service"]
    else:
        spec = importlib.util.find_spec("board_service")
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        sys.modules["board_service"] = bs

    # Write goal file for sprint-1
    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    (sprints_dir / "sprint-1-goal.txt").write_text("Ship the widget", encoding="utf-8")

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
        patch.object(bs, "_sprints_dir", return_value=sprints_dir),
    ):
        board = bs.assemble_board(_PROJECT)

    draft_cards = board["sections"]["draft"]
    sprint1 = next((c for c in draft_cards if c["label"] == "sprint-1"), None)
    assert sprint1 is not None
    assert sprint1["goal"] == "Ship the widget"


def test_ac1_outcome_none_for_unrun_sprint(tmp_path):
    """outcome is None for a sprint that hasn't been run yet (AC1)."""
    board = _build_board(tmp_path)
    draft_cards = board["sections"]["draft"]
    sprint1 = next((c for c in draft_cards if c["label"] == "sprint-1"), None)
    assert sprint1 is not None
    assert sprint1["outcome"] is None


def test_ac1_outcome_has_state_for_run_sprint(tmp_path):
    """outcome dict has state field for a sprint that has run (AC1)."""
    board = _build_board(tmp_path)
    rework_cards = board["sections"]["needs_rework"]
    sprint3 = next((c for c in rework_cards if c["label"] == "sprint-3"), None)
    assert sprint3 is not None
    assert sprint3["outcome"] is not None
    assert "state" in sprint3["outcome"]


def test_ac1_outcome_running_for_running_sprint(tmp_path):
    """outcome.state == 'running' for a currently-running sprint (AC1)."""
    board = _build_board(tmp_path)
    running_cards = board["sections"]["running"]
    sprint2 = next((c for c in running_cards if c["label"] == "sprint-2"), None)
    assert sprint2 is not None
    assert sprint2["outcome"] is not None
    assert sprint2["outcome"]["state"] == "running"


def test_ac1_conflicts_has_expected_shape(tmp_path):
    """conflicts field has 'conflicts' list and 'pending_count' (AC1)."""
    board = _build_board(tmp_path)
    for card in _collect_sprint_cards(board):
        c = card["conflicts"]
        assert isinstance(c, dict), f"conflicts must be dict for {card.get('label')}"
        assert "conflicts" in c, f"conflicts must have 'conflicts' key for {card.get('label')}"
        assert "pending_count" in c, f"conflicts must have 'pending_count' for {card.get('label')}"
        assert isinstance(c["conflicts"], list)


def test_ac1_conflicts_entries_have_titles(tmp_path):
    """Conflict entries include ticket1_title and ticket2_title (AC1)."""
    # Create a scenario with shared files by patching estimate reads
    import importlib, importlib.util
    mock_db = _make_mock_db()
    mock_gc = _make_mock_github()
    mock_ss = _make_mock_sprint_state()

    if "board_service" in sys.modules:
        bs = sys.modules["board_service"]
    else:
        spec = importlib.util.find_spec("board_service")
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        sys.modules["board_service"] = bs

    sprints_dir = tmp_path / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    est_dir = tmp_path / ".commander" / "estimates"
    est_dir.mkdir(parents=True)
    # Both tickets touch the same file
    (est_dir / "issue-10.json").write_text(json.dumps({"size": "S", "files_likely_affected": ["src/foo.py"]}))
    (est_dir / "issue-11.json").write_text(json.dumps({"size": "S", "files_likely_affected": ["src/foo.py"]}))

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
        patch.object(bs, "_sprints_dir", return_value=sprints_dir),
        patch.object(bs, "_estimates_dir", return_value=est_dir),
    ):
        board = bs.assemble_board(_PROJECT)

    draft_cards = board["sections"]["draft"]
    sprint1 = next((c for c in draft_cards if c["label"] == "sprint-1"), None)
    assert sprint1 is not None
    conflicts = sprint1["conflicts"]["conflicts"]
    assert len(conflicts) == 1
    c = conflicts[0]
    assert "ticket1_title" in c, "conflict entry must include ticket1_title"
    assert "ticket2_title" in c, "conflict entry must include ticket2_title"
    assert "shared_files" in c


def test_ac1_finish_card_no_data_for_unrun_sprint(tmp_path):
    """finish_card.state == 'no_data' for a sprint that hasn't run (AC1)."""
    board = _build_board(tmp_path)
    draft_cards = board["sections"]["draft"]
    sprint1 = next((c for c in draft_cards if c["label"] == "sprint-1"), None)
    assert sprint1 is not None
    assert sprint1["finish_card"]["state"] == "no_data"


def test_ac1_finish_card_has_state_for_run_sprint(tmp_path):
    """finish_card for a run sprint has a non-no_data state (AC1)."""
    board = _build_board(tmp_path)
    rework_cards = board["sections"]["needs_rework"]
    sprint3 = next((c for c in rework_cards if c["label"] == "sprint-3"), None)
    assert sprint3 is not None
    fc = sprint3["finish_card"]
    assert fc["state"] != "no_data", f"Expected non-no_data finish_card, got {fc}"


def test_ac1_branch_status_has_exists_field(tmp_path):
    """branch_status always has 'exists' and 'branch' keys (AC1)."""
    board = _build_board(tmp_path)
    for card in _collect_sprint_cards(board):
        bs_data = card["branch_status"]
        assert "exists" in bs_data, f"branch_status missing 'exists' for {card.get('label')}"
        assert "branch" in bs_data, f"branch_status missing 'branch' for {card.get('label')}"


def test_ac1_branch_status_has_false_exists(tmp_path):
    """branch_status.exists is False in aggregate (no gh call) (AC1)."""
    board = _build_board(tmp_path)
    for card in _collect_sprint_cards(board):
        assert card["branch_status"]["exists"] is False


def test_ac1_branch_name_matches_sprint_label(tmp_path):
    """branch_status.branch is 'sprint/<label>' (AC1)."""
    board = _build_board(tmp_path)
    for card in _collect_sprint_cards(board):
        label = card["label"]
        assert card["branch_status"]["branch"] == f"sprint/{label}"


# ── AC2/AC4: Behavioral — zero per-sprint requests from board service ──────────

def test_ac4_no_subprocess_calls_during_assemble_board(tmp_path):
    """assemble_board makes zero subprocess calls (no gh CLI) (AC4)."""
    with patch("subprocess.run") as mock_run:
        _build_board(tmp_path)
    mock_run.assert_not_called()


def test_ac4_branch_status_not_fetched_via_sprint_run_handler(tmp_path):
    """get_sprint_branch_status handler not called during board assembly (AC4).

    The per-sprint /branch-status endpoint uses subprocess.run (gh CLI) —
    board_service must not call it. We verify via subprocess.run call count
    (already zero in the prior test) and by checking board_service never
    imports or calls sprint_run.get_sprint_branch_status.
    """
    import importlib, importlib.util
    if "board_service" in sys.modules:
        bs = sys.modules["board_service"]
    else:
        spec = importlib.util.find_spec("board_service")
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        sys.modules["board_service"] = bs

    # board_service must not reference get_sprint_branch_status anywhere
    import inspect
    src = inspect.getsource(bs)
    assert "get_sprint_branch_status" not in src, (
        "board_service must not call get_sprint_branch_status — "
        "branch status must be computed inline without gh CLI"
    )


def test_ac4_all_new_fields_present_means_no_per_sprint_fetch_needed(tmp_path):
    """assemble_board returns all 5 new inline fields on every card (AC4).

    When each card carries goal/outcome/conflicts/finish_card/branch_status,
    the JS aggregate loaders can skip their per-sprint network requests entirely.
    This test verifies the invariant that makes zero-fetch possible.
    """
    board = _build_board(tmp_path)
    cards = _collect_sprint_cards(board)
    assert cards, "Need at least one card to test inline fields"
    for card in cards:
        for field in ("goal", "outcome", "conflicts", "finish_card", "branch_status"):
            assert field in card, (
                f"Card {card.get('label')!r} missing '{field}' — "
                "aggregate shortcut cannot work without inline data"
            )


# ── AC3: Flag OFF — legacy card fields unchanged ───────────────────────────────

def test_ac3_legacy_card_fields_still_present(tmp_path):
    """Cards still carry the original 6 fields (AC3 — legacy path unchanged)."""
    board = _build_board(tmp_path)
    for card in _collect_sprint_cards(board):
        for field in ("label", "lifecycle_state", "tickets", "mini_rail", "dep_order", "estimate_hours", "run_stats"):
            assert field in card, f"Legacy field '{field}' missing from card {card.get('label')}"


# ── JS source: aggregate guard in all four loaders ────────────────────────────

def _board_render_src() -> str:
    return _BOARD_RENDER.read_text(encoding="utf-8")


def test_ac2_smgmt_fetch_missing_outcomes_has_aggregate_guard():
    """_smgmtFetchMissingOutcomes checks _smgmtAggregateCards and skips fetch (AC2)."""
    src = _board_render_src()
    # Find the function body
    fn_start = src.find("export async function _smgmtFetchMissingOutcomes")
    assert fn_start != -1
    # Find the next exported function after it
    fn_end = src.find("\nexport ", fn_start + 10)
    if fn_end == -1:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "_smgmtAggregateCards" in fn_body, (
        "_smgmtFetchMissingOutcomes must check _smgmtAggregateCards for aggregate shortcut"
    )
    assert "/outcome" not in fn_body.split("_smgmtAggregateCards")[0], (
        "outcome fetch must not appear before the _smgmtAggregateCards guard"
    )


def test_ac2_smgmt_load_goals_has_aggregate_guard():
    """_smgmtLoadGoals checks _smgmtAggregateCards and skips fetch (AC2)."""
    src = _board_render_src()
    fn_start = src.find("export async function _smgmtLoadGoals")
    assert fn_start != -1
    fn_end = src.find("\nexport ", fn_start + 10)
    if fn_end == -1:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "_smgmtAggregateCards" in fn_body, (
        "_smgmtLoadGoals must check _smgmtAggregateCards for aggregate shortcut"
    )


def test_ac2_smgmt_load_conflicts_has_aggregate_guard():
    """_smgmtLoadConflicts checks _smgmtAggregateCards and skips fetch (AC2)."""
    src = _board_render_src()
    fn_start = src.find("export async function _smgmtLoadConflicts")
    assert fn_start != -1
    fn_end = src.find("\nexport ", fn_start + 10)
    if fn_end == -1:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "_smgmtAggregateCards" in fn_body, (
        "_smgmtLoadConflicts must check _smgmtAggregateCards for aggregate shortcut"
    )


def test_ac2_smgmt_load_finish_cards_has_aggregate_guard():
    """_smgmtLoadFinishCards checks _smgmtAggregateCards and skips fetch (AC2)."""
    src = _board_render_src()
    fn_start = src.find("export async function _smgmtLoadFinishCards")
    assert fn_start != -1
    fn_end = src.find("\nexport ", fn_start + 10)
    if fn_end == -1:
        fn_end = len(src)
    fn_body = src[fn_start:fn_end]
    assert "_smgmtAggregateCards" in fn_body, (
        "_smgmtLoadFinishCards must check _smgmtAggregateCards for aggregate shortcut"
    )


def test_ac2_bundle_contains_aggregate_guards_in_all_loaders():
    """Rebuilt bundle contains _smgmtAggregateCards check near all four loader names (AC2)."""
    bundle = _BUNDLE.read_text(encoding="utf-8")
    for fn_name in (
        "_smgmtFetchMissingOutcomes",
        "_smgmtLoadGoals",
        "_smgmtLoadConflicts",
        "_smgmtLoadFinishCards",
    ):
        assert fn_name in bundle, f"bundle must contain {fn_name}"
    # The aggregate cards variable must also be in the bundle (ensures guard compiled in)
    assert "_smgmtAggregateCards" in bundle
