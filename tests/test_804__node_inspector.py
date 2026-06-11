"""Tests for issue #804 — Add node inspector panel with per-issue log tabs.

The feature spans two layers, each tested where it lives:

* Backend — a ``/logs/tail?sprint=&issue=&project=`` endpoint in the Run Browser
  router module (``apps/dashboard/routers/runs.py``), NOT ``server.py``. It
  reuses ``log_source.read_log(kind="issue")`` to stream ``sprint-issue-<N>.log``
  keyed by sprint + issue only. Tested with a TestClient over the router and a
  stub server (no heavy monolith import).

* Frontend — the inspector panel in ``apps/dashboard/static/project.html``.
  Following the established pattern for frontend tickets (#800–#803), the
  assertions read the static HTML/JS source and check the design-contract
  tokens, the builder functions, and their state logic — anchored one-to-one to
  the acceptance criteria.

AC coverage:
  AC1  — Clicking a node opens the inspector and reflects the ticket's title,
         size, and calibrated estimate.
  AC2  — Attempt timeline: failed → gate-reason chip; running → failure sidecar;
         queued testers listed.
  AC3  — Log tabs: one Orchestrator tab + one per active/selected issue, chips
         coloured per the lg-* agent-colour tokens.
  AC4  — Each per-issue log tab streams from sprint-issue-<N>.log via a
         /logs/tail?sprint=&issue= endpoint.
  AC5  — Issue refs (#N) and agent names in log lines are tokenized + coloured
         (shared colorizeLogLine, BA Path-A literal tokens).
  AC6  — All log content is HTML-escaped before rendering — no raw HTML executes.
  AC7  — With no attempt history / failure records, the timeline degrades to a
         single current-state row without error.
  AC8  — Backend endpoint lives in a router module (not server.py), structured
         to be absorbed by the M5 run-browser surface; the existing run-browser
         router is reused.
  AC9  — Panel layout targets the .inspector / .attempts / .att / .log-tabs /
         .log-body CSS contract from mock v5.
"""
from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = (DASHBOARD_DIR / "static" / "project.html").read_text(encoding="utf-8")
SERVER_PY = (DASHBOARD_DIR / "server.py").read_text(encoding="utf-8")
RUNS_PY = (ROUTERS_DIR / "runs.py").read_text(encoding="utf-8")
RUNS_SERVICE_PY = (ROUTERS_DIR / "runs_service.py").read_text(encoding="utf-8")


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


def _load_module(name: str, path: Path):
    """Load a Python file as a module without triggering package __init__."""
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class _StubServer:
    """Minimal stand-in for the monolith, so the router resolves paths without
    importing the heavy server.py module under test."""
    _SPRINT_LABEL_RE = re.compile(r"^sprint-\d+$")

    def __init__(self, root: Path):
        self._root = root

    def _project_root_path(self, project: str) -> Path:  # noqa: D401 — stub
        return self._root


@pytest.fixture()
def runs_client(tmp_path, monkeypatch):
    """A TestClient over the runs router with project-root resolution stubbed to
    a temp project that owns a .commander/logs/ dir. Returns (client, logs_dir)."""
    for mod_name in ("routers.runs_service", "routers.runs"):
        sys.modules.pop(mod_name, None)
    runs_service = _load_module("routers.runs_service", ROUTERS_DIR / "runs_service.py")
    runs = _load_module("routers.runs", ROUTERS_DIR / "runs.py")

    logs_dir = tmp_path / ".commander" / "logs"
    logs_dir.mkdir(parents=True)
    monkeypatch.setattr(runs_service, "_server", lambda: _StubServer(tmp_path))

    app = FastAPI()
    app.include_router(runs.router)
    return TestClient(app, raise_server_exceptions=True), logs_dir


# ═══════════════════════════════ Backend ══════════════════════════════════════
# AC4 / AC6 / AC8 — the /logs/tail endpoint.

def test_ac8_logs_tail_route_in_router_not_server():
    """The endpoint lives in the runs router module, never in the monolith."""
    assert '@router.get("/logs/tail")' in RUNS_PY, "/logs/tail must be declared in runs.py"
    assert "/logs/tail" not in SERVER_PY, "/logs/tail must NOT be added to server.py (monolith gate)"


