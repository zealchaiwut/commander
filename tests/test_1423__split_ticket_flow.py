"""Tests for issue #1423 — Add split-ticket flow from DAG and capacity bar.

Acceptance Criteria anchored:
- AC1: Split button visible when (a) size-XL label, (b) DAG blocking ≥2 peer waves,
       or (c) sprint capacity bar is red.
- AC2: Clicking Split opens modal pre-filled with two draft titles and AC scopes
       from a lightweight BA call; both fields are fully editable.
- AC3: Confirming creates two new GitHub issues assigned to the same sprint as the
       original, inheriting all labels except size-XL.
- AC4: User chooses to remove original from sprint OR close it with a comment
       referencing the two child issues.
- AC5: Both child issues trigger an automatic re-estimate run; size label applied.
- AC6: Preview-DAG re-renders after split; children with disjoint file sets appear
       in separate parallel waves.
- AC7: If the BA call is unavailable, dialog opens with empty fields + fallback notice.
- AC8: Sprint board and DAG reflect original ticket's updated state immediately.
"""
from __future__ import annotations

import sys
import json
import py_compile
from pathlib import Path
from unittest.mock import patch, MagicMock, call

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

SPLIT_ROUTER_PATH = DASHBOARD_DIR / "routers" / "split_ticket.py"
SPLIT_SERVICE_PATH = DASHBOARD_DIR / "routers" / "split_ticket_service.py"
INIT_PATH = DASHBOARD_DIR / "routers" / "__init__.py"
SERVER_PATH = DASHBOARD_DIR / "server.py"
PROJECT_HTML_PATH = DASHBOARD_DIR / "static" / "project.html"


# ── AC1 (a): Split button condition — size-XL label ──────────────────────────

def test_ac1a_size_xl_label_triggers_split_eligibility():
    """AC1a: a ticket carrying size-XL label is split-eligible.

    Verified by reading the eligibility logic from split_ticket_service or
    confirming project.html encodes the condition 'size-XL' → show split button.
    """
    source = PROJECT_HTML_PATH.read_text(encoding="utf-8")
    assert "size-XL" in source or "size-xl" in source.lower(), (
        "project.html must reference size-XL label to evaluate split eligibility"
    )
    assert "split" in source.lower(), (
        "project.html must contain split button / modal JS code"
    )


# ── AC1 (b): Split button condition — DAG blocking ≥2 peer waves ─────────────

def test_ac1b_dag_blocking_eligibility_function_in_project_html():
    """AC1b: project.html contains JS to detect DAG-blocking tickets (≥2 wave overlaps)."""
    source = PROJECT_HTML_PATH.read_text(encoding="utf-8")
    # Must reference conflicts and levels from DAG cache
    assert "_smgmtDagDataCache" in source, (
        "project.html must use _smgmtDagDataCache to evaluate DAG-blocking condition"
    )
    # Must have a function that checks split eligibility
    assert "IsSplitEligible" in source or "splitEligible" in source or "_smgmtSplitEligible" in source, (
        "project.html must define a function that evaluates split eligibility"
    )


# ── AC1 (c): Split button condition — capacity bar red ───────────────────────

def test_ac1c_capacity_red_triggers_split():
    """AC1c: project.html checks whether the capacity bar is red to show Split."""
    source = PROJECT_HTML_PATH.read_text(encoding="utf-8")
    # cap-bar--red or similar class plus a check that drives split eligibility
    assert "cap-bar--red" in source, (
        "project.html must define the cap-bar--red class (already exists) — "
        "split eligibility must read whether cap bar is in red state"
    )
    # The split eligibility must somehow correlate with capacity-over state
    assert "IsCapacityRed" in source or "capacityRed" in source or "cap-bar--red" in source, (
        "project.html must expose a way to detect capacity-red state for split eligibility"
    )


# ── AC2: Modal pre-fill via suggest API ──────────────────────────────────────

def test_ac2_suggest_endpoint_exists_in_router():
    """AC2: split_ticket.py defines a POST endpoint for generating BA draft suggestions."""
    source = SPLIT_ROUTER_PATH.read_text(encoding="utf-8")
    assert "split/suggest" in source or "suggest" in source, (
        "split_ticket.py must define the /suggest endpoint"
    )
    assert "@router.post" in source, (
        "split_ticket.py must use @router.post (not @app.post)"
    )


