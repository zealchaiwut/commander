"""Tests for issue #803 — Add running metrics strip above the rail.

The feature has two halves, each tested at the layer it lives in:

* Backend — the ``/api/sprints/{label}/live`` snapshot is extended (via the
  extracted ``apps/dashboard/live_metrics.py`` helper) with agent_runs-derived
  metrics: fix-round count, literal token total, and per-agent elapsed time.
  These are unit-tested directly against a seeded SQLite ``agent_runs`` table.

* Frontend — five metric cards render above the level-rail in the Running
  sub-view (``apps/dashboard/static/project.html``). Following the pattern for
  frontend tickets (#800, #801, #802) the assertions read the static HTML/JS
  source and check the design-contract tokens and the builder's field sourcing.

AC coverage:
  AC1  — Five metric cards render above the rail: Done x/y, Active agents
         (mode), Fix rounds used, Tokens ≈ $, Agent time split.
  AC2  — Card values are sourced from live ``/live`` snapshot data.
  AC3  — ``/live`` is extended with fix-round count, token totals, and
         per-agent elapsed time when ``agent_runs`` exists.
  AC4  — Fix rounds card renders with an amber highlight when count > 0.
  AC5  — A card whose data source is absent (no agent_runs / no price map) is
         hidden — not errored or blank-broken.
  AC6  — No console errors when agent-time data is absent (guarded access).
  AC7  — Done and Active agents cards match the values shown in the rail.
  AC8  — Matches the mock v5 design contract: ``.metrics`` / ``.metric``
         variant class structure.
  AC9  — Token display uses BA Path-A literal token counts (no estimation).
  AC10 — Passes the impeccable gate: no banned patterns (side-stripe borders,
         gradient text) in the metric-strip styles.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")
SERVER_PY = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")


# ──────────────────────────── helpers ────────────────────────────────────────

def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a JS function defined in ``src``."""
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


def _seed_agent_runs(db_module, rows: list[dict]) -> None:
    """Create the agent_runs table in the test DB and insert the given rows."""
    with db_module.get_conn() as conn:
        db_module._create_agent_runs_table(conn)
        for r in rows:
            conn.execute(
                "INSERT INTO agent_runs "
                "(issue_number, sprint_label, agent, started_at, finished_at, "
                " duration_seconds, outcome, total_tokens, attempt_kind) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    r.get("issue_number", 1),
                    r["sprint_label"],
                    r["agent"],
                    r.get("started_at", "2026-06-11T00:00:00"),
                    r.get("finished_at", "2026-06-11T00:02:00"),
                    r.get("duration_seconds"),
                    r.get("outcome", "passed"),
                    r.get("total_tokens"),
                    r.get("attempt_kind", "initial"),
                ),
            )
        conn.commit()


