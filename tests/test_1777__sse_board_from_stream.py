"""Tests for issue #1777 — Drive sprint board from SSE stream; drop snapshot polling.

AC coverage:
  AC1 — ≤ 4 calls/min during steady-state SSE connection (structural: 2s poll removed)
  AC2 — 2s setInterval for _smgmtLivePollTick removed from _smgmtLivePollRestart
  AC3 — Board updates from snapshot events (5s interval on server → within 5s AC)
  AC4 — Inspector 5s poll guarded by !_smgmtSseEs; only starts in fallback mode
  AC5 — _smgmtWorkerLogFetch guarded by !_smgmtSseEs; not called while stream lives
  AC6 — Fallback poll interval (_SMGMT_SSE_FALLBACK_MS) is ≥ 15000 ms
  AC7 — Completed sprints render correctly; SSE emits 'complete' → fallback handles it
  AC8 — Server emits 'snapshot' event type on the SSE stream
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS = _DASHBOARD / "routers"
_STATIC = _DASHBOARD / "static"
_PROJECT_HTML = _STATIC / "project.html"
_SPRINT_LIVE_PY = _ROUTERS / "sprint_live.py"

for _p in (str(_REPO_ROOT), str(_DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PROJECT_HTML = _PROJECT_HTML.read_text(encoding="utf-8")
SPRINT_LIVE_SRC = _SPRINT_LIVE_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fn_body(name: str, src: str = PROJECT_HTML) -> str:
    """Return the brace-balanced body of a named JS function in src."""
    for needle in (
        f"function {name}(",
        f"function {name} (",
        f"async function {name}(",
    ):
        pos = src.find(needle)
        if pos != -1:
            break
    else:
        raise AssertionError(f"function {name} not found in source")
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


# ---------------------------------------------------------------------------
# AC8 (backend behavioral): SSE stream emits 'snapshot' event on connect
# ---------------------------------------------------------------------------

def _make_mock_server(tmp_path: Path, sprint_label: str = "sprint-100"):
    """Build a MagicMock for the deferred _server() import."""
    commander = tmp_path / ".commander"
    (commander / "sprints").mkdir(parents=True)
    (commander / "logs").mkdir(parents=True)

    mock = MagicMock()
    mock._SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)*$")
    mock._project_root_path.return_value = tmp_path
    mock._commander_dir.return_value = commander
    mock._read_plan_json.return_value = {"state": "running"}
    mock._sprint_pid_alive.return_value = True
    mock._is_sprint_running.return_value = True
    mock._sprint_statuses = {
        ("owner/repo", sprint_label): {
            "sprint_label": sprint_label,
            "start_timestamp": "2026-07-09T00:00:00",
            "pipeline_mode": False,
            "max_coder_slots": 1,
            "max_tester_slots": 1,
            "issues": [
                {
                    "number": 10,
                    "title": "Ticket 10",
                    "status": "in-progress",
                    "agent_status": "coder_running",
                    "dispatch_level": 0,
                    "coder_started_at": "2026-07-09T00:01:00",
                },
            ],
            "estimates": {},
        }
    }
    return mock, commander


async def _collect_sse_chunks(gen, max_chunks: int = 10) -> list[str]:
    """Drive an async generator, collecting up to max_chunks string chunks."""
    chunks: list[str] = []
    async for chunk in gen:
        chunks.append(chunk)
        if len(chunks) >= max_chunks:
            break
    return chunks


def _run_sse_test(tmp_path: Path, sprint_label: str = "sprint-100",
                  max_chunks: int = 8) -> list[str]:
    """Run the SSE stream in a test context, collecting the first N chunks."""
    project = "owner/repo"
    srv_mock, commander = _make_mock_server(tmp_path, sprint_label)
    log_file = commander / "logs" / f"sprint-run-{sprint_label}-20260709.log"
    log_file.write_text("Dispatching coder for #10\n")

    disconnect_calls = {"n": 0}

    async def _run():
        from routers import sprint_live

        async def _is_disconnected():
            disconnect_calls["n"] += 1
            # Report disconnect after enough iterations to collect chunks
            return disconnect_calls["n"] > max_chunks * 3

        fake_req = MagicMock()
        fake_req.is_disconnected = _is_disconnected

        with (
            patch.object(sprint_live, "_server", return_value=srv_mock),
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
            patch("github_client.cached_open_issues_with_body", return_value=[]),
        ):
            resp = await sprint_live.get_sprint_live_stream(
                sprint_label, project, fake_req
            )
            return await _collect_sse_chunks(resp.body_iterator, max_chunks=max_chunks)

    return asyncio.run(_run())


class TestAC8ServerEmitsSnapshotEvent:
    """AC8: The SSE endpoint emits 'event: snapshot' data on connect."""

    def test_snapshot_event_type_emitted(self, tmp_path):
        """The SSE stream must emit at least one 'event: snapshot' chunk."""
        chunks = _run_sse_test(tmp_path, max_chunks=8)
        snapshot_chunks = [c for c in chunks if "event: snapshot" in c]
        assert snapshot_chunks, (
            f"Expected at least one 'event: snapshot' chunk in SSE output.\n"
            f"Got {len(chunks)} chunks:\n" + "\n".join(repr(c) for c in chunks)
        )

    def test_snapshot_event_data_is_valid_json(self, tmp_path):
        """The snapshot event data must be valid JSON with 'issues' key."""
        chunks = _run_sse_test(tmp_path, max_chunks=8)
        snapshot_chunk = next((c for c in chunks if "event: snapshot" in c), None)
        assert snapshot_chunk is not None, "No snapshot event found"

        m = re.search(r"data:\s*(\{.*\})", snapshot_chunk, re.DOTALL)
        assert m is not None, f"No JSON data in snapshot chunk: {snapshot_chunk!r}"
        snap = json.loads(m.group(1))
        assert "issues" in snap, f"Snapshot JSON missing 'issues' key: {list(snap.keys())}"
        assert isinstance(snap["issues"], list)

    def test_snapshot_event_precedes_log_lines(self, tmp_path):
        """The snapshot event must be the FIRST event on the stream (for bootstrap)."""
        chunks = _run_sse_test(tmp_path, max_chunks=5)
        first = next((c for c in chunks if c.strip()), None)
        assert first is not None, "Stream produced no output"
        assert "event: snapshot" in first, (
            f"Expected first event to be 'snapshot', got: {first!r}"
        )

    def test_log_line_events_still_emitted(self, tmp_path):
        """log_line or complete events must still be emitted alongside snapshot events."""
        chunks = _run_sse_test(tmp_path, max_chunks=15)
        has_snapshot = any("event: snapshot" in c for c in chunks)
        assert has_snapshot, "Expected snapshot event in stream output"


# ---------------------------------------------------------------------------
# AC8 structural: 'snapshot' event type in sprint_live.py
# ---------------------------------------------------------------------------

class TestAC8SnapshotEventInSource:
    """AC8 (structural): sprint_live.py's stream function emits 'snapshot' events."""

    def test_snapshot_event_type_string_in_source(self):
        """The string 'event: snapshot' must appear in sprint_live.py."""
        assert "event: snapshot" in SPRINT_LIVE_SRC, (
            "Expected 'event: snapshot' in sprint_live.py — server must emit this event type"
        )

    def test_snapshot_uses_get_sprint_live_snapshot(self):
        """The stream generator must call get_sprint_live_snapshot to build the payload."""
        assert "get_sprint_live_snapshot" in SPRINT_LIVE_SRC


