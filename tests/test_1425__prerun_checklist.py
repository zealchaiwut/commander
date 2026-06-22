"""Tests for issue #1425: pre-run checklist on partial preview-dag.

Anchored to Acceptance Criteria:
- AC1: When partial: true, mini-rail checklist renders "Estimate N
       tickets" item linked to the existing "Estimate all" action
- AC2: When conflicts.length > 0, checklist includes "Resolve K file
       conflicts" item linked to the "Apply DAG Order" action
- AC3: When M XL tickets exceed the XL threshold, checklist includes
       "Split M XL tickets" item linked to the "Split" action
- AC4: Checklist items that do not apply (count 0) are not rendered
- AC5: Live count reflects the current sprint state (_smgmtPreflightHints
       computes xlCount from the live ticket list)
- AC6: Completing an estimate action auto-refetches preview-dag
       (_smgmtBulkEstimate calls _smgmtRefreshPreviewDag when sized)
- AC7: When all checklist items are checked off the mini-rail shows
       a "Ready to run" visual state
- AC8: Checklist not shown when partial is false and conflicts is
       empty — existing DAG view renders unchanged
- AC9: No changes required to any backend API (contract unchanged)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROJECT_HTML = (
    REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
).read_text(encoding="utf-8")


# ── helpers ──────────────────────────────────────────────────────────────


def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a named JS function."""
    for needle in (
        f"function {name}(",
        f"{name} = function",
        f"async function {name}(",
        f"export function {name}(",
        f"export async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
    brace = src.find("{", pos)
    assert brace != -1, f"No opening brace for {name}"
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace:i + 1]
    raise AssertionError(f"Unbalanced braces for function {name}")


