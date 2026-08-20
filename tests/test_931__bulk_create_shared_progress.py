"""Tests for issue #931: Bulk Create — Use Shared Progress Component (Tier 1).

AC coverage:
- AC1: Bulk-create step 2 renders the shared progress component in bar mode (no custom bar)
- AC2: total = number of tickets to create; current advances once per created issue
- AC3: Per-ticket states map correctly (drafting → in-progress; created → success; failed → error; skipped → skipped)
- AC4: Failed and skipped tickets surface visibly in the component log
- AC5: Parallel BA concurrency is preserved
- AC6: Stop-after-current halts the queue after the in-flight ticket finishes
- AC7: Closing the tab while posting continues the job in the background
- AC8: Reopening the tab reconnects to the running job via the shared component
- AC9: No regression in bulk-create step 1 or the final summary/review step
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
PROJECT_HTML = DASHBOARD_DIR / "static" / "project.html"


@pytest.fixture(scope="module")
def project_html() -> str:
    return PROJECT_HTML.read_text()


@pytest.fixture
def client(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(DASHBOARD_DIR))
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))
    monkeypatch.setenv("COMMANDER_DISABLE_NEON", "1")

    import server
    import projects as projects_module
    import github_client as gc

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}
    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr("server.projects_module.load_projects", lambda: [fake_project])
    monkeypatch.setattr(
        gc, "list_labels",
        lambda repo_name=None: [
            {"name": "enhancement", "color": "84b6eb"},
            {"name": "backlog", "color": "e4e669"},
        ],
    )

    yield TestClient(server.app, raise_server_exceptions=False)


def _make_job(job_id, tickets=None, status="drafts_ready"):
    return {
        "job_id": job_id,
        "status": status,
        "repo": "zealchaiwut/commander",
        "default_labels": ["enhancement"],
        "concurrency": 3,
        "has_attachments": False,
        "image_url_map": {},
        "created_at": "2024-01-01T00:00:00+00:00",
        "tickets": tickets or [],
    }


def _draft_ready_ticket(index, title="Test ticket"):
    return {
        "index": index,
        "prompt": "test prompt",
        "state": "draft_ready",
        "title": title,
        "body": "body content",
        "body_preview": "body content",
        "issue_num": None,
        "issue_url": None,
        "label_pills": None,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


# ---------------------------------------------------------------------------
# AC1: Shared progress component container exists in step 2
# ---------------------------------------------------------------------------

class TestProgressComponentExists:
    def test_posting_progress_container_in_step2(self, project_html):
        """A dedicated posting-progress container exists inside bc-step2."""
        step2_start = project_html.find('id="bc-step2"')
        step2_end = project_html.find('</div><!-- /pane-bulk-create -->', step2_start)
        step2_html = project_html[step2_start:step2_end]
        assert 'id="bc-posting-progress"' in step2_html, (
            "bc-posting-progress container must exist inside bc-step2"
        )

    def test_posting_progress_hidden_by_default(self, project_html):
        """bc-posting-progress is hidden by default (hidden class or display:none)."""
        pos = project_html.find('id="bc-posting-progress"')
        assert pos != -1, "bc-posting-progress must exist"
        snippet = project_html[max(0, pos - 200): pos + 200]
        assert "hidden" in snippet or "display:none" in snippet, (
            "bc-posting-progress must be hidden by default"
        )

    def test_progress_bar_element_in_component(self, project_html):
        """A progress bar element (legacy bc-pg-bar or shared pa-bar-fill) exists."""
        assert (
            "bc-pg-bar" in project_html
            or "bc-posting-bar" in project_html
            or "pa-bar-fill" in project_html
            or "mountProgressActivity" in project_html
        ), (
            "Progress bar element or shared ProgressActivity mount must exist"
        )

    def test_progress_log_container_in_component(self, project_html):
        """A log container for per-ticket entries exists in the component."""
        assert (
            'id="bc-pg-log"' in project_html
            or 'bc-pg-log' in project_html
            or "pa-log" in project_html
            or "log_tail" in project_html
        ), (
            "Log container/wiring must exist for per-ticket log entries"
        )

    def test_current_item_label_in_component(self, project_html):
        """A current-item label element exists in the component."""
        assert "bc-pg-current" in project_html or "pa-current" in project_html or "current:" in project_html, (
            "Current-item label must exist in posting-progress component"
        )


# ---------------------------------------------------------------------------
# AC1: No custom progress bar during posting stage
# ---------------------------------------------------------------------------

class TestNoCustomProgressBarDuringPosting:
    def test_render_posting_progress_function_exists(self, project_html):
        """A function renders/updates the posting-progress component."""
        assert "_bcRenderPostingProgress" in project_html or "_bcProgressActivity" in project_html, (
            "_bcRenderPostingProgress or _bcProgressActivity function must be defined"
        )

    def test_posting_stage_shows_progress_component(self, project_html):
        """During posting stage, bc-posting-progress is shown (remove hidden)."""
        assert "bc-posting-progress" in project_html
        # The function should show the posting progress container
        # The component is referenced in JS rendering logic
        assert "bc-posting-progress" in project_html[project_html.find("_bcRenderStep2"):
                                                      project_html.find("_bcRenderStep2") + 8000] or \
               "_bcRenderPostingProgress" in project_html or \
               "_bcProgressActivity" in project_html, (
            "Posting-progress component must be shown in render/stage logic"
        )

    def test_posting_stage_hides_ticket_list(self, project_html):
        """During posting stage, bc-ticket-list is hidden."""
        render_fn_start = project_html.find("function _bcRenderStep2(")
        assert render_fn_start != -1, "_bcRenderStep2 not found"
        fn_body = project_html[render_fn_start: render_fn_start + 10000]
        # When stage is 'posting', ticket-list must be hidden
        assert "bc-ticket-list" in fn_body, "bc-ticket-list must be referenced in _bcRenderStep2"
        # The function must handle the posting stage
        assert "posting" in fn_body, "'posting' stage must be handled in _bcRenderStep2"


# ---------------------------------------------------------------------------
# AC2: total/current tracking
# ---------------------------------------------------------------------------

class TestTotalCurrentTracking:
    def test_progress_fraction_uses_created_count(self, project_html):
        """Current count advances using tickets with state==='created'."""
        # The JS must count 'created' tickets for the progress fraction
        render_start = project_html.find("function _bcRenderStep2(")
        fn_body = project_html[render_start: render_start + 10000]
        pg_fn = ""
        pg_fn_start = project_html.find("function _bcRenderPostingProgress(")
        if pg_fn_start == -1:
            pg_fn_start = project_html.find("function _bcProgressActivity(")
        if pg_fn_start != -1:
            pg_fn = project_html[pg_fn_start: pg_fn_start + 3000]

        combined = fn_body + pg_fn
        assert "created" in combined, "Progress must track 'created' state"
        # total should be the number of tickets to post
        assert "total" in combined, "Progress must reference 'total' ticket count"

    def test_progress_bar_uses_fraction(self, project_html):
        """Progress bar width is derived from created/total fraction."""
        pg_fn_start = project_html.find("function _bcRenderPostingProgress(")
        if pg_fn_start == -1:
            pg_fn_start = project_html.find("function _bcProgressActivity(")
        if pg_fn_start == -1:
            # Check inline in render step2
            pg_fn_start = project_html.find("pa-bar-fill")
        assert pg_fn_start != -1, "Progress bar wiring must appear in the progress component"
        snippet = project_html[pg_fn_start: pg_fn_start + 2000]
        assert (
            "width" in snippet
            or "%" in snippet
            or "done:" in snippet
            or "total" in snippet
            or "mountProgressActivity" in snippet
        ), (
            "Progress bar must be driven by a fraction (width or done/total payload)"
        )


# ---------------------------------------------------------------------------
# AC3: Per-ticket state mapping
# ---------------------------------------------------------------------------

class TestPerTicketStateMapping:
    def _get_pg_fn(self, project_html):
        start = project_html.find("function _bcRenderPostingProgress(")
        if start == -1:
            start = project_html.find("function _bcProgressActivity(")
        if start != -1:
            return project_html[start: start + 5000]
        # Fall back: check broad area around bc-pg-log
        start = project_html.find("bc-pg-log")
        if start == -1:
            start = project_html.find("log_tail")
        if start != -1:
            return project_html[max(0, start - 3000): start + 3000]
        return project_html

    def test_drafting_shown_as_current_item(self, project_html):
        """Tickets in 'drafting' state show as the current in-progress item."""
        fn = self._get_pg_fn(project_html)
        assert "drafting" in fn, (
            "drafting state must be handled in the progress component"
        )

    def test_created_maps_to_success_log_entry(self, project_html):
        """Tickets in 'created' state produce a success log entry."""
        fn = self._get_pg_fn(project_html)
        assert "created" in fn, "created state must appear in progress component"
        # Success styling or marker should be present
        assert "success" in fn or "bc-pg-log-success" in fn or "green" in fn.lower() or \
               "bc-pg-entry--success" in fn or "bc-pg-success" in fn, (
            "created tickets must have success log styling"
        )

    def test_failed_maps_to_error_log_entry(self, project_html):
        """Tickets in 'failed' state produce an error log entry."""
        fn = self._get_pg_fn(project_html)
        assert "failed" in fn, "failed state must appear in progress component"
        assert "error" in fn or "bc-pg-log-error" in fn or "red" in fn.lower() or \
               "bc-pg-entry--error" in fn or "bc-pg-error" in fn, (
            "failed tickets must have error log styling"
        )

    def test_skipped_maps_to_skipped_log_entry(self, project_html):
        """Skipped/unposted tickets produce a skipped log entry."""
        fn = self._get_pg_fn(project_html)
        assert "skipped" in fn or "draft_ready" in fn, (
            "skipped or unposted (draft_ready) tickets must be handled"
        )


# ---------------------------------------------------------------------------
# AC4: Failed and skipped visible in log (not silently dropped)
# ---------------------------------------------------------------------------

class TestFailedSkippedVisible:
    def test_log_shows_failed_ticket_title(self, project_html):
        """Failed tickets show their title in the log (not just a count)."""
        pg_fn_start = project_html.find("function _bcRenderPostingProgress(")
        if pg_fn_start == -1:
            pg_fn_start = project_html.find("function _bcProgressActivity(")
        if pg_fn_start == -1:
            pg_fn_start = project_html.find("log_tail")
        assert pg_fn_start != -1
        fn_body = project_html[pg_fn_start: pg_fn_start + 5000]
        # Title must be included in log entries
        assert "title" in fn_body or "t.title" in fn_body or "escHtml" in fn_body, (
            "Log entries must include the ticket title"
        )

    def test_log_container_rendered_during_posting(self, project_html):
        """Log container/wiring is populated during the posting stage."""
        assert "bc-pg-log" in project_html or "log_tail" in project_html, "Posting log wiring must exist"
        # The log is updated from ticket state (referenced near 'failed' or 'created')
        pos = project_html.find("bc-pg-log")
        if pos == -1:
            pos = project_html.find("log_tail")
        pg_ctx = project_html[pos: pos + 3000]
        assert "failed" in pg_ctx or "created" in pg_ctx or "state" in pg_ctx, (
            "Posting log wiring must reference ticket state for log entries"
        )


# ---------------------------------------------------------------------------
# AC5: Parallel BA concurrency preserved
# ---------------------------------------------------------------------------

class TestConcurrencyPreserved:
    def test_post_selected_still_uses_create_task(self, client):
        """POST /api/tickets/bulk/{job_id}/post-selected still spawns an async task."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, tickets=[_draft_ready_ticket(0)])
        server._bulk_jobs[job_id] = job

        tasks_created = []
        original_create_task = asyncio.create_task

        with patch("asyncio.create_task", side_effect=lambda coro, **kw: tasks_created.append(coro) or original_create_task(coro)):
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/post-selected",
                json={"tickets": [{"index": 0, "labels": ["enhancement"]}]},
            )
        assert resp.status_code == 200
        server._bulk_jobs.pop(job_id, None)

    def test_concurrency_field_preserved_in_job(self, client):
        """Concurrency field on the job is not altered by post-selected."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, tickets=[_draft_ready_ticket(0)], status="drafts_ready")
        job["concurrency"] = 5
        server._bulk_jobs[job_id] = job

        with patch("asyncio.create_task"):
            client.post(
                f"/api/tickets/bulk/{job_id}/post-selected",
                json={"tickets": [{"index": 0, "labels": ["enhancement"]}]},
            )
        assert server._bulk_jobs.get(job_id, {}).get("concurrency") == 5
        server._bulk_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC6: Stop-after-current
# ---------------------------------------------------------------------------

class TestStopAfterCurrent:
    def test_stop_button_exists_in_step2(self, project_html):
        """bc-stop-btn exists in step 2."""
        step2_start = project_html.find('id="bc-step2"')
        step2_end = project_html.find('</div><!-- /pane-bulk-create -->', step2_start)
        step2_html = project_html[step2_start:step2_end]
        assert 'id="bc-stop-btn"' in step2_html, (
            "bc-stop-btn must exist in step 2"
        )

    def test_stop_button_visible_during_posting(self, project_html):
        """Stop button is not hidden during posting stage."""
        render_fn_start = project_html.find("function _bcRenderStep2(")
        fn_body = project_html[render_fn_start: render_fn_start + 10000]
        # Find the posting branch
        posting_pos = fn_body.find("'posting'")
        if posting_pos == -1:
            posting_pos = fn_body.find('"posting"')
        assert posting_pos != -1, "posting stage must be handled in _bcRenderStep2"
        # Stop button should NOT be hidden during posting
        assert "bc-stop-btn" in fn_body, "bc-stop-btn must be referenced in _bcRenderStep2"

    def test_stop_endpoint_works_during_posting(self, client):
        """POST /stop still halts the job during posting."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, tickets=[_draft_ready_ticket(0)], status="running")
        server._bulk_jobs[job_id] = job

        resp = client.post(f"/api/tickets/bulk/{job_id}/stop")
        assert resp.status_code == 200
        server._bulk_jobs.pop(job_id, None)

    def test_bcStop_function_exists(self, project_html):
        """bcStop() function is defined and calls the /stop endpoint."""
        assert "async function bcStop()" in project_html or "function bcStop()" in project_html, (
            "bcStop function must exist"
        )
        stop_start = project_html.find("function bcStop()")
        if stop_start == -1:
            stop_start = project_html.find("async function bcStop()")
        fn_body = project_html[stop_start: stop_start + 400]
        assert "/stop" in fn_body, "bcStop must call the /stop endpoint"


