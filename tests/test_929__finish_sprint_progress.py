"""Tests for issue #929: Show shared progress component while finishing sprint.

AC coverage:
  AC1  — POST /finish-bg starts job; modal shows ProgressActivity in bar mode
  AC2  — total = N issues + 1 merge + 1 cleanup; done advances per step
  AC3  — current-item label updates for each issue/step
  AC4  — log_tail grows and streams in real time
  AC5  — job survives client disconnect (queue-based subscriber model)
  AC6  — reconnecting to /finish-stream returns current live state immediately
  AC7  — done summary: N closed, sprint merged in result string
  AC8  — error state has error field; retry affordance in modal
  AC9  — project.html uses pa-root (shared ProgressActivity), not one-off UI
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = STATIC_DIR / "project.html"
FINISH_MODAL_JS = STATIC_DIR / "src" / "sprint-board" / "finish-modal.js"
BUNDLE_JS = STATIC_DIR / "dist" / "bundle.js"
FINISH_PROGRESS_SVC = REPO_ROOT / "apps" / "dashboard" / "routers" / "finish_progress_service.py"
FINISH_PROGRESS_ROUTER = REPO_ROOT / "apps" / "dashboard" / "routers" / "finish_progress.py"
ROUTERS_INIT = REPO_ROOT / "apps" / "dashboard" / "routers" / "__init__.py"
STATE_JS = STATIC_DIR / "src" / "sprint-board" / "state.js"


def _html() -> str:
    return PROJECT_HTML.read_text(encoding="utf-8")


def _finish_modal_js() -> str:
    return FINISH_MODAL_JS.read_text(encoding="utf-8") if FINISH_MODAL_JS.exists() else ""


def _bundle() -> str:
    return BUNDLE_JS.read_text(encoding="utf-8") if BUNDLE_JS.exists() else ""


def _js_src() -> str:
    return _finish_modal_js() + "\n" + _bundle()


# ── AC1: /finish-bg endpoint + modal progress slot ────────────────────────────

def test_finish_bg_router_file_exists():
    """AC1 — finish_progress.py router must exist."""
    assert FINISH_PROGRESS_ROUTER.exists(), (
        "apps/dashboard/routers/finish_progress.py not found"
    )


def test_finish_bg_endpoint_in_router():
    """AC1 — POST /finish-bg endpoint must be defined in the router."""
    src = FINISH_PROGRESS_ROUTER.read_text()
    assert "/finish-bg" in src, "POST /finish-bg endpoint not found in finish_progress.py"


def test_finish_stream_endpoint_in_router():
    """AC1 — GET /finish-stream SSE endpoint must be defined in the router."""
    src = FINISH_PROGRESS_ROUTER.read_text()
    assert "/finish-stream" in src, "GET /finish-stream endpoint not found in finish_progress.py"


def test_finish_progress_router_registered():
    """AC1 — finish_progress_router must be imported and mounted on the app."""
    init_src = ROUTERS_INIT.read_text()
    assert "finish_progress" in init_src, (
        "finish_progress_router not imported in routers/__init__.py"
    )
    server_src = (REPO_ROOT / "apps" / "dashboard" / "server.py").read_text()
    assert "finish_progress_router" in server_src, (
        "finish_progress_router not imported in server.py"
    )
    assert "include_router(finish_progress_router)" in server_src, (
        "finish_progress_router not mounted via app.include_router in server.py"
    )


def test_finish_modal_posts_to_finish_bg():
    """AC1 — _fsConfirm must POST to /finish-bg instead of blocking on /finish."""
    js = _finish_modal_js()
    assert "finish-bg" in js, (
        "finish-modal.js does not POST to /finish-bg — progress wiring not implemented"
    )


def test_project_html_has_fs_progress_slot():
    """AC1 — #fs-progress container must exist in the finish-sprint modal."""
    html = _html()
    assert 'id="fs-progress"' in html, (
        "#fs-progress slot not found in project.html finish-sprint modal"
    )