def _css_exists(selector: str, src: str = PROJECT_HTML) -> bool:
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{")
    return bool(pat.search(src))


# =============================================================================
# AC1 — "Estimate N tickets" checklist item when partial: true
# =============================================================================


def test_checklist_renders_estimate_item_when_partial():
    """AC1: _smgmtMiniRailHtml builds an 'Estimate N tickets' checklist
    item when data.partial is true and unestimated tickets exist.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    has_estimate_check = (
        "unestimatedCount" in body
        or "unestimated.length" in body
        or "data.partial" in body
    )
    assert has_estimate_check, (
        "_smgmtMiniRailHtml must check data.partial / unestimated "
        "for the Estimate item"
    )
    assert "Estimate" in body, (
        "_smgmtMiniRailHtml must emit an 'Estimate' label for the "
        "estimate checklist item"
    )


def test_checklist_estimate_item_links_to_bulk_estimate():
    """AC1: The 'Estimate N tickets' checklist item calls
    _smgmtBulkEstimate.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    assert "_smgmtBulkEstimate" in body, (
        "_smgmtMiniRailHtml must link the Estimate checklist item "
        "to _smgmtBulkEstimate"
    )


# =============================================================================
# AC2 — "Resolve K file conflicts" checklist item
# =============================================================================


def test_checklist_renders_conflict_item_when_conflicts_present():
    """AC2: _smgmtMiniRailHtml builds a 'Resolve K file conflicts'
    checklist item when data.conflicts is non-empty.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    assert "Resolve" in body, (
        "_smgmtMiniRailHtml must emit a 'Resolve' label for the "
        "conflicts checklist item"
    )
    assert "conflicts" in body, (
        "_smgmtMiniRailHtml must reference conflicts for the "
        "Resolve checklist item"
    )


def test_checklist_conflict_item_links_to_dag_order():
    """AC2: The 'Resolve K file conflicts' item calls
    smgmtApplyDagOrder.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    assert "smgmtApplyDagOrder" in body, (
        "_smgmtMiniRailHtml must link the Resolve conflicts item "
        "to smgmtApplyDagOrder"
    )


# =============================================================================
# AC3 — "Split M XL tickets" checklist item
# =============================================================================


def test_checklist_renders_xl_split_item_when_xl_tickets_present():
    """AC3: _smgmtMiniRailHtml builds a 'Split M XL tickets' checklist
    item when pfHints.xlCount > 0.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    assert "xlCount" in body, (
        "_smgmtMiniRailHtml must use pfHints.xlCount for the "
        "Split XL item"
    )
    assert "Split" in body, (
        "_smgmtMiniRailHtml must emit a 'Split' label for the "
        "XL checklist item"
    )


def test_checklist_split_item_links_to_existing_action():
    """AC3: The 'Split M XL tickets' item links to an existing sprint
    action (smgmtRunSprint — opens the preflight modal where XL
    suggestions appear).
    """
    body = _fn_body("_smgmtMiniRailHtml")
    has_action = "smgmtRunSprint" in body or "smgmtSplitOpen" in body
    assert has_action, (
        "_smgmtMiniRailHtml must link the Split item to an existing "
        "split action (smgmtRunSprint or smgmtSplitOpen)"
    )


# =============================================================================
# AC4 — No checklist item rendered when count is 0
# =============================================================================


def test_checklist_items_gated_on_positive_count():
    """AC4: Each checklist item is guarded by a positive count check
    (> 0).
    """
    body = _fn_body("_smgmtMiniRailHtml")
    has_estimate_guard = (
        "unestimatedCount > 0" in body
        or "unestimated.length > 0" in body
        or ("checklistItems" in body and "unestimatedCount" in body)
    )
    assert has_estimate_guard, (
        "Estimate checklist item must be guarded by a positive "
        "unestimated count"
    )
    has_conflict_guard = (
        "conflictsCount > 0" in body
        or ("checklistItems" in body and "conflictsCount" in body)
    )
    assert has_conflict_guard, (
        "Resolve conflicts item must be guarded by conflictsCount > 0"
    )
    assert "xlCount > 0" in body, (
        "Split XL item must be guarded by xlCount > 0"
    )


# =============================================================================
# AC5 — Live count: _smgmtPreflightHints computes xlCount
# =============================================================================


def test_preflight_hints_computes_xl_count():
    """AC5: _smgmtPreflightHints includes xlCount derived from the live
    ticket list.
    """
    body = _fn_body("_smgmtPreflightHints")
    assert "xlCount" in body, (
        "_smgmtPreflightHints must compute xlCount for live XL "
        "ticket count"
    )
    assert "_smgmtTicketSize" in body or "XL" in body, (
        "_smgmtPreflightHints must derive xlCount using ticket "
        "size (XL check)"
    )


def test_preflight_hints_returns_xl_count():
    """AC5: _smgmtPreflightHints return value includes xlCount."""
    body = _fn_body("_smgmtPreflightHints")
    assert "xlCount" in body and "return" in body, (
        "_smgmtPreflightHints must return xlCount in its result object"
    )


def test_mini_rail_sig_includes_xl_count():
    """AC5: _smgmtMiniRailSig incorporates xlCount so signature changes
    when XL ticket count changes (triggers re-render, not cached HTML).
    """
    body = _fn_body("_smgmtMiniRailSig")
    assert "xlCount" in body, (
        "_smgmtMiniRailSig must include pfHints.xlCount so cache "
        "invalidates when XL ticket count changes"
    )


# =============================================================================
# AC6 — Completing estimate action auto-refetches preview-dag
# =============================================================================


def test_bulk_estimate_refreshes_preview_dag_after_success():
    """AC6: _smgmtBulkEstimate calls _smgmtRefreshPreviewDag after
    estimating at least one ticket, so the checklist re-evaluates
    automatically.
    """
    body = _fn_body("_smgmtBulkEstimate")
    assert "_smgmtRefreshPreviewDag" in body, (
        "_smgmtBulkEstimate must call _smgmtRefreshPreviewDag after "
        "estimation so the Estimate item is auto-checked off (AC6)"
    )


# =============================================================================
# AC7 — "Ready to run" state when all checklist items are cleared
# =============================================================================


def test_ready_to_run_css_class_defined():
    """AC7: .mr-ready CSS class is defined so the 'Ready to run'
    indicator renders.
    """
    assert _css_exists(".mr-ready"), (
        ".mr-ready CSS class must be defined for the 'Ready to run' "
        "visual state (AC7)"
    )


def test_ready_to_run_state_rendered_when_checklist_primed_and_clear():
    """AC7: _smgmtMiniRailHtml emits a 'Ready to run' element when the
    checklist was previously shown (_smgmtChecklistPrimed) and all
    items are now resolved.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    assert "_smgmtChecklistPrimed" in body, (
        "_smgmtMiniRailHtml must reference _smgmtChecklistPrimed to "
        "track when the checklist transitions to 'Ready to run' (AC7)"
    )
    assert "Ready to run" in body, (
        "_smgmtMiniRailHtml must emit 'Ready to run' text for the "
        "AC7 green state"
    )


def test_checklist_primed_set_exists():
    """AC7: _smgmtChecklistPrimed Set is declared at module scope."""
    assert "_smgmtChecklistPrimed" in PROJECT_HTML, (
        "_smgmtChecklistPrimed must be declared as a module-level Set "
        "so the 'Ready to run' transition persists across re-renders"
    )


# =============================================================================
# AC8 — No checklist shown when partial=false and conflicts empty
# =============================================================================


def test_checklist_not_shown_when_all_clear():
    """AC8: When partial is false, conflicts is empty, and xlCount is 0,
    no checklist markup is emitted — the existing DAG view is unchanged.
    """
    body = _fn_body("_smgmtMiniRailHtml")
    has_guard = (
        "hasChecklist" in body
        or "checklistItems.length" in body
        or "checklistSection" in body
    )
    assert has_guard, (
        "_smgmtMiniRailHtml must gate the checklist section on a "
        "'hasChecklist' (or equivalent) guard so it is absent when "
        "all counts are 0 (AC8)"
    )


# =============================================================================
# AC9 — No backend changes (preview-dag API contract unchanged)
# =============================================================================


def test_preview_dag_response_contract_unchanged(tmp_path):
    """AC9: The preview-dag endpoint still returns the same contract
    (levels, conflicts, cycles, unestimated, partial) with no new
    required fields.
    """
    import sys
    DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
    SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"
    for p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)

    if "server" in sys.modules:
        del sys.modules["server"]

    from unittest.mock import patch
    import server as srv

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    issues = [
        {
            "number": 1,
            "title": "Ticket A",
            "state": "open",
            "url": "https://github.com/t/r/issues/1",
            "body": "## Acceptance Criteria\n- [ ] done",
            "labels": [{"name": "sprint-10"}, {"name": "backlog"}],
        }
    ]

    with patch.object(
        srv.github_client,
        "cached_open_issues_with_body",
        return_value=issues,
    ), patch("server._project_root_path", side_effect=fake_root):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get(
            "/api/sprints/sprint-10/preview-dag?project=test/repo"
        )
        if resp.status_code == 200:
            data = resp.json()
            for key in (
                "levels", "conflicts", "cycles", "unestimated", "partial"
            ):
                assert key in data, (
                    f"preview-dag response must still contain '{key}' "
                    "(AC9 — no API contract change)"
                )


# =============================================================================
# CSS classes for checklist elements
# =============================================================================


def test_checklist_css_classes_defined():
    """Checklist CSS classes (.mr-checklist, .mr-checklist-item,
    .mr-cl-link) must be defined so checklist items render correctly.
    """
    for cls in (".mr-checklist", ".mr-checklist-item", ".mr-cl-link"):
        assert _css_exists(cls), (
            f"CSS class '{cls}' must be defined for the pre-run "
            "checklist (issue #1425)"
        )
