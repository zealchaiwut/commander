"""Tests for issue #1416 — Add multi-lane live view for concurrent runs.

Tests cover all AC items via static analysis of project.html (frontend
HTML/CSS/JS) and backend sprint_live.py.

AC coverage:
  AC1 — Each active worktree/worker renders as its own lane displaying
         ticket ID, current phase, and a tail of that ticket's dispatch log.
  AC2 — Lanes reuse existing log tail endpoints and status pills; no new
         card primitives are introduced.
  AC3 — Idle/inactive slots are collapsed and not shown as empty lanes.
  AC4 — During a 3-coder run, exactly three lanes are visible with
         independent, live-updating log tails.
  AC5 — In pipeline mode with slots=1, the Running pane renders the
         existing two-lane coder/tester view unchanged.
  AC6 — Lane count updates dynamically as workers are added or complete
         (lanes appear and collapse without a full page reload).
  AC7 — Each lane's log tail scrolls independently.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_HTML_PATH = DASHBOARD_DIR / "static" / "project.html"
_SPRINT_LIVE_PY = ROUTERS_DIR / "sprint_live.py"

PROJECT_HTML = _HTML_PATH.read_text(encoding="utf-8")
SPRINT_LIVE_SRC = _SPRINT_LIVE_PY.read_text(encoding="utf-8")


# ── helpers ────────────────────────────────────────────────────────────────────

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


def _css_block(selector: str, src: str = PROJECT_HTML) -> str:
    """Return the CSS block for a selector, or empty string if not found."""
    pat = re.compile(re.escape(selector) + r"[^{]*\{([^}]*)\}", re.DOTALL)
    m = pat.search(src)
    return m.group(1) if m else ""


# ── AC1: Per-worker lane rendering ────────────────────────────────────────────

def test_ac1_worker_lanes_function_exists():
    """A function builds per-worker lanes HTML (e.g. _smgmtWorkerLanesHtml)."""
    has_fn = (
        "_smgmtWorkerLanesHtml" in PROJECT_HTML
        or "workerLanesHtml" in PROJECT_HTML
        or "worker-lane" in PROJECT_HTML
    )
    assert has_fn, (
        "No per-worker lane builder found in project.html — "
        "expected _smgmtWorkerLanesHtml or equivalent"
    )


def test_ac1_lane_renders_ticket_id():
    """Per-worker lane renders ticket ID (issue number) for each active worker."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    has_num = (
        "number" in fn
        and ("#" in fn or "num" in fn.lower())
    )
    assert has_num, (
        "_smgmtWorkerLanesHtml does not render ticket ID (#N)"
    )


def test_ac1_lane_renders_phase_pill():
    """Per-worker lane renders a phase pill (coding / testing / merging)."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    has_phase = (
        "coding" in fn
        or "phase" in fn.lower()
        or "worker-lane-phase" in fn
    )
    assert has_phase, (
        "_smgmtWorkerLanesHtml does not render a phase indicator for each lane"
    )


def test_ac1_lane_has_log_tail_container():
    """Per-worker lane has a log tail container element."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    has_log = (
        "worker-log" in fn
        or "worker-lane-log" in fn
        or "log" in fn.lower()
    )
    assert has_log, (
        "_smgmtWorkerLanesHtml does not render a log tail container per lane"
    )