@pytest.fixture()
def live_metrics_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at a fresh temp DB and return (live_metrics, db)."""
    import db as db_module
    import live_metrics
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test_803.db")
    return live_metrics, db_module


# ───────────── AC3: /live extended with agent_runs-derived metrics ────────────

def test_ac3_running_metrics_present_when_agent_runs_exist(live_metrics_db, monkeypatch):
    live_metrics, db_module = live_metrics_db
    monkeypatch.setattr(live_metrics, "_blended_rate", lambda project: None)
    _seed_agent_runs(db_module, [
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": 1000,
         "duration_seconds": 120, "attempt_kind": "initial"},
        {"sprint_label": "sprint-99", "agent": "tester", "total_tokens": 500,
         "duration_seconds": 60, "attempt_kind": "initial"},
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": 300,
         "duration_seconds": 30, "attempt_kind": "fix_round"},
    ])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    assert m, "metrics must be present when agent_runs rows exist"
    assert m["fix_rounds"] == 1, "one fix_round attempt → fix_rounds == 1"
    assert m["token_total"] == 1800, "token_total is the literal SUM(total_tokens)"
    assert m["agent_time_split"] == {"coder": 150, "tester": 60}, \
        "per-agent elapsed time sums duration_seconds by agent"


def test_ac3_metrics_scoped_to_the_sprint(live_metrics_db, monkeypatch):
    live_metrics, db_module = live_metrics_db
    monkeypatch.setattr(live_metrics, "_blended_rate", lambda project: None)
    _seed_agent_runs(db_module, [
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": 1000,
         "duration_seconds": 120},
        {"sprint_label": "sprint-100", "agent": "coder", "total_tokens": 9999,
         "duration_seconds": 999},
    ])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    assert m["token_total"] == 1000, "other sprints' agent_runs must not leak in"


# ───────────── AC5: cards hidden when their data source is absent ─────────────

def test_ac5_returns_empty_when_no_agent_runs(live_metrics_db):
    live_metrics, db_module = live_metrics_db
    # Table exists but holds no rows for this sprint.
    _seed_agent_runs(db_module, [])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    assert m == {}, "no agent_runs → empty dict so the /live contract is unchanged"


def test_ac5_no_token_cost_when_price_map_absent(live_metrics_db, monkeypatch):
    live_metrics, db_module = live_metrics_db
    monkeypatch.setattr(live_metrics, "_blended_rate", lambda project: None)
    _seed_agent_runs(db_module, [
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": 1000,
         "duration_seconds": 120},
    ])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    assert "token_cost_usd" not in m, "no price map → omit token cost (card hidden)"
    assert "token_total" in m, "literal token total is still present"


def test_ac5_token_cost_present_when_price_map_configured(live_metrics_db, monkeypatch):
    live_metrics, db_module = live_metrics_db
    monkeypatch.setattr(live_metrics, "_blended_rate", lambda project: 0.000002)
    _seed_agent_runs(db_module, [
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": 1000,
         "duration_seconds": 120},
        {"sprint_label": "sprint-99", "agent": "tester", "total_tokens": 800,
         "duration_seconds": 60},
    ])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    assert "token_cost_usd" in m, "price map configured → token cost present"
    assert m["token_cost_usd"] == round(1800 * 0.000002, 4)
    assert m["usd_per_token"] == 0.000002


# ───────────── AC9: literal token counts (no estimation) ──────────────────────

def test_ac9_token_total_is_literal_not_estimated(live_metrics_db, monkeypatch):
    live_metrics, db_module = live_metrics_db
    monkeypatch.setattr(live_metrics, "_blended_rate", lambda project: None)
    _seed_agent_runs(db_module, [
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": 1234,
         "duration_seconds": 10},
        {"sprint_label": "sprint-99", "agent": "tester", "total_tokens": 4321,
         "duration_seconds": 10},
    ])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    # Exact literal sum, not a size-bucket estimate.
    assert m["token_total"] == 1234 + 4321


def test_ac9_null_tokens_are_ignored_not_estimated(live_metrics_db, monkeypatch):
    live_metrics, db_module = live_metrics_db
    monkeypatch.setattr(live_metrics, "_blended_rate", lambda project: None)
    _seed_agent_runs(db_module, [
        {"sprint_label": "sprint-99", "agent": "coder", "total_tokens": None,
         "duration_seconds": 10},
        {"sprint_label": "sprint-99", "agent": "tester", "total_tokens": 700,
         "duration_seconds": 10},
    ])
    m = live_metrics.running_metrics("sprint-99", "owner/repo")
    assert m["token_total"] == 700, "rows with NULL tokens contribute 0, never an estimate"


# ───────────── compute_levels extracted (offsets server.py growth) ────────────

def test_compute_levels_extracted_to_helper(live_metrics_db):
    live_metrics, _ = live_metrics_db
    issues = [
        {"number": 1, "dispatch_level": 1, "status": "done"},
        {"number": 2, "dispatch_level": 2, "status": "in-progress", "agent": "coder"},
        {"number": 3, "dispatch_level": 3, "status": "pending"},
    ]
    levels = live_metrics.compute_levels(issues)
    assert [l["level"] for l in levels] == [1, 2, 3]
    assert levels[0]["state"] == "complete"
    assert levels[1]["state"] == "active"
    assert levels[2]["state"] == "waiting"
    assert levels[0]["merged"] == 1 and levels[0]["total"] == 1


# ───────────── server.py wiring: merge metrics, reuse helper ──────────────────

def _live_fn_src() -> str:
    start = SERVER_PY.index('@app.get("/api/sprints/{sprint_label}/live")')
    end = SERVER_PY.index('@app.get("/api/sprints/{sprint_label}/live/stream")', start)
    return SERVER_PY[start:end]


def test_ac3_live_endpoint_merges_running_metrics():
    live_fn = _live_fn_src()
    assert "running_metrics" in live_fn, \
        "the /live endpoint must merge the agent_runs-derived running metrics"


def test_ac3_live_endpoint_uses_extracted_compute_levels():
    live_fn = _live_fn_src()
    assert "compute_levels" in live_fn, \
        "the /live endpoint must call the extracted compute_levels helper"


def test_server_imports_live_metrics_module():
    assert (DASHBOARD_DIR / "live_metrics.py").exists(), "live_metrics helper module must exist"
    assert "live_metrics" in SERVER_PY, "server.py must import the live_metrics helper"


# ───────────── AC1: five metric cards above the rail ──────────────────────────

def test_ac1_metrics_strip_mounts_above_rail_in_running_subview():
    start = PROJECT_HTML.index('id="smgmt-subview-running"')
    end = PROJECT_HTML.index("smgmt-subview-running -->", start)
    subview = PROJECT_HTML[start:end]
    assert 'id="smgmt-metrics"' in subview, "metrics strip must mount in the Running sub-view"
    assert 'class="metrics"' in subview, "metrics container must carry the `.metrics` class"
    # Above the rail: the metrics mount precedes the rail mount in document order.
    assert subview.index('id="smgmt-metrics"') < subview.index('id="smgmt-rail"'), \
        "metrics strip must render above the rail"


def test_ac1_builder_emits_all_five_cards():
    body = _fn_body("_smgmtMetricsHtml")
    # Note: The Done card was moved to the segmented gauge in the header (issue #1107).
    # The strip now emits four cards: Active agents, Retrying, Tokens, Agent time.
    for variant in ("metric-active", "metric-fix", "metric-tokens", "metric-time"):
        assert variant in body, f"builder must emit the `{variant}` card"
    for label in ("Active agents", "Agent time"):
        assert label in body, f"builder must label the `{label}` card"


# ───────────── AC2: card values sourced from the /live snapshot ───────────────

def test_ac2_builder_reads_live_snapshot_fields():
    body = _fn_body("_smgmtMetricsHtml")
    for field in ("done_count", "total_count", "active_agents", "pipeline_mode",
                  "fix_rounds", "token_total", "agent_time_split"):
        assert field in body, f"builder must source `{field}` from the live snapshot"


# ───────────── AC4: fix-rounds amber highlight ────────────────────────────────

def test_ac4_fix_card_gets_amber_class_when_positive():
    body = _fn_body("_smgmtMetricsHtml")
    # Amber class is applied conditionally on a positive fix-round count.
    assert "amber" in body, "fix card must take an amber variant"
    assert re.search(r"fix_rounds.*?>\s*0", body) or "> 0" in body or ">0" in body, \
        "amber highlight must be gated on fix_rounds > 0"


def test_ac4_amber_is_full_border_bg_not_side_stripe():
    rule = _css_rule(".metric.amber")
    assert "--amber" in rule, "amber highlight must use the amber token"
    # Impeccable ban: no coloured side-stripe; use a full border + background tint.
    assert "border-left" not in rule and "border-right" not in rule, \
        "amber highlight must not be a side-stripe border"
    assert "background" in rule or "border-color" in rule or "border:" in rule


# ───────────── AC5/AC6: guarded rendering, no console errors ──────────────────

def test_ac5_cards_are_guarded_on_source_presence():
    body = _fn_body("_smgmtMetricsHtml")
    # Fix-rounds / agent-time gated on agent_runs presence; tokens on cost presence.
    assert "agent_runs_present" in body, "agent_runs-sourced cards must be feature-detected"
    assert "token_cost_usd" in body, "tokens card must be gated on a configured cost"


def test_ac6_agent_time_access_is_guarded():
    body = _fn_body("_smgmtMetricsHtml")
    # The agent_time_split block must check presence before reading .coder/.tester,
    # so an absent source can never throw (no console errors).
    assert re.search(r"if\s*\(\s*live\.agent_time_split", body), \
        "agent-time card must guard on `live.agent_time_split` before reading it"


# ───────────── AC7: Done / Active cards match the rail ────────────────────────

def test_ac7_done_card_matches_rail_done_count():
    # The Done card was moved to the segmented gauge (issue #1107). The gauge shows
    # done_count / total_count using the same source; verify the gauge function uses them.
    gauge_fn = "_smgmtGaugeHtml"
    gauge_body = _fn_body(gauge_fn) if gauge_fn in PROJECT_HTML else ""
    counts_fn = "_smgmtGaugeCounts"
    counts_body = _fn_body(counts_fn) if counts_fn in PROJECT_HTML else ""
    combined = gauge_body + counts_body
    assert "done_count" in combined and "total_count" in combined, (
        "Done count must still come from snapshot done_count / total_count "
        "(now in the gauge header, not the metrics strip)"
    )


def test_ac7_active_card_matches_rail_active_agents():
    body = _fn_body("_smgmtMetricsHtml")
    assert "active_agents" in body, "Active agents card must use the rail's active_agents"
    assert "pipeline_mode" in body, "Active agents card must show the run mode"


# ───────────── AC8: design contract .metrics / .metric ────────────────────────

def test_ac8_metrics_and_metric_classes_styled():
    _css_rule(".metrics")  # raises if missing
    _css_rule(".metric")   # raises if missing


def test_ac8_metric_variants_present():
    # Variant class structure per the mock v5 contract.
    for cls in ("metric-done", "metric-active", "metric-fix", "metric-tokens", "metric-time"):
        assert cls in PROJECT_HTML, f"variant `{cls}` must exist in the markup contract"


def test_ac8_update_is_wired_into_poll_and_subview():
    poll = _fn_body("_smgmtLivePollTick")
    assert "_smgmtRunningViewUpdate" in poll or "_smgmtMetricsUpdate" in poll, \
        "metrics must refresh on the live poll"
    switch = _fn_body("_smgmtShowSubView")
    assert "_smgmtRunningViewUpdate" in switch or "_smgmtMetrics" in switch, \
        "switching to Running must render the running shell from cache"


# ───────────── AC10: impeccable — no banned patterns ──────────────────────────

def test_ac10_no_gradient_text_in_metric_styles():
    for cls in (".metrics", ".metric", ".metric-value", ".metric-label"):
        try:
            rule = _css_rule(cls)
        except AssertionError:
            continue
        assert "background-clip" not in rule and "-webkit-background-clip" not in rule, \
            f"gradient text (background-clip) is banned in `{cls}`"


def test_ac10_no_side_stripe_in_metric_card():
    rule = _css_rule(".metric")
    assert "border-left" not in rule and "border-right" not in rule, \
        "metric cards must not use side-stripe borders (impeccable ban)"


# ───────────── Per-ticket elapsed sums all agent_runs attempts ───────────────

def test_issue_elapsed_secs_sums_all_agent_runs_attempts():
    """Fix-round retries must accumulate, not show only the latest coder window."""
    import live_metrics as lm

    now = datetime.now(timezone.utc)
    by_issue = {
        802: [
            {"duration_seconds": 600, "finished_at": "2026-06-11T00:10:00", "started_at": "2026-06-11T00:00:00"},
            {"duration_seconds": 120, "finished_at": "2026-06-11T00:12:00", "started_at": "2026-06-11T00:10:00"},
            {"duration_seconds": 218, "finished_at": "2026-06-11T00:15:38", "started_at": "2026-06-11T00:12:00"},
        ],
    }
    iss = {
        "number": 802,
        # Fix round reset coder_started_at — timestamp-only math would under-count.
        "coder_started_at": "2026-06-11T00:12:00Z",
        "tester_finished_at": None,
    }
    assert lm.issue_elapsed_secs(iss, now, by_issue) == 938