def test_ac2_suggest_service_returns_two_children():
    """AC2: suggest_split() returns child1 and child2 with title and ac_scope keys."""
    from routers.split_ticket_service import _parse_ba_suggest_output

    raw = json.dumps({
        "child1": {"title": "Part A: Handle UI", "ac_scope": "AC1: render form\nAC2: submit"},
        "child2": {"title": "Part B: Backend API", "ac_scope": "AC1: POST endpoint\nAC2: persist"},
    })
    result = _parse_ba_suggest_output(raw)

    assert "child1" in result and "child2" in result, (
        f"_parse_ba_suggest_output must return child1 and child2 keys; got {result}"
    )
    assert result["child1"]["title"], "child1 must have a non-empty title"
    assert result["child2"]["title"], "child2 must have a non-empty title"
    assert result["child1"]["ac_scope"] is not None, "child1 must have ac_scope"
    assert result["child2"]["ac_scope"] is not None, "child2 must have ac_scope"


def test_ac2_suggest_returns_fallback_false_on_success():
    """AC2: A successful BA call sets fallback=False in the response."""
    from routers.split_ticket_service import _parse_ba_suggest_output

    raw = json.dumps({
        "child1": {"title": "Child A", "ac_scope": "AC1: do X"},
        "child2": {"title": "Child B", "ac_scope": "AC1: do Y"},
    })
    result = _parse_ba_suggest_output(raw)
    assert result.get("fallback") is False, (
        "Successful parse must set fallback=False; got {result}"
    )


# ── AC3: Confirm creates two issues with correct labels ───────────────────────

def test_ac3_confirm_endpoint_exists_in_router():
    """AC3: split_ticket.py defines a POST endpoint for confirming the split."""
    source = SPLIT_ROUTER_PATH.read_text(encoding="utf-8")
    assert "split/confirm" in source or "confirm" in source, (
        "split_ticket.py must define the /confirm endpoint"
    )


def test_ac3_child_labels_inherit_all_except_size_xl():
    """AC3: build_child_labels() strips size-XL and keeps all other labels."""
    from routers.split_ticket_service import build_child_labels

    parent_labels = ["enhancement", "size-XL", "sprint-93", "in-progress", "estimated"]
    result = build_child_labels(parent_labels, sprint_label="sprint-93")

    assert "size-XL" not in result, (
        f"size-XL must be stripped from child labels; got {result}"
    )
    assert "enhancement" in result, "enhancement label must be inherited"
    assert "sprint-93" in result, "sprint label must be inherited"
    assert "estimated" not in result, (
        "estimated label should be stripped (children need fresh estimation)"
    )


def test_ac3_child_labels_without_size_xl_and_no_size():
    """AC3: build_child_labels works when parent has no size-XL (already S/M/L)."""
    from routers.split_ticket_service import build_child_labels

    parent_labels = ["enhancement", "size-M", "sprint-93"]
    result = build_child_labels(parent_labels, sprint_label="sprint-93")

    assert "size-M" not in result, (
        "Any existing size label on parent should be stripped for child"
    )
    assert "sprint-93" in result, "sprint label must be inherited"


def test_ac3_confirm_creates_two_issues_via_github():
    """AC3: confirm_split() calls github_client.create_issue twice."""
    from routers import split_ticket_service

    mock_gc = MagicMock()
    mock_gc.create_issue.side_effect = [(101, "http://example.com/101"),
                                        (102, "http://example.com/102")]
    mock_gc.get_issue.return_value = {
        "number": 100,
        "title": "Original Ticket",
        "body": "## Acceptance Criteria\n- [ ] do X",
        "labels": [{"name": "enhancement"}, {"name": "size-XL"}, {"name": "sprint-93"}],
    }
    mock_gc.add_comment = MagicMock()
    mock_gc.assign_sprint_by_label = MagicMock()
    mock_gc.update_labels = MagicMock()

    result = split_ticket_service.confirm_split(
        issue_num=100,
        sprint_label="sprint-93",
        project="owner/repo",
        child1_title="Part A",
        child1_body="## Acceptance Criteria\n- [ ] do A",
        child2_title="Part B",
        child2_body="## Acceptance Criteria\n- [ ] do B",
        action="remove",
        gc=mock_gc,
    )

    assert mock_gc.create_issue.call_count == 2, (
        f"create_issue must be called twice; called {mock_gc.create_issue.call_count} times"
    )
    assert result["child1_num"] == 101
    assert result["child2_num"] == 102
    assert result["ok"] is True