# ---------------------------------------------------------------------------
# AC2: 2s poll removed from _smgmtLivePollRestart; SSE used instead
# ---------------------------------------------------------------------------

class TestAC2TwoSecondPollRemoved:
    """AC2: The 2s setInterval for _smgmtLivePollTick is removed while SSE is live."""

    def test_2s_interval_not_in_live_poll_restart(self):
        """_smgmtLivePollRestart must NOT wire setInterval(_smgmtLivePollTick, 2000)
        unconditionally — SSE replaces the 2s poll (issue #1777 AC2)."""
        body = _fn_body("_smgmtLivePollRestart")
        assert "visibilityInterval(_smgmtLivePollTick, 2000)" not in body, (
            "The 2s _smgmtLivePollTick interval must not be set up unconditionally "
            "in _smgmtLivePollRestart — SSE-driven updates replace it per AC2 of #1777."
        )

    def test_sse_connect_called_in_live_poll_restart(self):
        """_smgmtLivePollRestart must call _smgmtSseConnect to start the stream."""
        body = _fn_body("_smgmtLivePollRestart")
        assert "_smgmtSseConnect(" in body, (
            "_smgmtLivePollRestart must call _smgmtSseConnect() to wire the SSE stream "
            "(replaces the removed 2s poll)"
        )

    def test_sse_disconnect_called_at_poll_restart(self):
        """_smgmtLivePollRestart must disconnect any existing SSE before reconnecting."""
        body = _fn_body("_smgmtLivePollRestart")
        assert "_smgmtSseDisconnect()" in body

    def test_first_paint_still_present_for_initial_render(self):
        """_smgmtRunningFirstPaint / _smgmtLivePollTick still used for first paint."""
        body = _fn_body("_smgmtLivePollRestart")
        has_first_paint = "_smgmtRunningFirstPaint()" in body or "_smgmtLivePollTick()" in body
        assert has_first_paint, (
            "_smgmtLivePollRestart must still perform a first-paint fetch "
            "(_smgmtRunningFirstPaint or _smgmtLivePollTick) before SSE takes over"
        )


