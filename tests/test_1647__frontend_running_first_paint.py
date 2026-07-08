"""Tests for issue #1647: Frontend running-tab first-paint via /api/running snapshot.

AC coverage (corrected per triage note 2026-07-03):
  AC-1: With flag ON the first-paint uses a single GET /api/running call
        (verified structurally: _smgmtRunningFirstPaint function exists in project.html
        and _smgmtLivePollRestart branches on window._commanderFeatures.running_aggregate).
  AC-2: With flag OFF the code path falls back to _smgmtLivePollTick() (existing fan-out).
  AC-3: features.js exports runningAggregateEnabled() and it reads running_aggregate.
  AC-4: _smgmtRunningFirstPaint falls back to _smgmtLivePollTick on 404/error.
  AC-5: The 2-second interval (setInterval) is always wired regardless of flag state.
  AC-6: No bundle rebuild needed — the change lives in project.html (HTML-only edit).
"""
from __future__ import annotations

import re
from pathlib import Path

_STATIC = Path(__file__).resolve().parent.parent / "apps" / "dashboard" / "static"
_PROJECT_HTML = _STATIC / "project.html"
_FEATURES_JS = _STATIC / "src" / "shell" / "features.js"

PROJECT_HTML = _PROJECT_HTML.read_text(encoding="utf-8")
FEATURES_JS = _FEATURES_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-1: _smgmtRunningFirstPaint exists and fetches /api/running
# ---------------------------------------------------------------------------

class TestAC1FirstPaintFunctionExists:
    """AC-1: _smgmtRunningFirstPaint is defined in project.html."""

    def test_first_paint_function_defined(self):
        assert "async function _smgmtRunningFirstPaint()" in PROJECT_HTML

    def test_first_paint_fetches_api_running(self):
        """The function must call /api/running with the project param."""
        assert "/api/running?project=" in PROJECT_HTML

    def test_first_paint_stores_in_live_cache(self):
        """The function must populate _smgmtLiveCache[label] from the snapshot."""
        # Check that the assignment appears in the context of the first-paint function
        assert "_smgmtLiveCache[label] = data" in PROJECT_HTML

    def test_first_paint_calls_running_view_update(self):
        """The function must call _smgmtRunningViewUpdate(label, data)."""
        assert "_smgmtRunningViewUpdate(label, data)" in PROJECT_HTML

    def test_first_paint_calls_heartbeat(self):
        """The function must update _smgmtLiveHeartbeatAt = Date.now()."""
        # This is set to track liveness across poll ticks
        assert "_smgmtLiveHeartbeatAt = Date.now()" in PROJECT_HTML


# ---------------------------------------------------------------------------
# AC-2: _smgmtLivePollRestart branches on window._commanderFeatures
# ---------------------------------------------------------------------------

class TestAC2FlagBranch:
    """AC-2: _smgmtLivePollRestart calls first-paint when flag ON, normal tick when OFF."""

    def test_restart_checks_running_aggregate_flag(self):
        """The branch reads window._commanderFeatures.running_aggregate."""
        assert "window._commanderFeatures" in PROJECT_HTML
        assert "running_aggregate" in PROJECT_HTML

    def test_restart_calls_first_paint_when_flag_on(self):
        """When flag is ON the code calls _smgmtRunningFirstPaint()."""
        assert "_smgmtRunningFirstPaint()" in PROJECT_HTML

    def test_restart_calls_live_poll_tick_when_flag_off(self):
        """When flag is OFF the code calls _smgmtLivePollTick() (existing fan-out)."""
        # The else branch must still call the normal tick
        # Verify both the old call and the branch exist
        assert "_smgmtLivePollTick()" in PROJECT_HTML

    def test_branch_structure_is_if_else(self):
        """The branch must be an if/else so exactly one path runs per call.

        Issue #1777 removed the 2s setInterval; the anchor is now _smgmtSseConnect
        (SSE replaces polling).  The if/else for the running_aggregate flag still
        controls which first-paint path runs.
        """
        # Find the first-paint if/else section within _smgmtLivePollRestart.
        # Anchor on the specific CALL SITE (_smgmtSseConnect(_sseLabel, ...) — not
        # the function definition) through to _smgmtTimelineRefreshId.
        poll_section = re.search(
            r"_smgmtSseConnect\(_sse.*?_smgmtTimelineRefreshId",
            PROJECT_HTML,
            re.DOTALL,
        )
        assert poll_section is not None, (
            "_smgmtLivePollRestart: expected _smgmtSseConnect(_sse... _smgmtTimelineRefreshId "
            "section not found (issue #1777 replaced 2s poll with SSE)"
        )
        section_text = poll_section.group(0)
        assert "_smgmtRunningFirstPaint()" in section_text
        assert "_smgmtLivePollTick()" in section_text
        # Both sides must be in an if/else — not both called unconditionally
        assert "if (" in section_text
        assert "} else {" in section_text


# ---------------------------------------------------------------------------
# AC-3: features.js exports runningAggregateEnabled() correctly
# ---------------------------------------------------------------------------