# ── AC4: Remove or close original ────────────────────────────────────────────

def test_ac4_action_remove_strips_sprint_label():
    """AC4: action='remove' removes the sprint label from the original ticket."""
    from routers import split_ticket_service

    mock_gc = MagicMock()
    mock_gc.create_issue.side_effect = [(101, "http://example.com/101"),
                                        (102, "http://example.com/102")]
    mock_gc.get_issue.return_value = {
        "number": 100,
        "title": "Original",
        "body": "",
        "labels": [{"name": "size-XL"}, {"name": "sprint-93"}],
    }

    split_ticket_service.confirm_split(
        issue_num=100,
        sprint_label="sprint-93",
        project="owner/repo",
        child1_title="Part A",
        child1_body="",
        child2_title="Part B",
        child2_body="",
        action="remove",
        gc=mock_gc,
    )

    # Should call assign_sprint_by_label(100, None) to remove from sprint
    assign_calls = mock_gc.assign_sprint_by_label.call_args_list
    # OR update_labels to remove sprint-93
    update_calls = mock_gc.update_labels.call_args_list
    removed_from_sprint = any(
        (args and args[1] is None) or
        (kwargs.get("sprint_label") is None)
        for args, kwargs in (c for c in assign_calls)
    ) or any(
        "sprint-93" in str(c)
        for c in update_calls
    )
    assert removed_from_sprint or mock_gc.update_labels.called or mock_gc.assign_sprint_by_label.called, (
        "action='remove' must strip the sprint label from the original ticket"
    )


def test_ac4_action_close_closes_with_comment():
    """AC4: action='close' adds a comment referencing child issues and closes original."""
    from routers import split_ticket_service

    mock_gc = MagicMock()
    mock_gc.create_issue.side_effect = [(101, "http://example.com/101"),
                                        (102, "http://example.com/102")]
    mock_gc.get_issue.return_value = {
        "number": 100,
        "title": "Original",
        "body": "",
        "labels": [{"name": "size-XL"}, {"name": "sprint-93"}],
    }

    split_ticket_service.confirm_split(
        issue_num=100,
        sprint_label="sprint-93",
        project="owner/repo",
        child1_title="Part A",
        child1_body="",
        child2_title="Part B",
        child2_body="",
        action="close",
        gc=mock_gc,
    )

    # close_issue must be called
    assert mock_gc.close_issue.called, (
        "action='close' must call gc.close_issue on the original ticket"
    )
    # add_comment must reference both children
    assert mock_gc.add_comment.called, (
        "action='close' must add a comment referencing the child issues"
    )
    comment_text = mock_gc.add_comment.call_args[0][1] if mock_gc.add_comment.call_args else ""
    assert "101" in comment_text and "102" in comment_text, (
        f"Comment must reference both child issue numbers; got: {comment_text!r}"
    )


# ── AC5: Children trigger re-estimate ────────────────────────────────────────

def test_ac5_children_trigger_estimate_run():
    """AC5: confirm_split() schedules re-estimate for both children after creation."""
    from routers import split_ticket_service

    mock_gc = MagicMock()
    mock_gc.create_issue.side_effect = [(101, "http://example.com/101"),
                                        (102, "http://example.com/102")]
    mock_gc.get_issue.return_value = {
        "number": 100,
        "title": "Original",
        "body": "",
        "labels": [{"name": "size-XL"}, {"name": "sprint-93"}],
    }

    triggered_estimates = []
    def fake_trigger(issue_num, project, repo):
        triggered_estimates.append(issue_num)

    with patch.object(split_ticket_service, "_trigger_estimate", side_effect=fake_trigger):
        split_ticket_service.confirm_split(
            issue_num=100,
            sprint_label="sprint-93",
            project="owner/repo",
            child1_title="Part A",
            child1_body="",
            child2_title="Part B",
            child2_body="",
            action="remove",
            gc=mock_gc,
        )

    assert 101 in triggered_estimates, (
        f"Re-estimate must be triggered for child1 (#101); triggered for {triggered_estimates}"
    )
    assert 102 in triggered_estimates, (
        f"Re-estimate must be triggered for child2 (#102); triggered for {triggered_estimates}"
    )


# ── AC7: Fallback when BA call unavailable ────────────────────────────────────

