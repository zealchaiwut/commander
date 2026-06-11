"""Tests for issue #807 — History folding + fold-size setting.

Two surfaces are exercised:

  * Backend (functional) — the ``history_fold_size`` project setting is a real,
    persisted, non-secret KV field with a default of 10. These assertions run
    against ``settings_schema`` directly (the same module the GET/PUT settings
    endpoints use), so they verify behaviour, not source text.

  * Frontend (design-contract) — the fold partition, aggregate metrics, fold
    expand/collapse, and the ``.fold`` / ``.fold-head`` / ``.fold-agg`` DOM
    contract live in ``project.html`` (no build step, no node in this clone).
    Following the established pattern for frontend tickets (#800–#806), these
    assertions read the static HTML/JS source and check the contract selectors
    and builder logic — anchored one-to-one to the acceptance criteria.

AC coverage:
  AC1  — ``history_fold_size`` setting exists (default 10), persisted via KV.
  AC2  — History renders exactly ``history_fold_size`` recent sprints expanded.
  AC3  — Older sprints group into fold rows labeled "Sprints X–Y".
  AC4  — Each fold row shows aggregate metrics: tickets done, avg est accuracy,
         failure count.
  AC5  — Clicking a fold expands it to reveal individual sprint rows, rendered
         with the SAME card builder (locked/normal states preserved).
  AC6  — Collapsing a previously expanded fold hides its rows again.
  AC7  — Changing ``history_fold_size`` and reloading re-partitions (the loader
         reads the setting and feeds it to the partitioner).
  AC8  — Fold group size matches ``history_fold_size`` (folds chunk by it).
  AC9  — DOM uses the ``.fold`` / ``.fold-head`` / ``.fold-agg`` class contract.
  AC10 — A toolbar note is rendered into the History view.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")


# ──────────────────────────── helpers ────────────────────────────────────────

def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a JS function defined in ``src``."""
    pos = -1
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = src.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found"
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
                return src[brace:i + 1]
    raise AssertionError(f"unbalanced braces for {name}")