def test_finish_modal_uses_bar_mode():
    """AC1 — modal renders ProgressActivity with mode='bar'."""
    js = _finish_modal_js() + "\n" + _bundle()
    assert "bar" in js and "renderProgressActivity" in js, (
        "renderProgressActivity in bar mode not found in finish-modal.js or bundle"
    )


# ── AC2: total = N + 2; done advances per step ────────────────────────────────

def test_service_total_is_n_plus_2():
    """AC2 — service computes total = len(selected_nums) + 2 (merge + cleanup)."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "n_issues + 2" in src or "len(selected_nums) + 2" in src or "+ 2" in src, (
        "total = N+2 formula not found in finish_progress_service.py"
    )


def test_service_done_increments():
    """AC2 — 'done' key must be present and incremented in the service snapshots."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert '"done"' in src or "'done'" in src, (
        "`done` field not in snapshot dict in service"
    )


# ── AC3: current-item label updates ───────────────────────────────────────────

def test_service_sets_current_for_issues():
    """AC3 — current must include issue number for each closing step."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "Closing #" in src or "closing" in src.lower(), (
        "current-item label for issue closing not found in service"
    )


def test_service_sets_current_for_merge():
    """AC3 — current must update to a merge-step label."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "Merging" in src or "merging" in src.lower(), (
        "Merge step current label not found in service"
    )


def test_finish_syncs_lifecycle_db():
    """Finish must mirror completed state into the sprints DB (History reads DB only)."""
    svc = FINISH_PROGRESS_SVC.read_text()
    server = (REPO_ROOT / "apps" / "dashboard" / "server.py").read_text()
    assert "_sprint_db_set_state" in svc and "completed" in svc
    assert "_sprint_db_set_state" in server and 'end_reason="merge_sprint"' in server


def test_service_sets_current_for_cleanup():
    """AC3 — current must update to a cleanup-step label."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "Updating" in src or "Cleanup" in src or "updating" in src.lower(), (
        "Cleanup step current label not found in service"
    )


# ── AC4: log_tail streams in real time ────────────────────────────────────────

def test_service_appends_to_log_tail():
    """AC4 — service must call _log() / append to log_tail throughout execution."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "_log(" in src or "log_tail.append" in src, (
        "log appending not found in finish_progress_service.py"
    )


def test_service_includes_log_tail_in_snapshots():
    """AC4 — each emitted snapshot must carry the log_tail list."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert '"log_tail"' in src or "'log_tail'" in src, (
        "log_tail not included in snapshot dict in service"
    )


# ── AC5: job survives client disconnect ───────────────────────────────────────

def test_service_uses_queue_subscriber_model():
    """AC5 — subscriber model uses asyncio.Queue so job outlives SSE disconnect."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "Queue" in src, (
        "asyncio.Queue not found in service — job will not survive disconnect"
    )
    assert "subscribe" in src and "unsubscribe" in src, (
        "subscribe/unsubscribe functions not found in service"
    )


def test_sse_unsubscribes_in_finally():
    """AC5 — SSE generator must unsubscribe the queue in a finally block."""
    src = FINISH_PROGRESS_ROUTER.read_text()
    assert "finally" in src and "unsubscribe" in src, (
        "SSE router does not unsubscribe in finally block — disconnect not handled"
    )


# ── AC6: reconnect shows current state ────────────────────────────────────────

def test_sse_sends_snapshot_on_connect():
    """AC6 — /finish-stream sends the current snapshot immediately on connect."""
    src = FINISH_PROGRESS_ROUTER.read_text()
    assert "get_snapshot" in src, (
        "get_snapshot not called in /finish-stream — reconnect not supported"
    )


def test_finish_modal_tracks_active_job():
    """AC6 — finish-modal.js tracks _fsActiveJob for reconnect support."""
    js = _finish_modal_js()
    assert "_fsActiveJob" in js, (
        "_fsActiveJob not found in finish-modal.js — reconnect state not tracked"
    )


def test_state_js_initialises_active_job():
    """AC6 — state.js must initialise _fsActiveJob on window."""
    src = STATE_JS.read_text()
    assert "_fsActiveJob" in src, (
        "_fsActiveJob not initialised in state.js"
    )