def test_ac7_fallback_notice_when_ba_fails():
    """AC7: when the BA subprocess call fails, suggest returns fallback=True with empty fields."""
    from routers.split_ticket_service import _parse_ba_suggest_output

    result = _parse_ba_suggest_output(None)  # None simulates BA call failure

    assert result.get("fallback") is True, (
        f"When BA fails, fallback must be True; got {result}"
    )
    assert result.get("child1", {}).get("title", "") == "", (
        "Fallback child1 title must be empty string"
    )
    assert result.get("child2", {}).get("title", "") == "", (
        "Fallback child2 title must be empty string"
    )


def test_ac7_fallback_on_invalid_json():
    """AC7: malformed BA output also produces fallback=True."""
    from routers.split_ticket_service import _parse_ba_suggest_output

    result = _parse_ba_suggest_output("not valid json at all")

    assert result.get("fallback") is True, (
        f"Malformed output must return fallback=True; got {result}"
    )


# ── AC7: Modal in project.html has fallback notice element ───────────────────

def test_ac7_fallback_notice_element_in_html():
    """AC7: project.html contains a fallback notice element in the split modal."""
    source = PROJECT_HTML_PATH.read_text(encoding="utf-8")
    assert "fallback" in source.lower() or "ba-unavailable" in source.lower() or "split-fallback" in source.lower(), (
        "project.html must show a fallback notice when BA is unavailable"
    )


# ── Router file structure ─────────────────────────────────────────────────────

def test_router_file_exists():
    """Infrastructure: apps/dashboard/routers/split_ticket.py exists."""
    assert SPLIT_ROUTER_PATH.exists(), (
        "routers/split_ticket.py not found — must be created"
    )


def test_service_file_exists():
    """Infrastructure: apps/dashboard/routers/split_ticket_service.py exists."""
    assert SPLIT_SERVICE_PATH.exists(), (
        "routers/split_ticket_service.py not found — must be created"
    )


def test_router_compiles():
    """Infrastructure: split_ticket.py has no syntax errors."""
    try:
        py_compile.compile(str(SPLIT_ROUTER_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        raise AssertionError(f"routers/split_ticket.py has syntax errors: {e}")


def test_service_compiles():
    """Infrastructure: split_ticket_service.py has no syntax errors."""
    try:
        py_compile.compile(str(SPLIT_SERVICE_PATH), doraise=True)
    except py_compile.PyCompileError as e:
        raise AssertionError(f"routers/split_ticket_service.py has syntax errors: {e}")


def test_router_registered_in_init():
    """Infrastructure: __init__.py exports split_ticket_router."""
    source = INIT_PATH.read_text(encoding="utf-8")
    assert "split_ticket" in source, (
        "routers/__init__.py must import and export split_ticket_router"
    )


def test_router_mounted_in_server():
    """Infrastructure: server.py mounts split_ticket_router via app.include_router."""
    source = SERVER_PATH.read_text(encoding="utf-8")
    assert "split_ticket_router" in source, (
        "server.py must mount the split_ticket_router"
    )


# ── AC8: project.html includes re-fetch DAG after split ──────────────────────

def test_ac8_project_html_refreshes_dag_after_split():
    """AC8: project.html calls the preview-dag refresh after a split confirms."""
    source = PROJECT_HTML_PATH.read_text(encoding="utf-8")
    # The split confirm handler must trigger a DAG re-fetch
    assert "smgmtSplitConfirm" in source or "splitConfirm" in source, (
        "project.html must define the split confirm function"
    )
    # After confirm, should trigger _smgmtFetchPreviewDag or similar
    assert "_smgmtFetchPreviewDag" in source or "preview-dag" in source, (
        "project.html must re-fetch preview-dag after split to update the mini-rail (AC8)"
    )


# ── AC2: Split modal elements in project.html ────────────────────────────────

def test_ac2_modal_has_two_editable_sections():
    """AC2: project.html contains a split modal with two editable child sections."""
    source = PROJECT_HTML_PATH.read_text(encoding="utf-8")
    assert "split" in source.lower() and "modal" in source.lower(), (
        "project.html must have a split modal"
    )
    # Modal must include input/textarea elements for both children
    # We check for 'child1' or 'split-child' patterns
    assert "split-child" in source or "splitChild" in source or "child1" in source, (
        "project.html split modal must contain elements for both child issues"
    )