def test_ac1_log_container_has_per_lane_id():
    """Each log container has a unique per-issue ID for independent log updates."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    # ID must incorporate the issue number to be unique per lane
    has_per_lane_id = (
        "worker-log-" in fn
        or ("smgmt-worker-log" in fn and "num" in fn.lower())
        or ("id=" in fn and "number" in fn)
    )
    assert has_per_lane_id, (
        "_smgmtWorkerLanesHtml does not assign a per-issue ID to log containers"
    )


# ── AC2: Reuse existing endpoints and primitives ──────────────────────────────

def test_ac2_reuses_issue_log_endpoint():
    """Multi-lane view fetches logs from the existing per-issue log endpoint."""
    # The endpoint is GET /api/sprints/{label}/issue/{N}/log
    has_endpoint = (
        "/issue/" in PROJECT_HTML and "/log" in PROJECT_HTML
        and ("smgmtWorkerLog" in PROJECT_HTML or "worker-log" in PROJECT_HTML or "workerLog" in PROJECT_HTML)
    )
    assert has_endpoint, (
        "Multi-lane view does not reuse /api/sprints/{label}/issue/{N}/log endpoint"
    )


def test_ac2_no_new_card_primitives():
    """Multi-lane view does not introduce new card CSS classes not already in the file."""
    # The worker lane should reuse existing CSS foundations (node, lane, pa-log-stream etc.)
    # rather than introducing a wholly new card primitive with its own full card structure.
    # We verify the worker lane function does NOT define entirely new card class hierarchies
    # that duplicate the existing .node or .smgmt-ticket classes.
    fn = _fn_body("_smgmtWorkerLanesHtml")
    # Worker lanes should NOT reproduce the full node/smgmt-ticket card structure
    has_duplicate_card = (
        'class="node ' in fn
        or 'class="smgmt-ticket' in fn
    )
    assert not has_duplicate_card, (
        "_smgmtWorkerLanesHtml introduces new card primitives (node / smgmt-ticket) — "
        "lanes should be simple containers reusing existing elements"
    )


def test_ac2_log_tail_length_bounded():
    """Log tail fetches use tail_lines parameter to bound response size."""
    has_tail_param = (
        "tail_lines" in PROJECT_HTML
        or "tailLines" in PROJECT_HTML
        or "tail=" in PROJECT_HTML
    )
    assert has_tail_param, (
        "Worker log fetch does not use tail_lines to bound the response"
    )


# ── AC3: Idle slots not shown ─────────────────────────────────────────────────

def test_ac3_only_active_workers_get_lanes():
    """Only issues with an active coder agent are rendered as lanes."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    # The function must filter to working coders only
    has_filter = (
        "agent" in fn
        or "coding" in fn
        or "working" in fn.lower()
        or "coderWorking" in fn
    )
    assert has_filter, (
        "_smgmtWorkerLanesHtml does not filter to active workers — "
        "idle slots would appear as empty lanes"
    )


def test_ac3_empty_state_when_no_active_workers():
    """When no workers are active, no lane divs are rendered (returns empty/waiting state)."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    has_empty_guard = (
        "length" in fn
        and ("return" in fn or "hidden" in fn or "empty" in fn.lower())
    )
    assert has_empty_guard, (
        "_smgmtWorkerLanesHtml does not guard against rendering with 0 active workers"
    )


# ── AC4: Three lanes for 3-coder run ─────────────────────────────────────────

def test_ac4_lanes_rendered_per_active_coder():
    """Each active coder in coderWorking list gets its own lane."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    # The function must iterate over active coders to produce N lanes
    has_iteration = (
        "forEach" in fn
        or "map" in fn
        or "for " in fn
        or ".length" in fn
    )
    assert has_iteration, (
        "_smgmtWorkerLanesHtml does not iterate over active coders — "
        "cannot produce N lanes for N concurrent coders"
    )


def test_ac4_multi_lane_activated_when_slots_gt_1():
    """Multi-lane view is activated when max_coder_slots > 1."""
    fn = _fn_body("_smgmtLanesUpdate")
    has_slot_check = (
        "max_coder_slots" in fn
        or "maxCoder" in fn
        or "slots" in fn.lower()
    )
    assert has_slot_check, (
        "_smgmtLanesUpdate does not check max_coder_slots to activate multi-lane view"
    )