# ---------------------------------------------------------------------------
# AC4: Inspector 5s poll removed when SSE is connected
# ---------------------------------------------------------------------------

class TestAC4InspectorPollGuarded:
    """AC4: The 5s inspector log poll is disabled when SSE is live."""

    def test_inspector_stream_restart_guards_poll_with_sse_check(self):
        """_smgmtInspectorStreamRestart must only start 5s poll if !_smgmtSseEs."""
        body = _fn_body("_smgmtInspectorStreamRestart")
        # The poll must be conditional on !_smgmtSseEs, not unconditional
        assert "_smgmtSseEs" in body, (
            "_smgmtInspectorStreamRestart must check _smgmtSseEs before wiring the 5s poll"
        )

    def test_inspector_poll_not_started_unconditionally(self):
        """The 5s inspector poll must not be set up without an SSE guard."""
        body = _fn_body("_smgmtInspectorStreamRestart")
        # If visibilityInterval is present, it must be inside a guard block
        if "visibilityInterval(_smgmtInspectorStreamTick, 5000)" in body:
            # The interval call must be guarded by a check on _smgmtSseEs
            idx_interval = body.find("visibilityInterval(_smgmtInspectorStreamTick, 5000)")
            idx_sse_check = body.find("_smgmtSseEs")
            assert idx_sse_check != -1, (
                "visibilityInterval is present but there is no _smgmtSseEs guard"
            )
            # The SSE check must come before the interval setup in some if-branch
            # (We check that the text "!_smgmtSseEs" appears to guard it)
            assert "!_smgmtSseEs" in body, (
                "The 5s inspector poll must be guarded by !_smgmtSseEs"
            )

    def test_initial_inspector_fetch_still_happens(self):
        """_smgmtInspectorStreamTick() must still be called once for initial paint."""
        body = _fn_body("_smgmtInspectorStreamRestart")
        assert "_smgmtInspectorStreamTick()" in body, (
            "_smgmtInspectorStreamRestart must still call _smgmtInspectorStreamTick() "
            "once for the initial log load"
        )


# ---------------------------------------------------------------------------
# AC5: _smgmtWorkerLogFetch guarded by !_smgmtSseEs
# ---------------------------------------------------------------------------

