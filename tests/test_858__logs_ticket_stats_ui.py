"""Tests for issue #858 — per-agent timing and error detail on the Logs tab (frontend half).

The expanded Logs-tab run gains a per-ticket stats strip rendered above the raw
event lines: one row per ticket with a coder time, a tester time, a token count,
and — for failed tickets only — an inline failure class + message. The data is
lazily fetched from ``GET /api/logs/runs/{label}/ticket-stats`` the first time a
run is expanded.

Following the History frontend test pattern (#810) the assertions read the
static HTML/JS source and check the builder's field sourcing, the dash
degradation, and the design-contract classes.

AC coverage (frontend):
  AC1/AC2 — the row builder renders coder + tester durations.
  AC3     — the row builder renders the token count.
  AC4     — failed rows render failure class + message inline; passing rows do not.
  AC5     — null durations / tokens render as a dash (—), guarded.
  AC6     — the strip is fetched from the ticket-stats endpoint on run expand.
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


def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    for needle in (f"function {name}(", f"{name} = function", f"async function {name}("):
        pos = src.find(needle)
        if pos != -1:
            break
    assert pos != -1, f"function {name} not found"
    brace = src.find("{", pos)
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


# ── AC6: the strip is fetched from the ticket-stats endpoint ──────────────────

def test_ticket_stats_endpoint_is_fetched():
    assert "/api/logs/runs/" in PROJECT_HTML
    assert "ticket-stats" in PROJECT_HTML


def test_fetch_helper_exists():
    body = _fn_body("_logsFetchTicketStats")
    assert "ticket-stats" in body
    assert "fetch(" in body


# ── AC1/AC2/AC3: the row builder renders both durations and the token count ───

def test_row_builder_renders_durations_and_tokens():
    body = _fn_body("_logsTicketStatsHtml")
    # per-agent durations + token count are all sourced from the item fields
    assert "coder_seconds" in body
    assert "tester_seconds" in body
    assert "total_tokens" in body


# ── AC5: null durations / tokens render as a dash, guarded ────────────────────

def test_dash_degradation_for_null_values():
    body = _fn_body("_logsTicketStatsHtml")
    # the builder routes timing/token cells through the dash-aware formatters
    assert "_histFmtSecs" in body            # returns '—' for null
    assert ("_logsFmtTokens" in body) or ("—" in body)


# ── AC4: failure class + message render inline, only for failed rows ──────────

def test_failure_class_and_message_render_inline():
    body = _fn_body("_logsTicketStatsHtml")
    assert "failure_class" in body
    assert "failure_message" in body
    # rendered conditionally on the failed flag so passing rows stay uncluttered
    assert "failed" in body


def test_failure_cell_has_design_contract_class():
    """A dedicated class ties the inline failure detail to the batch styling."""
    assert "logs-ticket-fail" in PROJECT_HTML or "logs-ticket-failure" in PROJECT_HTML


def test_ticket_row_has_design_contract_class():
    assert "logs-ticket-row" in PROJECT_HTML


# ── Impeccable bans (copy hygiene in the added markup) ────────────────────────

def test_no_em_dash_in_added_copy():
    """The em dash (—) is allowed as the empty-value glyph but not in prose labels."""
    body = _fn_body("_logsTicketStatsHtml")
    # strip the legitimate dash glyph usages, then ensure no ' — ' prose dashes
    prose = body.replace("'—'", "").replace('"—"', "").replace("`—`", "")
    assert " — " not in prose