def test_ac8_runs_router_mounted_and_registered():
    """The reused run-browser router stays mounted; no new router is needed."""
    init_src = (ROUTERS_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "runs_router" in init_src
    assert "runs_router" in SERVER_PY, "runs_router must remain mounted in server.py"


def test_ac8_endpoint_reuses_read_log_issue_kind():
    """The service reuses log_source.read_log(kind='issue') — sprint-issue-<N>.log."""
    assert "read_log" in RUNS_SERVICE_PY, "must reuse log_source.read_log"
    assert '"issue"' in RUNS_SERVICE_PY or "'issue'" in RUNS_SERVICE_PY, \
        "must read the issue-kind log (sprint-issue-<N>.log)"


def test_ac4_logs_tail_streams_sprint_issue_log(runs_client):
    """A per-issue log tab streams the last lines of sprint-issue-<N>.log."""
    client, logs_dir = runs_client
    (logs_dir / "sprint-issue-42.log").write_text("hello #42 from coder\nsecond line\n", encoding="utf-8")
    resp = client.get("/logs/tail", params={"sprint": "sprint-1", "issue": 42, "project": "owner/repo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert "hello #42 from coder" in body["tail"]
    assert body["path"].endswith("sprint-issue-42.log")


def test_ac4_logs_tail_keyed_by_issue(runs_client):
    """The endpoint resolves the correct file for the requested issue number."""
    client, logs_dir = runs_client
    (logs_dir / "sprint-issue-1.log").write_text("ONE\n", encoding="utf-8")
    (logs_dir / "sprint-issue-2.log").write_text("TWO\n", encoding="utf-8")
    r1 = client.get("/logs/tail", params={"sprint": "sprint-9", "issue": 1, "project": "p"}).json()
    r2 = client.get("/logs/tail", params={"sprint": "sprint-9", "issue": 2, "project": "p"}).json()
    assert "ONE" in r1["tail"] and "TWO" not in r1["tail"]
    assert "TWO" in r2["tail"] and "ONE" not in r2["tail"]


def test_ac6_backend_returns_script_payload_verbatim_as_data(runs_client):
    """A log line with <script> is returned as JSON string data — never executed
    server-side; the client escapes it before rendering (see AC6 frontend)."""
    client, logs_dir = runs_client
    payload = "<script>alert(1)</script>"
    (logs_dir / "sprint-issue-7.log").write_text(payload + "\n", encoding="utf-8")
    body = client.get("/logs/tail", params={"sprint": "sprint-1", "issue": 7, "project": "p"}).json()
    # Returned verbatim as inert data inside the JSON tail field.
    assert payload in body["tail"]


def test_ac8_invalid_sprint_label_rejected(runs_client):
    """A malformed sprint label (path-traversal guard) is rejected with 400."""
    client, _ = runs_client
    resp = client.get("/logs/tail", params={"sprint": "../etc", "issue": 1, "project": "p"})
    assert resp.status_code == 400


def test_ac7_backend_missing_log_degrades_not_errors(runs_client):
    """No sprint-issue log on disk → found:false with an empty tail, never a 500."""
    client, _ = runs_client
    resp = client.get("/logs/tail", params={"sprint": "sprint-1", "issue": 999, "project": "p"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["tail"] == ""


# ═══════════════════════════════ Frontend ═════════════════════════════════════
# AC1 — inspector opens + reflects title / size / calibrated estimate.

def test_ac1_inspector_mount_in_running_subview():
    start = PROJECT_HTML.index('id="smgmt-subview-running"')
    end = PROJECT_HTML.index("smgmt-subview-running -->", start)
    subview = PROJECT_HTML[start:end]
    assert 'id="smgmt-inspector"' in subview, "inspector must mount in the Running sub-view"
    assert 'class="inspector"' in subview


def test_ac1_node_click_feeds_inspector():
    body = _fn_body("_smgmtRailSelectNode")
    assert "_smgmtInspectorFeed" in body, "clicking a node must feed the inspector"


def test_ac1_feed_reflects_title_size_and_calibrated_estimate():
    body = _fn_body("_smgmtInspectorFeed")
    assert "inspector-title" in body and "title" in body, "must show the ticket title"
    assert "inspector-size" in body and "size" in body, "must show the size"
    # Calibrated estimate is the per-ticket minutes from the live snapshot.
    assert "inspector-est" in body and "minutes" in body, "must show the calibrated estimate"


def test_ac1_feed_opens_the_panel():
    body = _fn_body("_smgmtInspectorFeed")
    assert "hidden = false" in body.replace(" ", " "), "feeding the inspector must reveal the panel"


# AC2 — attempt timeline states.

def test_ac2_attempts_builder_exists():
    assert "function _smgmtInspectorAttempts(" in PROJECT_HTML
    assert "function _smgmtInspectorAttemptsHtml(" in PROJECT_HTML


def test_ac2_failed_attempt_has_gate_reason_chip():
    html = _fn_body("_smgmtInspectorAttemptsHtml")
    assert "att-gate" in html, "failed attempts must render a gate-reason chip"
    builder = _fn_body("_smgmtInspectorAttempts")
    assert "gate_reason" in builder, "attempt rows must carry a gate reason"


def test_ac2_running_attempt_has_failure_sidecar():
    html = _fn_body("_smgmtInspectorAttemptsHtml")
    assert "att-sidecar" in html, "running attempts must carry a failure sidecar"
    assert "running" in html


def test_ac2_queued_testers_listed():
    assert "function _smgmtInspectorQueuedTesters(" in PROJECT_HTML
    html = _fn_body("_smgmtInspectorAttemptsHtml")
    assert "att-queued" in html, "queued testers must be listed in the timeline block"
    assert "queued_testers" in _fn_body("_smgmtInspectorQueuedTesters")


def test_ac2_gate_and_sidecar_chips_styled():
    gate = _css_rule(".att-gate")
    assert "--red" in gate, "gate-reason chip should read as a failure (red token)"
    _css_rule(".att-sidecar")  # raises if unstyled


# AC3 — log tabs: Orchestrator + per active/selected issue, lg-* coloured chips.

def test_ac3_tabs_builder_includes_orchestrator_and_issues():
    body = _fn_body("_smgmtInspectorTabs")
    assert "orchestrator" in body.lower(), "an Orchestrator tab is always present"
    # Active = agent running / assigned, plus the inspected ticket.
    assert "agent_status" in body and "running" in body, "active issues get their own tab"


def test_ac3_tab_chips_use_lg_tokens():
    html = _fn_body("_smgmtInspectorTabsHtml")
    assert "lg-" in html, "tab chips must use the lg-* agent-colour tokens"
    # The lg-* tokens are defined in CSS for the orchestrator + the agents.
    for cls in (".lg-orchestrator", ".lg-coder", ".lg-tester"):
        _css_rule(cls)


def test_ac3_log_tabs_container_styled():
    _css_rule(".log-tabs")


# AC4 — per-issue tab streams from /logs/tail?sprint=&issue=.

def test_ac4_stream_hits_logs_tail_endpoint():
    body = _fn_body("_smgmtInspectorStreamTick")
    assert "/logs/tail?sprint=" in body, "per-issue tab must stream from /logs/tail?sprint=&issue="
    assert "issue=" in body
    # The orchestrator tab streams the dispatch (spine) log.
    assert "dispatch-log" in body, "the Orchestrator tab streams the dispatch log"


def test_ac4_stream_is_polled():
    restart = _fn_body("_smgmtInspectorStreamRestart")
    assert "setInterval" in restart and "_smgmtInspectorStreamTick" in restart, \
        "the active log tab must stream on a poll"


# AC5 — issue refs + agent names tokenized and coloured (shared colorizer).

def test_ac5_log_lines_run_through_colorizer():
    body = _fn_body("_smgmtInspectorStreamTick")
    assert "colorizeLogLine" in body, "log lines must be tokenized via the shared colorizer"


def test_ac5_colorizer_tokenizes_issue_and_agents():
    """The shared colorizer (BA Path-A literal tokens) handles #N and agent names."""
    colorize_src = (DASHBOARD_DIR / "static" / "log-colorize.js").read_text(encoding="utf-8")
    assert "#" in colorize_src and "log-chip--issue" in colorize_src, "issue refs tokenized"
    assert "log-agent-" in colorize_src, "agent names tokenized to colour chips"


# AC6 — all log content HTML-escaped; no raw HTML executes.

def test_ac6_stream_escapes_via_colorizer_not_raw_tail():
    body = _fn_body("_smgmtInspectorStreamTick")
    # Lines are rendered through colorizeLogLine (which escapes first); the raw
    # `tail` string is never injected straight into innerHTML. The only safe path
    # is splitting per-line and colorizing each line — `tail` reaching innerHTML
    # without that per-line split is the anti-pattern.
    assert "colorizeLogLine(line" in body, "each line must pass through the escaping colorizer"
    assert re.search(r"innerHTML\s*=\s*tail\b(?!\s*\.split)", body) is None, \
        "the un-escaped tail must never be assigned to innerHTML directly"


def test_ac6_colorizer_escapes_first():
    colorize_src = (DASHBOARD_DIR / "static" / "log-colorize.js").read_text(encoding="utf-8")
    assert "escapeLogHtml" in colorize_src
    assert "&lt;" in colorize_src and "&amp;" in colorize_src, "colorizer must HTML-escape"


# AC7 — degrade to a single current-state row when no history.

def test_ac7_attempts_degrade_to_single_row():
    body = _fn_body("_smgmtInspectorAttempts")
    # Feature-detects a rich `attempts` history; falls back to one current row.
    assert "attempts" in body, "rich attempt history is feature-detected"
    assert "current" in body, "absence of history degrades to a single current-state row"


def test_ac7_attempts_builder_never_indexes_unguarded():
    """The fallback must not assume any optional field exists (no crash)."""
    body = _fn_body("_smgmtInspectorAttempts")
    # Guards the optional history with Array.isArray before using it.
    assert "Array.isArray" in body, "attempt history access must be guarded"


# AC9 — CSS contract from mock v5.

def test_ac9_contract_targets_styled():
    for cls in (".inspector", ".attempts", ".att", ".log-tabs", ".log-body"):
        _css_rule(cls)  # raises if any contract token is unstyled


def test_ac9_no_side_stripe_anti_pattern_in_inspector_css():
    """Impeccable: emphasis is full-border + tint + glow, never a side-stripe."""
    for cls in (".att.failed", ".att.running", ".inspector"):
        rule = _css_rule(cls)
        assert "border-left:" not in rule and "border-right:" not in rule, \
            f"`{cls}` must not use a coloured side-stripe border"