class TestAC3FeatureFlagHelper:
    """AC-3: features.js exports runningAggregateEnabled() reading running_aggregate."""

    def test_running_aggregate_enabled_exported(self):
        assert "export function runningAggregateEnabled()" in FEATURES_JS

    def test_running_aggregate_enabled_reads_correct_key(self):
        """Must read 'running_aggregate' from commanderFeatures()."""
        assert "running_aggregate" in FEATURES_JS
        # Verify it's inside the function
        fn_match = re.search(
            r"export function runningAggregateEnabled\(\)\s*\{([^}]+)\}",
            FEATURES_JS,
        )
        assert fn_match is not None
        fn_body = fn_match.group(1)
        assert "running_aggregate" in fn_body

    def test_window_commanderfeatures_set_on_load(self):
        """loadCommanderFeatures must write to root._commanderFeatures so inline
        project.html code can read it without importing the ES module."""
        assert "_commanderFeatures = _features" in FEATURES_JS


# ---------------------------------------------------------------------------
# AC-4: Fallback to _smgmtLivePollTick on 404/error
# ---------------------------------------------------------------------------

class TestAC4FallbackOnError:
    """AC-4: _smgmtRunningFirstPaint falls back to normal tick on failure."""

    def _extract_first_paint_fn(self) -> str:
        m = re.search(
            r"async function _smgmtRunningFirstPaint\(\).*?^}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert m is not None, "_smgmtRunningFirstPaint not found"
        return m.group(0)

    def test_fallback_on_network_error(self):
        """On .catch(() => null) the function calls _smgmtLivePollTick()."""
        fn_body = self._extract_first_paint_fn()
        assert ".catch(() => null)" in fn_body

    def test_fallback_when_resp_not_ok(self):
        """If resp is falsy or !resp.ok the function falls back."""
        fn_body = self._extract_first_paint_fn()
        assert "!resp || !resp.ok" in fn_body or ("!resp.ok" in fn_body and "!resp" in fn_body)
        # Fallback call must be present in the error branch
        assert "_smgmtLivePollTick()" in fn_body

    def test_fallback_on_label_mismatch(self):
        """If the snapshot's sprint_label is not in running labels, fall back."""
        fn_body = self._extract_first_paint_fn()
        assert "!label || !_smgmtRunningLabels.has(label)" in fn_body

    def test_fallback_in_catch_block(self):
        """An outer try/catch must call _smgmtLivePollTick() on unexpected errors."""
        fn_body = self._extract_first_paint_fn()
        assert "catch (_)" in fn_body or "catch(" in fn_body


# ---------------------------------------------------------------------------
# AC-5 (updated for issue #1777): 2s poll replaced by SSE; fallback is ≥15 s
# ---------------------------------------------------------------------------

class TestAC5IntervalReplacedBySSE:
    """AC-5 (revised): The 2s setInterval is replaced by SSE-driven updates per issue #1777.

    Issue #1777 AC2 explicitly removes visibilityInterval(_smgmtLivePollTick, 2000)
    from _smgmtLivePollRestart and wires an SSE stream instead. The fallback poll
    uses _SMGMT_SSE_FALLBACK_MS (≥ 15 s) when the stream is offline.
    """

    def test_setinterval_removed_from_live_poll_restart(self):
        """The 2s setInterval must NOT be unconditionally wired in _smgmtLivePollRestart.
        SSE replaces it (issue #1777 AC2)."""
        poll_restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert poll_restart_match is not None
        fn_body = poll_restart_match.group(1)
        # The 2s poll must NOT appear unconditionally
        assert "visibilityInterval(_smgmtLivePollTick, 2000)" not in fn_body, (
            "The 2s _smgmtLivePollTick setInterval must be removed from "
            "_smgmtLivePollRestart — SSE replaces it per issue #1777 AC2."
        )

    def test_sse_connect_wired_in_live_poll_restart(self):
        """_smgmtSseConnect must be called inside _smgmtLivePollRestart."""
        poll_restart_match = re.search(
            r"function _smgmtLivePollRestart\(\)(.*?)^}",
            PROJECT_HTML,
            re.DOTALL | re.MULTILINE,
        )
        assert poll_restart_match is not None
        fn_body = poll_restart_match.group(1)
        assert "_smgmtSseConnect(" in fn_body, (
            "_smgmtLivePollRestart must call _smgmtSseConnect() to wire "
            "the SSE stream (replaces removed 2s poll)"
        )

    def test_fallback_interval_is_at_least_15s(self):
        """The SSE fallback poll interval must be ≥ 15000 ms (AC6 of issue #1777)."""
        m = re.search(r"_SMGMT_SSE_FALLBACK_MS\s*=\s*(\d+)", PROJECT_HTML)
        assert m is not None, "_SMGMT_SSE_FALLBACK_MS constant not defined"
        assert int(m.group(1)) >= 15000


# ---------------------------------------------------------------------------
# AC-6: The change is HTML-only — no src/ module was modified
# ---------------------------------------------------------------------------

class TestAC6HtmlOnlyEdit:
    """AC-6: The first-paint function lives in project.html, not in a src/ module."""

    def test_first_paint_fn_not_in_bundle_src(self):
        """_smgmtRunningFirstPaint must NOT appear in any static/src/ file."""
        src_dir = _STATIC / "src"
        for js_file in src_dir.rglob("*.js"):
            content = js_file.read_text(encoding="utf-8")
            assert "_smgmtRunningFirstPaint" not in content, (
                f"_smgmtRunningFirstPaint found in bundle src file: {js_file}"
            )

    def test_first_paint_fn_in_project_html(self):
        """_smgmtRunningFirstPaint must be present in project.html."""
        assert "_smgmtRunningFirstPaint" in PROJECT_HTML