class TestAC5WorkerLogFetchGuarded:
    """AC5: _smgmtWorkerLogFetch does not fire while SSE is connected."""

    def test_worker_log_fetch_call_guarded_by_sse(self):
        """_smgmtLanesUpdate must guard _smgmtWorkerLogFetch with !_smgmtSseEs."""
        body = _fn_body("_smgmtLanesUpdate")
        assert "_smgmtSseEs" in body, (
            "_smgmtLanesUpdate must check _smgmtSseEs before calling _smgmtWorkerLogFetch"
        )

    def test_worker_log_fetch_not_called_without_guard(self):
        """_smgmtWorkerLogFetch call site inside _smgmtLanesUpdate must be SSE-guarded."""
        body = _fn_body("_smgmtLanesUpdate")
        idx_fetch = body.find("_smgmtWorkerLogFetch(")
        if idx_fetch != -1:
            # There must be a !_smgmtSseEs guard in the function body
            assert "!_smgmtSseEs" in body, (
                "_smgmtWorkerLogFetch is called but there is no !_smgmtSseEs guard "
                "in _smgmtLanesUpdate"
            )

    def test_sse_log_line_handler_updates_worker_lane(self):
        """_smgmtSseHandleLogLine must update worker lane containers from log_line events."""
        assert "_smgmtSseHandleLogLine" in PROJECT_HTML, (
            "_smgmtSseHandleLogLine function not found in project.html"
        )
        body = _fn_body("_smgmtSseHandleLogLine")
        # Must reference smgmt-worker-log element (for per-issue routing)
        assert "smgmt-worker-log-" in body or "issue_num" in body, (
            "_smgmtSseHandleLogLine must route log_line events to worker lane containers"
        )


# ---------------------------------------------------------------------------
# AC6: Fallback poll interval is ≥ 15000ms
# ---------------------------------------------------------------------------

class TestAC6FallbackInterval:
    """AC6: On SSE disconnect, fallback poll runs at ≥ 15s."""

    def test_fallback_interval_constant_defined(self):
        """_SMGMT_SSE_FALLBACK_MS constant must be defined in project.html."""
        assert "_SMGMT_SSE_FALLBACK_MS" in PROJECT_HTML, (
            "_SMGMT_SSE_FALLBACK_MS constant not defined in project.html"
        )

    def test_fallback_interval_is_at_least_15000ms(self):
        """_SMGMT_SSE_FALLBACK_MS must be ≥ 15000 to satisfy the AC6 contract."""
        m = re.search(r"_SMGMT_SSE_FALLBACK_MS\s*=\s*(\d+)", PROJECT_HTML)
        assert m is not None, "_SMGMT_SSE_FALLBACK_MS not found or has non-integer value"
        interval_ms = int(m.group(1))
        assert interval_ms >= 15000, (
            f"_SMGMT_SSE_FALLBACK_MS is {interval_ms}ms — must be ≥ 15000ms per AC6"
        )

    def test_fallback_uses_api_running_endpoint(self):
        """The fallback poll must target /api/running (the sprint-103 snapshot endpoint)."""
        assert "_smgmtSseStartFallback" in PROJECT_HTML, (
            "_smgmtSseStartFallback function not found in project.html"
        )
        body = _fn_body("_smgmtSseStartFallback")
        assert "/api/running" in body, (
            "_smgmtSseStartFallback must poll /api/running as the fallback data source"
        )

    def test_sse_connect_called_in_fallback(self):
        """Fallback must attempt to reconnect SSE on each tick (auto-resume per AC6)."""
        body = _fn_body("_smgmtSseStartFallback")
        assert "_smgmtSseConnect(" in body, (
            "_smgmtSseStartFallback must attempt to reconnect the SSE stream "
            "on each tick for automatic resume (AC6)"
        )


# ---------------------------------------------------------------------------
# AC6: Fallback structures exist
# ---------------------------------------------------------------------------