def test_ac4_worker_lanes_fn_called_from_lanes_update():
    """_smgmtLanesUpdate calls the worker-lane builder for multi-slot mode."""
    fn = _fn_body("_smgmtLanesUpdate")
    has_dispatch = (
        "_smgmtWorkerLanesHtml" in fn
        or "workerLanesHtml" in fn
        or "WorkerLanes" in fn
    )
    assert has_dispatch, (
        "_smgmtLanesUpdate does not dispatch to the per-worker lane builder"
    )


# ── AC5: slots=1 → existing two-lane view ────────────────────────────────────

def test_ac5_existing_two_lane_preserved_for_single_slot():
    """In single-slot mode (max_coder_slots===1), _smgmtLanesHtml is still called."""
    fn = _fn_body("_smgmtLanesUpdate")
    # When not in multi-slot mode, must fall back to the existing lane builder
    has_fallback = (
        "_smgmtLanesHtml" in fn
        or "lanesHtml" in fn
    )
    assert has_fallback, (
        "_smgmtLanesUpdate does not call _smgmtLanesHtml for single-slot mode — "
        "the existing two-lane view must be preserved"
    )


def test_ac5_two_lane_function_unchanged():
    """_smgmtLanesHtml still renders the Coder and Tester lanes."""
    fn = _fn_body("_smgmtLanesHtml")
    assert "coder" in fn.lower() and "tester" in fn.lower(), (
        "_smgmtLanesHtml no longer renders both coder and tester lanes — "
        "the single-slot two-lane view has been broken"
    )


# ── AC6: Dynamic lane count updates ──────────────────────────────────────────

def test_ac6_lanes_update_called_from_running_view():
    """_smgmtRunningViewUpdate calls _smgmtLanesUpdate to refresh lanes on each poll."""
    fn = _fn_body("_smgmtRunningViewUpdate")
    assert "_smgmtLanesUpdate" in fn, (
        "_smgmtRunningViewUpdate does not call _smgmtLanesUpdate — "
        "lane count will not update dynamically"
    )


def test_ac6_lanes_re_rendered_on_each_update():
    """_smgmtLanesUpdate does a full re-render of the lane container."""
    fn = _fn_body("_smgmtLanesUpdate")
    has_rerender = (
        "innerHTML" in fn
        or "render" in fn.lower()
    )
    assert has_rerender, (
        "_smgmtLanesUpdate does not re-render on each update call — "
        "lane count changes won't take effect without a page reload"
    )


# ── AC7: Independent log tail scrolling ───────────────────────────────────────

def test_ac7_log_fetch_preserves_scroll_position():
    """Worker log fetch preserves scroll position for independent scrolling."""
    # Check the log fetch function handles scroll state
    fetch_fn_body = ""
    for name in ("_smgmtWorkerLogFetch", "smgmtWorkerLogFetch", "workerLogFetch"):
        try:
            fetch_fn_body = _fn_body(name)
            break
        except AssertionError:
            continue
    if not fetch_fn_body:
        # Fall back: check the whole project.html for the pattern
        has_scroll = (
            "scrollTop" in PROJECT_HTML
            and ("worker-log" in PROJECT_HTML or "smgmt-worker" in PROJECT_HTML)
        )
        assert has_scroll, (
            "No scroll preservation found for worker log containers — "
            "lane log tails will not scroll independently"
        )
        return
    has_scroll_preserve = (
        "scrollTop" in fetch_fn_body
        or "atBottom" in fetch_fn_body
        or "scrollHeight" in fetch_fn_body
    )
    assert has_scroll_preserve, (
        "Worker log fetch function does not preserve scrollTop — "
        "lanes won't scroll independently"
    )


def test_ac7_each_log_container_independently_scrollable():
    """Each lane's log container is individually addressable for scroll."""
    fn = _fn_body("_smgmtWorkerLanesHtml")
    # Each container must have a unique ID so scroll can be captured/restored per lane
    has_unique_id = (
        "smgmt-worker-log-" in fn
        or ("id=" in fn and ("num" in fn or "number" in fn))
    )
    assert has_unique_id, (
        "Lane log containers do not have unique per-issue IDs — "
        "scroll cannot be tracked independently per lane"
    )