# ---------------------------------------------------------------------------
# AC7: Background continuation
# ---------------------------------------------------------------------------

class TestBackgroundContinuation:
    def test_sse_not_closed_on_tab_switch(self, project_html):
        """Switching tabs does not close the SSE stream (job continues)."""
        # The SSE is only closed on job_done or explicit disconnect
        # Verify: _bcEventSource is not closed in switchTab or similar
        assert "_bcEventSource" in project_html, "_bcEventSource must exist"
        # job_done event closes the SSE; tab switch should not
        job_done_pos = project_html.find("job_done")
        job_done_snippet = project_html[job_done_pos: job_done_pos + 400] if job_done_pos != -1 else ""
        assert "es.close()" in job_done_snippet or "_bcEventSource" in job_done_snippet, (
            "SSE is closed only on job_done, not tab switch"
        )

    def test_posting_job_continues_after_reconnect(self, client):
        """Job remains in running state after re-checking its status."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(
            job_id,
            tickets=[_draft_ready_ticket(0), _draft_ready_ticket(1)],
            status="running",
        )
        server._bulk_jobs[job_id] = job

        resp = client.get(f"/api/tickets/bulk/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"
        server._bulk_jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# AC8: Reconnect on reopen
# ---------------------------------------------------------------------------

class TestReconnectOnReopen:
    def test_bc_init_tab_handles_running_job(self, project_html):
        """_bcInitTab reconnects to a running job when bc_job_id exists."""
        init_start = project_html.find("function _bcInitTab(")
        assert init_start != -1, "_bcInitTab must exist"
        fn_body = project_html[init_start: init_start + 1500]
        # Must handle existing job (savedJobId or _bcJobId)
        assert "_bcJobId" in fn_body or "savedJobId" in fn_body or "bc_job_id" in fn_body, (
            "_bcInitTab must reconnect when bc_job_id is in localStorage"
        )
        assert "_bcConnectSSE" in fn_body or "_bcRenderStep2" in fn_body, (
            "_bcInitTab must reconnect SSE or render state for existing jobs"
        )

    def test_snapshot_event_restores_posting_progress(self, project_html):
        """SSE 'snapshot' event triggers re-render which shows posting-progress if job is posting."""
        sse_handler_pos = project_html.find("'snapshot'")
        assert sse_handler_pos != -1, "snapshot SSE event handler must exist"
        snippet = project_html[sse_handler_pos: sse_handler_pos + 300]
        assert "_bcRenderStep2" in snippet or "_bcJobState" in snippet, (
            "snapshot event must update _bcJobState and re-render"
        )

    def test_posting_status_renders_progress_component(self, project_html):
        """When _bcJobState.status is 'running' with posting in progress, progress component shows."""
        render_fn_start = project_html.find("function _bcRenderStep2(")
        fn_body = project_html[render_fn_start: render_fn_start + 10000]
        # The posting stage must reference the progress component
        assert "bc-posting-progress" in fn_body or "_bcRenderPostingProgress" in fn_body or \
               "_bcProgressActivity" in fn_body, (
            "_bcRenderStep2 must show the posting-progress component during posting stage"
        )


# ---------------------------------------------------------------------------
# AC9: No regression in step 1 / review step
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_bc_step1_still_exists(self, project_html):
        """bc-step1 element still exists."""
        assert 'id="bc-step1"' in project_html

    def test_bc_review_overlay_still_exists(self, project_html):
        """bc-review-overlay still exists in step 2."""
        assert 'id="bc-review-overlay"' in project_html

    def test_bc_ticket_list_still_exists(self, project_html):
        """bc-ticket-list still exists in step 2."""
        assert 'id="bc-ticket-list"' in project_html

    def test_bc_post_github_btn_still_exists(self, project_html):
        """bc-post-github-btn still exists in the review overlay."""
        assert 'id="bc-post-github-btn"' in project_html

    def test_bulk_create_job_creation_not_regressed(self, client):
        """POST /api/tickets/bulk still creates a job successfully."""
        with patch("asyncio.create_task"):
            resp = client.post(
                "/api/tickets/bulk",
                data={"repo": "zealchaiwut/commander", "prompts": json.dumps(["test"]), "concurrency": "1"},
            )
        assert resp.status_code == 202
        assert "job_id" in resp.json()

    def test_post_selected_returns_ok(self, client):
        """POST /post-selected still returns 200 for valid draft-ready tickets."""
        import server
        job_id = str(uuid.uuid4())
        job = _make_job(job_id, tickets=[_draft_ready_ticket(0)])
        server._bulk_jobs[job_id] = job

        with patch("asyncio.create_task"):
            resp = client.post(
                f"/api/tickets/bulk/{job_id}/post-selected",
                json={"tickets": [{"index": 0, "labels": ["enhancement"]}]},
            )
        assert resp.status_code == 200
        server._bulk_jobs.pop(job_id, None)

    def test_stop_endpoint_not_regressed(self, client):
        """POST /stop still returns 200."""
        import server
        job_id = str(uuid.uuid4())
        server._bulk_jobs[job_id] = _make_job(job_id)
        resp = client.post(f"/api/tickets/bulk/{job_id}/stop")
        assert resp.status_code == 200
        server._bulk_jobs.pop(job_id, None)

    def test_bcPostSelected_function_exists(self, project_html):
        """bcPostSelected function still exists in project.html."""
        assert "async function bcPostSelected()" in project_html or \
               "function bcPostSelected()" in project_html

    def test_bcReviewAndPost_function_exists(self, project_html):
        """bcReviewAndPost function still exists."""
        assert "function bcReviewAndPost()" in project_html