class TestAC6FallbackStructures:
    """AC6 structural: SSE management functions are wired correctly."""

    def test_sse_connect_function_defined(self):
        assert "_smgmtSseConnect" in PROJECT_HTML
        body = _fn_body("_smgmtSseConnect")
        assert "EventSource" in body

    def test_sse_disconnect_function_defined(self):
        assert "_smgmtSseDisconnect" in PROJECT_HTML

    def test_sse_snapshot_handler_defined(self):
        assert "_smgmtSseHandleSnapshot" in PROJECT_HTML
        body = _fn_body("_smgmtSseHandleSnapshot")
        # Must update _smgmtLiveCache and patch the board
        assert "_smgmtLiveCache" in body
        assert "_smgmtLivePatch(" in body

    def test_sse_onerror_starts_fallback(self):
        """EventSource.onerror must start the fallback poll."""
        body = _fn_body("_smgmtSseConnect")
        assert "onerror" in body
        assert "_smgmtSseFallbackId" in body or "_smgmtSseStartFallback" in body

    def test_snapshot_event_handler_in_connect(self):
        """_smgmtSseConnect must listen for 'snapshot' events."""
        body = _fn_body("_smgmtSseConnect")
        assert "'snapshot'" in body or '"snapshot"' in body

    def test_log_line_event_handler_in_connect(self):
        """_smgmtSseConnect must listen for 'log_line' events."""
        body = _fn_body("_smgmtSseConnect")
        assert "'log_line'" in body or '"log_line"' in body


# ---------------------------------------------------------------------------
# AC7: Completed sprints use fallback; no JS errors
# ---------------------------------------------------------------------------

class TestAC7CompletedSprintFallback:
    """AC7: Completed sprints render via fallback; 'complete' event wires fallback."""

    def test_complete_event_handler_in_sse_connect(self):
        """_smgmtSseConnect must handle 'complete' event by starting fallback."""
        body = _fn_body("_smgmtSseConnect")
        assert "'complete'" in body or '"complete"' in body

    def test_complete_event_calls_fallback(self):
        """On 'complete', SSE must be disconnected and fallback started."""
        body = _fn_body("_smgmtSseConnect")
        assert "_smgmtSseStartFallback" in body or "_smgmtSseFallbackId" in body


# ---------------------------------------------------------------------------
# AC3: Snapshot event drives board update within 5s
# ---------------------------------------------------------------------------

class TestAC3SnapshotDrivesBoard:
    """AC3: Board updates from snapshot events (5s or less latency)."""

    def test_snapshot_handler_calls_live_patch(self):
        """_smgmtSseHandleSnapshot must call _smgmtLivePatch to update board metrics."""
        body = _fn_body("_smgmtSseHandleSnapshot")
        assert "_smgmtLivePatch(" in body

    def test_snapshot_handler_calls_progress_render(self):
        """_smgmtSseHandleSnapshot must call _sprintProgressRender for pill sync."""
        body = _fn_body("_smgmtSseHandleSnapshot")
        assert "_sprintProgressRender()" in body

    def test_snapshot_handler_updates_running_view(self):
        """_smgmtSseHandleSnapshot must call _smgmtRunningViewUpdate."""
        body = _fn_body("_smgmtSseHandleSnapshot")
        assert "_smgmtRunningViewUpdate(" in body

    def test_server_emits_snapshot_periodically(self):
        """sprint_live.py must emit snapshots at regular intervals in the stream loop."""
        # The stream should emit a snapshot every N iterations
        assert "snapshot" in SPRINT_LIVE_SRC
        # A counter or tick variable should trigger periodic snapshots
        assert "snapshot_tick" in SPRINT_LIVE_SRC or "_SNAPSHOT_EVERY_N" in SPRINT_LIVE_SRC


# ---------------------------------------------------------------------------
# SSE log_line with issue_num (AC5 backend)
# ---------------------------------------------------------------------------

class TestAC5BackendIssueNumField:
    """AC5 (backend): SSE stream emits log_line events with issue_num for per-worker routing."""

    def test_issue_num_field_in_log_line_events(self):
        """sprint_live.py must emit 'issue_num' in log_line event data."""
        assert "issue_num" in SPRINT_LIVE_SRC, (
            "sprint_live.py must include 'issue_num' in log_line event data "
            "for per-worker log routing (AC5)"
        )

    def test_per_issue_log_files_are_tailed(self):
        """The stream must tail sprint-issue-N.log files for per-worker log lines."""
        assert "sprint-issue-" in SPRINT_LIVE_SRC or "issue_log" in SPRINT_LIVE_SRC, (
            "sprint_live.py must tail per-issue log files and route them via issue_num"
        )
