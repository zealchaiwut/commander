"""Tests for issue #802 — Render level-rail node board in the Running view.

The rail is a vanilla-JS frontend feature living in
``apps/dashboard/static/project.html``. Following the established pattern for
frontend tickets (#800, #801), the assertions read the static HTML/JS source and
check the design-contract tokens, the render/patch functions, and their state
logic — anchored one-to-one to the acceptance criteria.

AC coverage:
  AC1  — Rail renders from the /live snapshot: levels on a spine, ticket node
         cards grouped per level (`.rail` / `.level` / `.level-dot` / `.nodes`
         / `.node`).
  AC2  — Node states render correctly: done (green check + elapsed),
         coder-active (purple glow), tester-active (amber glow), queued (grey).
  AC3  — Pipeline mode: two concurrent agent nodes can glow at once within the
         same level (no logic collapses coder+tester to a single glow).
  AC4  — Done levels stay expanded with a green indicator; the active level is
         highlighted; queued levels show gate text.
  AC5  — A fix-round badge appears on a node when retry/attempt data is present
         and is hidden otherwise (no change to the /live contract).
  AC6  — Each node displays a size pill and elapsed time.
  AC7  — Clicking a node selects it and feeds the inspector ticket panel.
  AC8  — The rail updates on the existing poll cycle with no flicker / full
         re-render (an in-place patch path exists and is wired into the poll).
  AC9  — DOM targets match the design contract: `.rail`, `.level`, `.level-dot`,
         `.nodes`, `.node` and the named state variants.
  AC10 — Only the BA Path-A literal tokens are used; node state classes use the
         literal contract names, not invented synonyms.
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

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


# ──────────────────────────── helpers ────────────────────────────────────────

def _fn_body(name: str) -> str:
    """Return the brace-balanced body of a JS function defined in project.html."""
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = PROJECT_HTML.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found in project.html"
    brace = PROJECT_HTML.find("{", pos)
    assert brace != -1, f"no opening brace for {name}"
    depth = 0
    for i in range(brace, len(PROJECT_HTML)):
        c = PROJECT_HTML[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return PROJECT_HTML[brace:i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_rule(selector: str) -> str:
    """Return the body of the first CSS rule whose selector list contains `selector`."""
    # Find a `{` whose preceding selector text mentions the selector as a whole token.
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(PROJECT_HTML)
    assert m is not None, f"CSS rule for `{selector}` not found"
    return m.group(1)


# ───────────────────────── AC1: rail renders from /live ──────────────────────

def test_ac1_running_pane_renders_issues_grouped_by_level():
    """Issues render grouped by dispatch level in the Running sub-view.

    Note: the standalone level-rail was consolidated into the All Issues panel
    (issue #1106 / #1233). The level-grouping contract survived the move — the
    All Issues render still groups via `_smgmtRailLevels` and emits per-level
    rows — so the contract is asserted against its current home.
    """
    body = _fn_body("_smgmtAllIssuesHtml")
    assert "_smgmtRailLevels(" in body, "issues must be grouped into dispatch levels"
    for token in ("ai-level", "ai-level-rows", "_smgmtAiRowHtml"):
        assert token in body, f"panel render must emit the `{token}` contract token"
    # Per-issue rows are built by the row helper and carry the row contract class.
    assert "ai-row" in _fn_body("_smgmtAiRowHtml"), "issue rows must use the `ai-row` class"


def test_ac1_running_panels_mount_in_running_subview():
    """The live panels mount inside the Running sub-view, not elsewhere.

    The level-rail mount point was replaced by the orchestrator + all-issues
    panels in the running-pane consolidation (issue #1106).
    """
    start = PROJECT_HTML.index('id="smgmt-subview-running"')
    end = PROJECT_HTML.index("smgmt-subview-running -->", start)
    subview = PROJECT_HTML[start:end]
    assert 'id="smgmt-all-issues"' in subview, \
        "all-issues panel must mount in the Running sub-view"
    assert 'id="smgmt-orch-panel"' in subview, \
        "orchestrator panel must mount in the Running sub-view"


def test_ac1_groups_issues_by_dispatch_level():
    """Levels on the spine are derived by grouping snapshot issues by dispatch_level."""
    body = _fn_body("_smgmtRailLevels")
    assert "dispatch_level" in body, "levels must be grouped by dispatch_level"
    # Falls back to the levels[] state array from the snapshot when present.
    assert "levels" in body


def test_ac1_render_and_orchestrator_exist():
    assert "function _smgmtRailRender(" in PROJECT_HTML
    assert "function _smgmtRailUpdate(" in PROJECT_HTML


# ───────────────────────── AC2: node state mapping ───────────────────────────

def test_ac2_node_state_function_maps_four_states():
    body = _fn_body("_smgmtRailNodeState")
    # The four literal state tokens from the contract.
    for state in ("done", "coder-active", "tester-active", "queued"):
        assert f"'{state}'" in body or f'"{state}"' in body, f"missing state `{state}`"
    # done is keyed off status, the active states off the agent role.
    assert "done" in body and "status" in body
    assert "coder" in body and "tester" in body


def test_ac2_done_node_shows_green_check():
    """Done nodes render a check mark (green via the done state class)."""
    body = _fn_body("_smgmtRailNodeHtml")
    assert "&#10003;" in body or "✓" in body or "ti-check" in body, "done node needs a check glyph"


def test_ac2_coder_purple_tester_amber_in_css():
    coder = _css_rule(".node.coder-active")
    assert "--purple" in coder, "coder-active glow must use the purple token"
    tester = _css_rule(".node.tester-active")
    assert "--amber" in tester, "tester-active glow must use the amber token"
    done = _css_rule(".node.done")
    assert "--green" in done, "done node must use the green token"


def test_ac2_active_states_use_glow_not_side_stripe():
    """Glow is a box-shadow (impeccable bans thick coloured side-stripe borders)."""
    coder = _css_rule(".node.coder-active")
    assert "box-shadow" in coder, "purple glow must be a box-shadow"
    assert "border-left" not in coder and "border-right" not in coder


# ───────────────────────── AC3: pipeline dual glow ───────────────────────────

def test_ac3_two_concurrent_glows_allowed():
    """Nothing forces a single active node: state is computed per-issue, so a
    coder node and a tester node in the same level both resolve to a glow state."""
    state_body = _fn_body("_smgmtRailNodeState")
    # The mapping is per-issue (agent role), never a single-winner across the level.
    assert "active_agents" not in state_body, "node state must be per-issue, not a level-wide single agent"
    levels_body = _fn_body("_smgmtRailLevels")
    # Pipeline grouping keeps every issue in its level so both can render.
    assert "push" in levels_body or "concat" in levels_body or "filter" in levels_body


# ───────────────────────── AC4: level collapse / gate ────────────────────────

def test_ac4_level_state_classes_in_css():
    for cls in (".level.done", ".level.active", ".level.queued"):
        _css_rule(cls)  # raises if missing


def test_ac4_done_level_keeps_nodes_visible():
    """Mock v5: completed levels stay expanded with green check nodes."""
    m = re.search(r"\.level\.done\s+\.nodes\s*\{([^}]*)\}", PROJECT_HTML)
    if m:
        assert "display" not in m.group(1) or "none" not in m.group(1), \
            "done level must keep its node cards visible (no collapse)"


def test_ac4_done_level_dot_is_green():
    rule = _css_rule(".level.done .level-dot")
    assert "--green" in rule, "done level's level-dot must be green"


def test_ac4_queued_level_shows_gate_text():
    meta = _fn_body("_smgmtRailLevelMeta")
    assert "waits for level" in meta or "Waiting for Level" in meta, \
        "queued levels must show gate text"
    assert "queued" in meta or "waiting" in meta


# ───────────────────────── AC5: fix-round badge ──────────────────────────────

def test_ac5_fix_round_is_feature_detected():
    body = _fn_body("_smgmtRailFixRound")
    # Reads retry/attempt data from the issue; does not invent a /live field.
    assert "fix_round" in body or "attempt" in body or "retry" in body or "round" in body


def test_ac5_badge_hidden_when_absent():
    """The node renderer only emits the badge when the fix-round helper returns truthy."""
    node = _fn_body("_smgmtRailNodeHtml")
    assert "_smgmtRailFixRound" in node, "node render must consult the fix-round helper"
    assert "node-fix-badge" in node


def test_ac5_fix_round_helper_reads_snapshot_fields():
    """The fix-round helper reads retry/attempt/fix_round data from the snapshot issue.

    A fix-round field WAS added to the /live response in a later sprint — the
    badge now uses it directly. The helper must read at least one of: fix_round,
    retry, round, attempt, tester_attempt_count.
    """
    body = _fn_body("_smgmtRailFixRound")
    any_field = any(f in body for f in ("fix_round", "retry", "round", "attempt", "tester_attempt_count"))
    assert any_field, "fix-round helper must read retry/attempt/fix_round data from the snapshot"


# ───────────────────────── AC6: size pill + elapsed ──────────────────────────

def test_ac6_node_has_size_pill_and_elapsed():
    body = _fn_body("_smgmtRailNodeHtml")
    assert "node-size" in body, "node must render a size pill"
    assert "node-time" in body or "node-elapsed" in body, "node must render elapsed time"
    # Uses the snapshot fields.
    assert "size" in body
    assert "elapsed" in body or "_fmtTicketElapsed" in body


# ───────────────────────── AC7: click selects + inspector ────────────────────

def test_ac7_node_is_clickable():
    node = _fn_body("_smgmtRailNodeHtml")
    assert "_smgmtRailSelectNode" in node, "node must wire a click handler"


def test_ac7_select_marks_selected_and_feeds_inspector():
    body = _fn_body("_smgmtRailSelectNode")
    assert "selected" in body, "selection must mark the node selected"
    # Feeds the (out-of-scope, future) inspector panel via a feature-detected hook.
    assert "_smgmtInspector" in body or "inspector" in body.lower(), \
        "selecting a node must feed the inspector ticket panel"


# ───────────────────────── AC8: poll update, no flicker ──────────────────────

def test_ac8_patch_path_exists():
    assert "function _smgmtRailPatch(" in PROJECT_HTML


def test_ac8_orchestrator_prefers_patch_over_full_rebuild():
    body = _fn_body("_smgmtRailUpdate")
    assert "_smgmtRailPatch" in body, "update must use the in-place patch path"
    assert "_smgmtRailRender" in body, "update must full-render only when structure changes"
    # A structural signature gates render-vs-patch to avoid flicker.
    assert "sig" in body.lower() or "railsig" in body.lower()


def test_ac8_patch_does_not_wipe_whole_rail():
    """The patch path must not blow away the rail container's innerHTML."""
    body = _fn_body("_smgmtRailPatch")
    assert "querySelectorAll" in body, "patch updates existing nodes in place"
    # It must not reset the rail root's innerHTML (that would be a full re-render).
    assert re.search(r"rail\w*\.innerHTML\s*=", body) is None


def test_ac8_wired_into_live_poll():
    poll = _fn_body("_smgmtLivePollTick")
    assert "_smgmtRunningViewUpdate" in poll or "_smgmtRailUpdate" in poll, \
        "rail must update on the existing 2s poll"


def test_ac8_wired_into_subview_switch():
    body = _fn_body("_smgmtShowSubView")
    assert "_smgmtRunningViewUpdate" in body or "_smgmtRail" in body, \
        "switching to Running must render the rail from cache"


# ───────────────────────── AC9 / AC10: token contract ────────────────────────

def test_ac9_all_contract_targets_styled():
    for cls in (".rail", ".level", ".level-dot", ".nodes", ".node"):
        _css_rule(cls)  # raises if any contract token is unstyled


def test_ac10_node_state_classes_use_literal_tokens():
    """State variants use the exact BA Path-A names; no invented synonyms."""
    render = _fn_body("_smgmtRailNodeHtml") + _fn_body("_smgmtRailNodeState")
    # The literal contract names must appear.
    for state in ("coder-active", "tester-active", "queued"):
        assert state in render
    # Common invented synonyms must NOT be used for node state.
    for bad in ("node--active", "node-running", "is-coder", "node-glow", "active-coder"):
        assert bad not in render, f"invented token `{bad}` must not be used"