# ── AC7: done summary ─────────────────────────────────────────────────────────

def test_service_emits_done_status():
    """AC7 — service emits status='done' when finish completes successfully."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert '"done"' in src, (
        "status='done' not found in service snapshots"
    )


def test_service_done_result_mentions_closed():
    """AC7 — done snapshot result string mentions closed count."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert "closed" in src and "result" in src, (
        "result field with closed count not found in done snapshot"
    )


def test_finish_modal_handles_done_state():
    """AC7 — finish-modal.js handles done state from ProgressActivity."""
    js = _finish_modal_js()
    assert "done" in js and "loadSprintMgmt" in js, (
        "done state handling not found in finish-modal.js"
    )


# ── AC8: error state + retry ──────────────────────────────────────────────────

def test_service_emits_error_status():
    """AC8 — service emits status='error' when an unhandled exception occurs."""
    src = FINISH_PROGRESS_SVC.read_text()
    assert '"error"' in src and "except" in src, (
        "status='error' or exception handling not found in service"
    )


def test_finish_modal_has_fs_retry_function():
    """AC8 — _fsRetry function must exist in finish-modal.js."""
    js = _finish_modal_js()
    assert "_fsRetry" in js, (
        "_fsRetry function not found in finish-modal.js"
    )


def test_project_html_has_retry_button():
    """AC8 — project.html must include the retry button (fs-retry-btn)."""
    html = _html()
    assert "fs-retry-btn" in html, (
        "fs-retry-btn not found in project.html finish-sprint modal"
    )


def test_index_exports_fs_retry_on_global():
    """AC8 — sprint-board barrel must expose _fsRetry for _setupModals wiring."""
    index_js = (REPO_ROOT / "apps" / "dashboard" / "static" / "src" / "sprint-board" / "index.js").read_text()
    assert "globalThis._fsRetry = _fsRetry" in index_js, (
        "_fsRetry not assigned on globalThis in sprint-board/index.js"
    )


# ── AC9: shared ProgressActivity used, no one-off UI ─────────────────────────

def test_finish_modal_calls_render_progress_activity():
    """AC9 — finish-modal.js must use renderProgressActivity (shared component)."""
    js = _finish_modal_js()
    assert "renderProgressActivity" in js, (
        "renderProgressActivity not called in finish-modal.js — one-off UI present"
    )


def test_finish_modal_no_bespoke_progress_elements():
    """AC9 — finish-modal.js must not define one-off progress bar elements."""
    js = _finish_modal_js()
    bespoke = ["fs-progress-fill", "fs-progress-bar", "fs-bar-fill", "fs-op-fill"]
    found = [p for p in bespoke if p in js]
    assert not found, (
        f"Bespoke progress elements found in finish-modal.js: {found}"
    )


# ── Service unit tests (asyncio) ──────────────────────────────────────────────

def _import_svc():
    """Import finish_progress_service directly (bypasses __init__.py → sse_starlette)."""
    import importlib.util  # noqa: PLC0415
    mod_key = "routers.finish_progress_service"
    if mod_key in sys.modules:
        return sys.modules[mod_key]
    file_path = REPO_ROOT / "apps" / "dashboard" / "routers" / "finish_progress_service.py"
    spec = importlib.util.spec_from_file_location(mod_key, file_path)
    svc = importlib.util.module_from_spec(spec)
    sys.modules[mod_key] = svc
    spec.loader.exec_module(svc)
    return svc


def _make_mock_server(close_error=None):
    """Build a minimal mock server for service unit tests."""
    srv = MagicMock()
    srv._sprint_label_base.return_value = "sprint-73"
    srv._merge_sprint_branch_chain.return_value = []
    srv._project_root_path.return_value = Path("/tmp/test-project")
    srv.children_of.return_value = []
    srv._plan_json_set_state.return_value = None

    gh = MagicMock()
    if close_error:
        import subprocess
        exc = subprocess.CalledProcessError(1, ["gh"])
        exc.stderr = close_error
        gh.close_issue.side_effect = exc
    else:
        gh.close_issue.return_value = None
    gh.invalidate.return_value = None
    srv.github_client = gh

    async def _noop_broadcast(_data):
        pass

    srv.broadcast = _noop_broadcast
    return srv