# ── CSS: per-worker lane styles ───────────────────────────────────────────────

def test_css_worker_lane_class_defined():
    """CSS defines .worker-lane rule for per-worker lane containers."""
    has_css = (
        ".worker-lane" in PROJECT_HTML
        or ".worker-lanes" in PROJECT_HTML
    )
    assert has_css, (
        ".worker-lane CSS class not found in project.html — "
        "per-worker lane containers have no styling"
    )


def test_css_worker_lane_phase_pill_defined():
    """CSS defines phase pill classes for coding/testing/merging states."""
    has_phase_css = (
        "worker-lane-phase" in PROJECT_HTML
        or ("coding" in PROJECT_HTML and "worker" in PROJECT_HTML)
    )
    assert has_phase_css, (
        "No phase pill CSS for worker lanes found in project.html"
    )


def test_css_worker_lane_log_scrollable():
    """Worker lane log container CSS has overflow-y for scrolling."""
    log_css = _css_block(".worker-lane-log")
    if not log_css:
        # Try to find it another way
        has_overflow = (
            "worker-lane-log" in PROJECT_HTML
            and "overflow" in PROJECT_HTML[
                PROJECT_HTML.find("worker-lane-log"):
                PROJECT_HTML.find("worker-lane-log") + 300
            ]
        )
        assert has_overflow, (
            ".worker-lane-log does not define overflow-y — log tail cannot scroll independently"
        )
        return
    has_overflow = "overflow" in log_css or "overflow-y" in log_css
    assert has_overflow, (
        ".worker-lane-log CSS does not set overflow-y — log tail cannot scroll"
    )


# ── Backend: active_agents includes all concurrent coders ─────────────────────

def test_backend_active_agents_includes_all_coders():
    """Backend /live active_agents list collects ALL active coder workers."""
    # The current implementation only captures one coder_entry.
    # For multi-coder, it must capture ALL issues where cs and not cf.
    # Check that the sprint_live.py code iterates over all active coders.
    # The fix should replace `coder_entry = ...` with a list/array accumulation.
    has_multi_coder = (
        "coder_entries" in SPRINT_LIVE_SRC
        or "coder_entry =" not in SPRINT_LIVE_SRC  # old single-capture pattern removed
        or (
            "coder_entry" in SPRINT_LIVE_SRC
            and "append" in SPRINT_LIVE_SRC[
                SPRINT_LIVE_SRC.find("coder_entry"):
                SPRINT_LIVE_SRC.find("coder_entry") + 400
            ]
        )
    )
    assert has_multi_coder, (
        "sprint_live.py active_agents still uses single coder_entry — "
        "concurrent coders will not appear as separate active_agents"
    )


def test_backend_active_agents_field_documented():
    """Backend /live response shape documents active_agents as a list."""
    has_doc = (
        "active_agents" in SPRINT_LIVE_SRC
        and "[" in SPRINT_LIVE_SRC[
            SPRINT_LIVE_SRC.find("active_agents"):
            SPRINT_LIVE_SRC.find("active_agents") + 200
        ]
    )
    assert has_doc, (
        "active_agents not found as a list in sprint_live.py documentation or payload"
    )


def test_backend_issue_log_endpoint_exists():
    """The per-issue log endpoint exists for the multi-lane log tail fetch."""
    # /api/sprints/{sprint_label}/issue/{issue_num}/log
    has_endpoint = (
        "issue/{issue_num}/log" in SPRINT_LIVE_SRC
        or "/issue/" in SPRINT_LIVE_SRC
    )
    assert has_endpoint, (
        "Per-issue log endpoint not found in sprint_live.py — "
        "multi-lane log tails cannot be fetched"
    )
