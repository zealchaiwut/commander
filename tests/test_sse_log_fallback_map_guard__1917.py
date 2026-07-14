"""
Tests for issue #1917: SSE log-fallback checks treat _smgmtSseEs Map as nullable
(runs against UAT)

Issue #1818 converted _smgmtSseEs from nullable EventSource to const Map().
Two call sites still tested it as nullable, permanently disabling REST fallbacks.
Fix: replaced `if (!_smgmtSseEs)` with label-aware Map checks.

AC1: Multi-worker log REST fetch guard updated
AC2: Inspector 5s poll guard updated
AC3: No other sites retain old boolean check pattern
AC4: Multi-worker REST fallback activates when SSE disconnected
AC5: Inspector poll activates when SSE disconnected
AC6: REST fallbacks remain dormant when SSE connected
"""
import os
import pytest
import httpx
from pathlib import Path


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


@pytest.fixture
def project_html_source():
    """Read the source project.html file for code inspection."""
    html_path = Path(__file__).parent.parent / "apps" / "dashboard" / "static" / "project.html"
    if not html_path.exists():
        # Fallback: try relative path from dashboard dir
        html_path = Path(__file__).parent.parent.parent / "dashboard" / "static" / "project.html"

    assert html_path.exists(), f"project.html not found at {html_path}"
    return html_path.read_text()


# --- Acceptance Criteria ---

def test_sse_log_fallback__multi_worker_guard_uses_map_has(project_html_source):
    """AC1: Multi-worker log fetch guard uses _smgmtSseEs.has(label) not boolean check."""
    html = project_html_source
    # The corrected line should contain the Map-aware check
    assert "if (!_smgmtSseEs.has(label)) {" in html, \
        "Multi-worker log fetch should use Map.has(label) check"


def test_sse_log_fallback__inspector_poll_guard_uses_map_has(project_html_source):
    """AC2: Inspector 5s poll guard uses _smgmtSseEs.has(label) not boolean check."""
    html = project_html_source
    # The corrected line should use Map.has check with _smgmtInspectorLabel
    assert "if (!_smgmtSseEs.has(_smgmtInspectorLabel))" in html, \
        "Inspector poll should use Map.has(_smgmtInspectorLabel) check"


def test_sse_log_fallback__no_boolean_guards_remain(project_html_source):
    """AC3: No other call sites in project.html retain old if (!_smgmtSseEs) pattern."""
    html = project_html_source
    # Search for the old pattern — should have zero occurrences in guard context
    old_guard_false = "if (!_smgmtSseEs)"

    # Check that old pattern doesn't appear as a guard (allow only in comments/strings)
    lines_with_old_pattern = [
        line for line in html.split('\n')
        if old_guard_false in line and not line.strip().startswith('//')
    ]

    assert len(lines_with_old_pattern) == 0, \
        f"Old pattern 'if (!_smgmtSseEs)' still exists as a guard: {lines_with_old_pattern[:3]}"


def test_sse_log_fallback__sse_map_initialized_constant(project_html_source):
    """Verify _smgmtSseEs is initialized as const Map (not nullable)."""
    html = project_html_source
    assert "const _smgmtSseEs = new Map();" in html, \
        "_smgmtSseEs should be declared as const Map, not nullable"


def test_sse_log_fallback__fallback_poll_uses_map_check(project_html_source):
    """Verify the SSE fallback poll checks Map.has() for reconnection."""
    html = project_html_source
    # _smgmtSseStartFallback should check _smgmtSseEs.has(label) to know when SSE returns
    assert "if (_smgmtSseEs.has(label)) { _smgmtSseFallbackStop(label); return; }" in html, \
        "Fallback poll should use Map.has() to detect SSE reconnection"


def test_sse_log_fallback__handler_stops_fallback_when_sse_live(project_html_source):
    """Verify SSE snapshot handler checks Map.has(label) to stop fallback."""
    html = project_html_source
    # _smgmtSseHandleSnapshot should stop the fallback poll when SSE is live
    assert "if (_smgmtSseEs.has(label)) _smgmtSseFallbackStop(label);" in html, \
        "SSE snapshot handler should stop fallback poll when SSE is connected"
