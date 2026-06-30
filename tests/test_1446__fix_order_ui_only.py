"""Tests for issue #1446 — Fix order must be UI-only (no backend persistence).

Follow-up to #1422. The original `smgmtFixOrderViolation` POSTed the new order
to `/api/sprints/{label}/plan` and called `loadSprintMgmt()`, contradicting
#1422's stated UI-only scope. This ticket makes Fix order a pure DOM
re-evaluation, matching the drag-drop path.

Verified by static analysis of the `smgmtFixOrderViolation` function body in
project.html — no server needed (the function is browser-only JS).

AC coverage:
  AC1 — `smgmtFixOrderViolation` does not POST to `/api/sprints/{label}/plan`.
  AC2 — Fix order reorders dispatch slots in the DOM only, with no network write.
  AC3 — No persistence occurs (no fetch/XHR), so a reload reverts to backend order.
  AC4 — `loadSprintMgmt()` is not called inside `smgmtFixOrderViolation`; the
         visual update is a local DOM/state patch.
  AC5 — A comment reconciling the UI-only scope from #1422 accompanies the fix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HTML_PATH = DASHBOARD_DIR / "static" / "project.html"
PROJECT_HTML = _HTML_PATH.read_text(encoding="utf-8")

_FN_NAME = "smgmtFixOrderViolation"


def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a JS function in src."""
    for needle in (
        f"function {name}(",
        f"function {name} (",
        f"{name} = function(",
        f"async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found in source"
    brace = src.find("{", pos)
    assert brace != -1, f"no opening brace for {name}"
    depth = 0
    for i in range(brace, len(src)):
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[brace : i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


FN_BODY = _fn_body(_FN_NAME)


# ── AC1: no POST to the plan API ─────────────────────────────────────────────

def test_ac1_no_post_to_plan_api():
    """AC1: the function body never references the plan endpoint."""
    assert "/plan" not in FN_BODY, (
        f"{_FN_NAME} must not reference /api/sprints/.../plan; got body:\n{FN_BODY}"
    )


def test_ac1_no_post_method():
    """AC1: the function body issues no POST request."""
    assert "method: 'POST'" not in FN_BODY and 'method: "POST"' not in FN_BODY, (
        f"{_FN_NAME} must not issue a POST; got body:\n{FN_BODY}"
    )


# ── AC2/AC3: no network write at all (pure DOM) ──────────────────────────────

def test_ac2_no_fetch_call():
    """AC2/AC3: the function makes no network write (no fetch / XHR)."""
    assert "fetch(" not in FN_BODY, (
        f"{_FN_NAME} must not call fetch — Fix order is UI-only; got body:\n{FN_BODY}"
    )
    assert "XMLHttpRequest" not in FN_BODY, (
        f"{_FN_NAME} must not use XMLHttpRequest; got body:\n{FN_BODY}"
    )


def test_ac2_reorders_dom_nodes():
    """AC2: the function performs an in-DOM reorder of the ticket rows."""
    has_dom_move = any(
        op in FN_BODY
        for op in ("insertBefore", "appendChild", ".before(", ".after(", "insertAdjacent")
    )
    assert has_dom_move, (
        f"{_FN_NAME} must reorder ticket rows in the DOM; got body:\n{FN_BODY}"
    )


def test_ac3_reevaluates_violations_locally():
    """AC3: after the DOM reorder, violations are re-evaluated locally (no reload)."""
    assert "_smgmtUpdateOrderViolations" in FN_BODY, (
        f"{_FN_NAME} must re-evaluate order violations via _smgmtUpdateOrderViolations "
        f"(local re-evaluation, matching the drag-drop path); got body:\n{FN_BODY}"
    )


# ── AC4: no full board reload ────────────────────────────────────────────────

def test_ac4_does_not_call_load_sprint_mgmt():
    """AC4: loadSprintMgmt() is not called inside the function."""
    assert "loadSprintMgmt(" not in FN_BODY, (
        f"{_FN_NAME} must not call loadSprintMgmt() — the update must be a local "
        f"DOM/state patch; got body:\n{FN_BODY}"
    )


# ── AC5: reconciliation comment ──────────────────────────────────────────────

def test_ac5_reconciliation_comment_present():
    """AC5: a comment near the function reconciles the UI-only scope from #1422."""
    pos = PROJECT_HTML.find(f"async function {_FN_NAME}(")
    if pos == -1:
        pos = PROJECT_HTML.find(f"function {_FN_NAME}(")
    assert pos != -1
    # Look at the comment block preceding the function definition.
    preamble = PROJECT_HTML[max(0, pos - 800):pos]
    mentions_ui_only = re.search(r"UI-?only", preamble, re.IGNORECASE) is not None
    mentions_1422 = "1422" in preamble or "1446" in preamble
    assert mentions_ui_only and mentions_1422, (
        "A comment above smgmtFixOrderViolation must reconcile the UI-only scope "
        f"and reference #1422/#1446; got preamble:\n{preamble}"
    )