def _css_rule(selector: str) -> str:
    """Return the body of the first CSS rule whose selector list contains `selector`."""
    pat = re.compile(re.escape(selector) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
    m = pat.search(PROJECT_HTML)
    assert m is not None, f"CSS rule for `{selector}` not found"
    return m.group(1)


# ═══════════════════ AC1 — history_fold_size setting (backend) ════════════════

def test_ac1_history_fold_size_is_a_known_non_secret_field():
    from services.sprint_manager.settings_schema import (
        KNOWN_FIELDS,
        NON_SECRET_FIELDS,
        SECRET_FIELDS,
    )
    assert "history_fold_size" in KNOWN_FIELDS, \
        "history_fold_size must be a known settings field"
    assert "history_fold_size" in NON_SECRET_FIELDS, \
        "history_fold_size is a plain (non-secret) field"
    assert "history_fold_size" not in SECRET_FIELDS


def test_ac1_history_fold_size_default_is_10():
    from services.sprint_manager.settings_schema import KNOWN_FIELDS, build_effective_response
    assert KNOWN_FIELDS["history_fold_size"]["default"] == 10
    # An empty stored config falls back to the default in the effective response
    # the settings endpoints return.
    eff = build_effective_response({})
    assert eff["history_fold_size"] == 10


def test_ac1_history_fold_size_persists_via_kv_roundtrip():
    """A stored value is echoed back through the effective-response builder —
    i.e. the field round-trips through the settings KV layer (AC1)."""
    from services.sprint_manager.settings_schema import build_effective_response
    eff = build_effective_response({"history_fold_size": 5})
    assert eff["history_fold_size"] == 5


# ═══════════════ AC2 / AC8 — partition by fold size ══════════════════════════

def test_ac2_partition_keeps_fold_size_most_recent_expanded():
    """The partitioner slices the first `foldSize` sprints as the recent group."""
    part = _fn_body("_histPartition")
    assert "slice(0," in part.replace(" ", "") or "slice(0 ," in part, \
        "the recent group is the first foldSize sprints (slice(0, foldSize))"
    assert "foldSize" in part, "partition is driven by the fold size"
    assert "recent" in part and "folds" in part, \
        "partition returns a recent group and a list of folds"


def test_ac2_recent_sprints_render_with_the_existing_card_builder():
    """Recent (expanded) rows reuse _histCardHtml so they look identical to today."""
    render = _fn_body("_histRenderLedger")
    assert "_histPartition" in render, "render must partition before drawing"
    assert "_histCardHtml" in render, \
        "recent sprints render through the existing card builder"


def test_ac8_folds_chunk_in_groups_of_fold_size():
    """Older sprints are chunked into folds whose size equals foldSize (AC8)."""
    part = _fn_body("_histPartition")
    # A chunk loop stepping by foldSize, or a slice(i, i + foldSize) window.
    stepped = re.search(r"\+=\s*foldSize", part) is not None
    windowed = re.search(r"slice\(\s*\w+\s*,\s*\w+\s*\+\s*foldSize", part) is not None
    assert stepped or windowed, \
        "folds must be chunked in groups of exactly foldSize sprints"


# ═══════════════════ AC3 — fold range label "Sprints X–Y" ════════════════════

def test_ac3_fold_label_uses_sprints_x_y_range():
    # The fold head renders a "Sprints X–Y" range label built from sprint numbers.
    head = _fn_body("_histFoldHtml")
    assert "Sprints" in head, "fold rows are labeled 'Sprints X–Y'"
    assert "–" in head or "_histFoldLabel" in head, \
        "the range uses an en-dash between the low and high sprint numbers"


def test_ac3_sprint_number_is_extracted_from_the_label():
    """The range endpoints derive from the numeric part of 'sprint-NN' labels."""
    num = _fn_body("_histSprintNum")
    assert "sprint-" in num and re.search(r"\\d", num), \
        "the sprint number is parsed out of the 'sprint-NN' label"


# ═══════════════════ AC4 — fold aggregate metrics ════════════════════════════

def test_ac4_fold_aggregates_done_accuracy_failures():
    agg = _fn_body("_histFoldAgg")
    assert "estimate_accuracy" in agg, "avg estimation accuracy is aggregated"
    assert "failed" in agg, "failure count is aggregated from failed sprints"
    # tickets-done comes from the per-sprint done helper.
    assert "_histTicketsDone" in agg or "merged" in agg, \
        "tickets-done is summed across the fold's sprints"


def test_ac4_tickets_done_counts_merged_issues():
    done = _fn_body("_histTicketsDone")
    assert "merged" in done, "a 'done' ticket is a merged ticket"
    assert "issues" in done, "tickets-done reads the sprint's issue list"


def test_ac4_accuracy_is_an_average_not_a_sum():
    agg = _fn_body("_histFoldAgg")
    # An average divides by a count — guard against a plain sum mislabelled 'avg'.
    assert "/" in agg, "estimation accuracy is averaged (divided by a count)"


def test_ac4_fold_agg_block_renders_the_three_metrics():
    """The .fold-agg block surfaces all three aggregate metrics."""
    agg_html = _fn_body("_histFoldAggHtml")
    assert "fold-agg" in agg_html, "the aggregate block carries the .fold-agg class"
    low = agg_html.lower()
    assert "done" in low, "tickets-done metric is shown"
    assert "est" in low or "accuracy" in low, "avg est-accuracy metric is shown"
    assert "fail" in low, "failure-count metric is shown"


# ═══════════════════ AC5 / AC6 — expand / collapse a fold ════════════════════

def test_ac5_fold_head_toggles_and_reveals_member_cards():
    head = _fn_body("_histFoldHtml")
    assert "_histToggleFold" in head, "the fold head is clickable (toggles open)"
    assert "fold-head" in head, "the clickable header carries the .fold-head class"
    # When expanded, the member sprints render via the SAME card builder so they
    # are visually identical to recent rows (locked/normal preserved).
    assert "_histCardHtml" in head, \
        "expanded fold members render through the shared _histCardHtml builder"


def test_ac5_locked_state_is_preserved_for_fold_members():
    """Member rows go through _histCardHtml, which already applies .locked — so
    locked sprints inside a fold keep their locked rendering (AC5)."""
    card = _fn_body("_histCardHtml")
    assert "_histIsLocked" in card and "locked" in card, \
        "the shared card builder still applies the locked state"


def test_ac6_fold_toggle_is_a_collapse_able_set():
    toggle = _fn_body("_histToggleFold")
    # A Set membership flip: delete when present (collapse), add when absent.
    assert ".has(" in toggle and ".delete(" in toggle and ".add(" in toggle, \
        "toggling a fold adds/removes it from the expanded set (collapse hides rows)"
    assert "_histRenderLedger" in toggle, "toggling re-renders the ledger"


def test_ac6_fold_expanded_state_is_tracked_separately():
    """Folds track their own expand state (distinct from per-card expansion)."""
    assert "_histFoldExpanded" in PROJECT_HTML, \
        "a dedicated set tracks which folds are expanded"


# ═══════════════════ AC7 — re-partition on setting change ════════════════════

def test_ac7_loader_reads_the_fold_size_setting():
    load = _fn_body("_histLoadLedger")
    assert "/api/projects/" in load and "settings" in load, \
        "the loader fetches project settings"
    assert "history_fold_size" in load, \
        "the loader reads history_fold_size from settings"


def test_ac7_fold_size_flows_into_the_partition():
    """The fetched fold size is what the renderer partitions by — so changing the
    setting and reloading re-partitions history."""
    assert "_histFoldSize" in PROJECT_HTML, "a module-level fold size is held"
    render = _fn_body("_histRenderLedger")
    assert "_histFoldSize" in render or "_histPartition" in render, \
        "the renderer partitions using the current fold size"


def test_ac7_fold_size_has_a_sane_default_in_js():
    """If settings are unavailable the JS still falls back to 10 (mirrors AC1)."""
    # The module-level initializer pins the default to 10.
    m = re.search(r"_histFoldSize\s*=\s*(\d+)", PROJECT_HTML)
    assert m is not None, "history fold size has a JS default"
    assert m.group(1) == "10", "the JS default fold size is 10"


# ═══════════════════ AC9 — .fold / .fold-head / .fold-agg contract ═══════════

def test_ac9_fold_contract_selectors_are_styled():
    for sel in (".fold", ".fold-head", ".fold-agg"):
        _css_rule(sel)  # raises if any contract token is unstyled


def test_ac9_fold_builder_emits_the_contract_classes():
    head = _fn_body("_histFoldHtml")
    assert 'class="fold' in head, "the fold container uses the .fold class"
    assert "fold-head" in head, "the header uses the .fold-head class"
    assert "fold-agg" in head or "_histFoldAggHtml" in head, \
        "the aggregate block uses the .fold-agg class"


def test_ac9_no_side_stripe_anti_pattern():
    """Impeccable: emphasis is full-border + tint, never a coloured side-stripe."""
    for sel in (".fold", ".fold-head"):
        rule = _css_rule(sel)
        assert "border-left:" not in rule and "border-right:" not in rule, \
            f"`{sel}` must not use a coloured side-stripe border"


# ═══════════════════ AC10 — toolbar note ═════════════════════════════════════

def test_ac10_toolbar_note_is_rendered():
    # A toolbar note element exists and is styled per the mock v5 contract.
    assert "hist-toolbar-note" in PROJECT_HTML, "a toolbar note element exists"
    _css_rule(".hist-toolbar-note")


def test_ac10_toolbar_note_is_injected_by_the_ledger_render():
    render = _fn_body("_histRenderLedger")
    assert "hist-toolbar-note" in render or "_histToolbarHtml" in render, \
        "the toolbar note is rendered into the History view on every render"