async def _collect_events(svc, key, coro, timeout=10.0):
    """Run coro while draining the subscribe queue for job_key."""
    q = svc.subscribe(key)
    collected: list[dict] = []
    try:
        task = asyncio.create_task(coro)
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=timeout)
                collected.append(dict(event))
                if event.get("status") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                break
        await task
    finally:
        svc.unsubscribe(key, q)
    return collected


def test_service_total_count_is_n_plus_2():
    """AC2 — total = N + 2 for N selected issues (here N=4, expect total=6)."""
    svc = _import_svc()
    selected = [10, 20, 30, 40]
    srv = _make_mock_server()
    key = "sprint-73@owner/repo-ac2"

    async def run():
        svc._FINISH_JOBS.clear()
        svc._FINISH_SUBS.clear()
        with patch.object(svc, "_server", return_value=srv):
            return await _collect_events(
                svc, key,
                svc.run_finish_sprint(key, "owner/repo-ac2", "sprint-73", selected, [], False, None, ""),
            )

    events = asyncio.run(run())
    assert events, "No events emitted"
    first = events[0]
    assert first["total"] == 6, f"Expected total=6 (4+2), got {first['total']}"
    done = next((e for e in events if e.get("status") == "done"), None)
    assert done is not None, "No done event emitted"
    assert done["done"] == done["total"], "done != total at completion"


def test_service_current_contains_issue_numbers():
    """AC3 — current field includes each issue number during closing steps."""
    svc = _import_svc()
    selected = [111, 222]
    srv = _make_mock_server()
    key = "sprint-73@owner/repo-ac3"

    async def run():
        svc._FINISH_JOBS.clear()
        svc._FINISH_SUBS.clear()
        with patch.object(svc, "_server", return_value=srv):
            return await _collect_events(
                svc, key,
                svc.run_finish_sprint(key, "owner/repo-ac3", "sprint-73", selected, [], False, None, ""),
            )

    events = asyncio.run(run())
    currents = [e.get("current", "") for e in events]
    assert any("#111" in c for c in currents), f"#111 not in current labels: {currents}"
    assert any("#222" in c for c in currents), f"#222 not in current labels: {currents}"


def test_service_done_result_has_closed_count():
    """AC7 — done snapshot result mentions the number of issues closed."""
    svc = _import_svc()
    selected = [5, 6, 7]
    srv = _make_mock_server()
    key = "sprint-73@owner/repo-ac7"

    async def run():
        svc._FINISH_JOBS.clear()
        svc._FINISH_SUBS.clear()
        with patch.object(svc, "_server", return_value=srv):
            return await _collect_events(
                svc, key,
                svc.run_finish_sprint(key, "owner/repo-ac7", "sprint-73", selected, [], False, None, ""),
            )

    events = asyncio.run(run())
    done = next((e for e in events if e.get("status") == "done"), None)
    assert done is not None, "No done event emitted"
    result = done.get("result", "")
    assert "3" in result or "closed" in result, (
        f"Done result does not mention 3 issues: {result!r}"
    )


def test_service_error_state_on_exception():
    """AC8 — service emits status='error' when _sprint_label_base raises."""
    svc = _import_svc()

    srv = MagicMock()
    srv._sprint_label_base.side_effect = RuntimeError("label parse failed")
    key = "sprint-73@owner/repo-ac8"

    async def run():
        svc._FINISH_JOBS.clear()
        svc._FINISH_SUBS.clear()
        with patch.object(svc, "_server", return_value=srv):
            return await _collect_events(
                svc, key,
                svc.run_finish_sprint(key, "owner/repo-ac8", "sprint-73", [1], [], False, None, ""),
            )

    events = asyncio.run(run())
    error = next((e for e in events if e.get("status") == "error"), None)
    assert error is not None, "No error event emitted"
    assert error.get("error"), "error field is empty in error snapshot"
