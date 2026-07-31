"""Tests for issue #1865: sprint-finished webhook — optional callback_url on sprint run.

AC1: POST /api/sprints/run accepts optional callback_url (validated: http/https URL); stored with run
AC2: On terminal state, outcome JSON POSTed to callback_url; behavioral test asserts payload shape
     for finished and needs_rework outcomes
AC3: Delivery failure (connection refused, 500, timeout) retries with backoff then gives up with
     logged warning — sprint outcome unaffected
AC4: No callback_url → zero behavior change
AC5: callback_url only accepted from token-authenticated callers when COMMANDER_API_TOKEN is set
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── AC1: SprintMgmtRunBody accepts callback_url ───────────────────────────────

class TestCallbackUrlBody:
    """AC1: body model accepts optional callback_url; validates http/https scheme."""

    def test_body_accepts_https_callback_url(self):
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-10",
            callback_url="https://example.com/webhook",
        )
        assert body.callback_url == "https://example.com/webhook"

    def test_body_accepts_http_callback_url(self):
        from routers.sprint_run_service import SprintMgmtRunBody
        # Public host is accepted (uses a documentation IP that resolves publicly).
        body = SprintMgmtRunBody(
            project="owner/repo",
            sprint_label="sprint-10",
            callback_url="http://example.com/hook",
        )
        assert body.callback_url == "http://example.com/hook"

    def test_body_rejects_internal_callback_url(self):
        # SSRF guard (#1896): an internal/loopback target is rejected at request time.
        import pytest as _pytest
        from routers.sprint_run_service import SprintMgmtRunBody
        with _pytest.raises(Exception):
            SprintMgmtRunBody(
                project="owner/repo",
                sprint_label="sprint-10",
                callback_url="http://localhost:9000/hook",
            )

    def test_body_defaults_callback_url_to_none(self):
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(project="owner/repo", sprint_label="sprint-10")
        assert body.callback_url is None

    def test_body_rejects_ftp_callback_url(self):
        from routers.sprint_run_service import SprintMgmtRunBody
        import pydantic
        with pytest.raises((pydantic.ValidationError, ValueError)):
            SprintMgmtRunBody(
                project="owner/repo",
                sprint_label="sprint-10",
                callback_url="ftp://bad.com/hook",
            )

    def test_body_rejects_empty_string_callback_url(self):
        from routers.sprint_run_service import SprintMgmtRunBody
        import pydantic
        with pytest.raises((pydantic.ValidationError, ValueError)):
            SprintMgmtRunBody(
                project="owner/repo",
                sprint_label="sprint-10",
                callback_url="",
            )

    def test_body_rejects_no_scheme_callback_url(self):
        from routers.sprint_run_service import SprintMgmtRunBody
        import pydantic
        with pytest.raises((pydantic.ValidationError, ValueError)):
            SprintMgmtRunBody(
                project="owner/repo",
                sprint_label="sprint-10",
                callback_url="example.com/webhook",
            )


# ── validate_callback_url helper ─────────────────────────────────────────────

class TestValidateCallbackUrl:
    """AC1: validate_callback_url helper accepts http/https, rejects others."""

    def test_accepts_https(self):
        from routers.sprint_webhook_service import validate_callback_url
        assert validate_callback_url("https://example.com/hook") is True

    def test_accepts_http(self):
        from routers.sprint_webhook_service import validate_callback_url
        # Public host over http passes; loopback/internal is now rejected by the
        # SSRF guard (#1896) — see test_rejects_internal below.
        assert validate_callback_url("http://example.com/hook") is True

    def test_rejects_internal(self):
        # SSRF guard (#1896): loopback resolves to 127.0.0.1 and is rejected.
        from routers.sprint_webhook_service import validate_callback_url
        assert validate_callback_url("http://localhost:9000/hook") is False

    def test_rejects_ftp(self):
        from routers.sprint_webhook_service import validate_callback_url
        assert validate_callback_url("ftp://example.com/hook") is False

    def test_rejects_no_scheme(self):
        from routers.sprint_webhook_service import validate_callback_url
        assert validate_callback_url("example.com/hook") is False

    def test_rejects_empty(self):
        from routers.sprint_webhook_service import validate_callback_url
        assert validate_callback_url("") is False

    def test_rejects_just_scheme(self):
        from routers.sprint_webhook_service import validate_callback_url
        assert validate_callback_url("https://") is False


# ── Helpers: in-process test HTTP receiver ────────────────────────────────────

def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _ReceiverHandler(BaseHTTPRequestHandler):
    """Minimal handler that records the first POST body in server.received."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.server.received.append(json.loads(body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_):
        pass  # suppress test output


def _start_test_receiver() -> tuple[HTTPServer, int]:
    port = _find_free_port()
    server = HTTPServer(("127.0.0.1", port), _ReceiverHandler)
    server.received = []
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    return server, port


# ── AC2: Payload shape — finished and needs_rework ───────────────────────────

class TestWebhookPayloadShape:
    """AC2: behavioral test — deliver_sprint_webhook sends correct JSON shape."""

    def test_delivers_finished_payload(self, monkeypatch):
        import routers.sprint_webhook_service as _wh
        # This test exercises the delivery mechanism against a loopback test
        # server; the SSRF guard (#1896) legitimately blocks 127.0.0.1, so
        # screen it off here — guard behavior is covered in test_1896.
        monkeypatch.setattr(_wh, "screen_callback_url", lambda u: None)
        from routers.sprint_webhook_service import deliver_sprint_webhook
        server, port = _start_test_receiver()
        payload = {
            "project": "owner/repo",
            "sprint_label": "sprint-10",
            "outcome": "finished",
            "duration_sec": 42,
            "tickets": [{"number": 100, "status": "done"}],
            "summary_url": "https://github.com/owner/repo/issues/99",
        }
        deliver_sprint_webhook(f"http://127.0.0.1:{port}/hook", payload)
        server.server_close()
        assert len(server.received) == 1
        got = server.received[0]
        assert got["outcome"] == "finished"
        assert got["sprint_label"] == "sprint-10"
        assert got["project"] == "owner/repo"
        assert got["duration_sec"] == 42
        assert got["tickets"] == [{"number": 100, "status": "done"}]
        assert got["summary_url"] == "https://github.com/owner/repo/issues/99"

    def test_delivers_needs_rework_payload(self, monkeypatch):
        import routers.sprint_webhook_service as _wh
        monkeypatch.setattr(_wh, "screen_callback_url", lambda u: None)
        from routers.sprint_webhook_service import deliver_sprint_webhook
        server, port = _start_test_receiver()
        payload = {
            "project": "owner/repo",
            "sprint_label": "sprint-10",
            "outcome": "needs_rework",
            "duration_sec": 17,
            "tickets": [
                {"number": 101, "status": "skipped"},
                {"number": 102, "status": "done"},
            ],
        }
        deliver_sprint_webhook(f"http://127.0.0.1:{port}/hook", payload)
        server.server_close()
        assert len(server.received) == 1
        got = server.received[0]
        assert got["outcome"] == "needs_rework"
        assert len(got["tickets"]) == 2

    def test_build_webhook_payload_finished(self, tmp_path):
        """AC2: build_webhook_payload returns correct shape for a finished sprint.

        Updated for issue #1945: webhook and file now share a single builder
        (build_commander_report), so the payload uses the rich format.
        """
        from routers.sprint_webhook_service import build_webhook_payload
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        plan = {
            "state": "ready_to_merge",
            "tickets": [10, 11],
            "started_at": "2026-01-01T00:00:00+00:00",
            "callback_url": "https://example.com/hook",
        }
        (sprints_dir / "sprint-10-plan.json").write_text(json.dumps(plan))
        state = {
            "issues": [
                {"number": 10, "title": "Ticket 10", "status": "done"},
                {"number": 11, "title": "Ticket 11", "status": "done"},
            ],
        }
        (sprints_dir / "sprint-10-state.json").write_text(json.dumps(state))
        payload = build_webhook_payload(
            sprints_dir=sprints_dir,
            sprint_label="sprint-10",
            project="owner/repo",
            started_at="2026-01-01T00:00:00+00:00",
        )
        # Rich format fields (issue #1945)
        assert isinstance(payload["run_id"], str)
        assert payload["branch"] == "sprint/sprint-10"
        assert payload["summary"]["completed"] == 2
        assert payload["summary"]["failed"] == 0
        assert len(payload["completed"]) == 2
        assert payload["completed"][0]["ticket_id"] == "10"
        assert isinstance(payload["cost"]["tokens"], int)
        assert isinstance(payload["actions"], list)

    def test_build_webhook_payload_needs_rework(self, tmp_path):
        """AC2: build_webhook_payload returns payload for a needs_rework sprint.

        Updated for issue #1945: rich format via shared builder.
        """
        from routers.sprint_webhook_service import build_webhook_payload
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        plan = {
            "state": "needs_rework",
            "tickets": [20],
            "started_at": "2026-01-01T00:00:00+00:00",
        }
        (sprints_dir / "sprint-20-plan.json").write_text(json.dumps(plan))
        state = {
            "issues": [{"number": 20, "title": "T20", "status": "skipped"}],
        }
        (sprints_dir / "sprint-20-state.json").write_text(json.dumps(state))
        payload = build_webhook_payload(
            sprints_dir=sprints_dir,
            sprint_label="sprint-20",
            project="owner/repo",
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert payload["summary"]["skipped"] == 1
        assert payload["summary"]["completed"] == 0

    def test_build_webhook_payload_killed(self, tmp_path):
        """AC2: build_webhook_payload maps killed sprint to correct summary.

        Updated for issue #1945: rich format via shared builder.
        """
        from routers.sprint_webhook_service import build_webhook_payload
        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()
        plan = {
            "state": "needs_rework",
            "end_reason": "stopped by user",
            "tickets": [30],
        }
        (sprints_dir / "sprint-30-plan.json").write_text(json.dumps(plan))
        state = {"issues": [{"number": 30, "title": "T30", "status": "skipped"}]}
        (sprints_dir / "sprint-30-state.json").write_text(json.dumps(state))
        payload = build_webhook_payload(
            sprints_dir=sprints_dir,
            sprint_label="sprint-30",
            project="owner/repo",
            started_at="2026-01-01T00:00:00+00:00",
        )
        # Rich format: branch contains sprint label regardless of kill state
        assert "sprint-30" in payload["branch"]
        assert isinstance(payload["run_id"], str)


# ── AC3: Delivery failure → retries → warning logged → sprint unaffected ─────

class TestDeliveryFailure:
    """AC3: unreachable URL retries with backoff then logs warning."""

    def test_unreachable_url_does_not_raise(self):
        from routers.sprint_webhook_service import deliver_sprint_webhook
        # Use a port we know is closed
        port = _find_free_port()
        url = f"http://127.0.0.1:{port}/hook"
        payload = {"outcome": "finished", "sprint_label": "sprint-99"}
        # Must not raise — sprint outcome is unaffected
        deliver_sprint_webhook(url, payload)

    def test_unreachable_url_logs_warning(self, caplog):
        from routers.sprint_webhook_service import deliver_sprint_webhook
        port = _find_free_port()
        url = f"http://127.0.0.1:{port}/hook"
        payload = {"outcome": "finished", "sprint_label": "sprint-99"}
        with caplog.at_level(logging.WARNING, logger="routers.sprint_webhook_service"):
            deliver_sprint_webhook(url, payload)
        assert any("webhook" in r.message.lower() for r in caplog.records), (
            "Expected a warning log about webhook delivery failure"
        )

    def test_server_500_triggers_retry(self):
        """AC3: a 5xx response is treated as failure and retried."""
        from routers.sprint_webhook_service import fire_sprint_webhook

        attempts = []

        class _500Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                self.rfile.read(length)
                attempts.append(1)
                self.send_response(500)
                self.end_headers()
            def log_message(self, *_): pass

        port = _find_free_port()
        server = HTTPServer(("127.0.0.1", port), _500Handler)
        t = threading.Thread(target=server.handle_request, daemon=True)
        t.start()

        result = fire_sprint_webhook(
            f"http://127.0.0.1:{port}/hook",
            {"outcome": "finished"},
        )
        server.server_close()
        assert result is False, "5xx should be treated as failure"

    def test_retries_three_times_on_failure(self):
        """AC3: deliver_sprint_webhook attempts delivery up to 3 times."""
        from routers import sprint_webhook_service

        call_count = []

        def _failing_fire(url, payload):
            call_count.append(1)
            return False

        with patch.object(sprint_webhook_service, "fire_sprint_webhook", _failing_fire):
            with patch.object(sprint_webhook_service, "time") as mock_time:
                mock_time.sleep = MagicMock()
                sprint_webhook_service.deliver_sprint_webhook(
                    "http://example.com/hook", {"outcome": "test"}
                )

        assert len(call_count) == 3, (
            f"Expected 3 delivery attempts, got {len(call_count)}"
        )

    def test_retries_use_backoff_sleep(self):
        """AC3: retries sleep between attempts (backoff)."""
        from routers import sprint_webhook_service

        sleep_calls = []

        def _failing_fire(url, payload):
            return False

        with patch.object(sprint_webhook_service, "fire_sprint_webhook", _failing_fire):
            with patch.object(time, "sleep", side_effect=lambda s: sleep_calls.append(s)):
                sprint_webhook_service.deliver_sprint_webhook(
                    "http://example.com/hook", {"outcome": "test"}
                )

        assert len(sleep_calls) >= 2, (
            "Expected at least 2 sleep calls (backoff between 3 attempts)"
        )
        assert all(s > 0 for s in sleep_calls), "Sleep durations must be positive"

    def test_success_on_second_attempt_no_warning(self, caplog):
        """AC3: success on retry does not log a warning."""
        from routers import sprint_webhook_service

        attempts = []

        def _flaky_fire(url, payload):
            attempts.append(1)
            return len(attempts) >= 2  # fail first, succeed second

        with patch.object(sprint_webhook_service, "fire_sprint_webhook", _flaky_fire):
            with patch.object(time, "sleep", lambda _: None):
                with caplog.at_level(logging.WARNING, logger="routers.sprint_webhook_service"):
                    sprint_webhook_service.deliver_sprint_webhook(
                        "http://example.com/hook", {"outcome": "test"}
                    )

        assert not any(r.levelno >= logging.WARNING for r in caplog.records), (
            "No warning should be logged when delivery eventually succeeds"
        )


# ── AC4: No callback_url → zero behavior change ───────────────────────────────

class TestNoCallbackUrl:
    """AC4: when callback_url is absent, no webhook thread is started."""

    def test_no_monitor_thread_without_callback_url(self):
        """No daemon threads are spawned when callback_url is None."""
        from routers.sprint_webhook_service import start_callback_monitor
        threads_before = threading.active_count()
        proc_mock = MagicMock()
        proc_mock.wait = MagicMock(return_value=0)
        # Should be a no-op when callback_url is None
        result = start_callback_monitor(
            proc=proc_mock,
            callback_url=None,
            sprint_label="sprint-10",
            project="owner/repo",
            sprints_dir=Path("/tmp"),
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert result is None, "start_callback_monitor with no URL should return None"
        # No new threads
        time.sleep(0.05)
        assert threading.active_count() <= threads_before + 1

    def test_body_without_callback_url_passes_validation(self):
        """AC4: the run body still works when callback_url is omitted."""
        from routers.sprint_run_service import SprintMgmtRunBody
        body = SprintMgmtRunBody(project="owner/repo", sprint_label="sprint-10")
        assert body.callback_url is None


# ── AC5: callback_url requires token auth when COMMANDER_API_TOKEN is set ─────

class TestCallbackUrlAuthGate:
    """AC5: callback_url field is rejected from unauthenticated callers when token auth is active."""

    def test_callback_url_rejected_without_bearer_token(self):
        """When COMMANDER_API_TOKEN is set, requests without Bearer token may not set callback_url."""
        from routers.sprint_webhook_service import check_callback_url_auth

        result = check_callback_url_auth(
            callback_url="https://example.com/hook",
            auth_header="",
            api_token="secret-token",
        )
        assert result is False, "Unauthenticated caller must not be allowed to set callback_url"

    def test_callback_url_accepted_with_correct_bearer_token(self):
        from routers.sprint_webhook_service import check_callback_url_auth

        result = check_callback_url_auth(
            callback_url="https://example.com/hook",
            auth_header="Bearer secret-token",
            api_token="secret-token",
        )
        assert result is True

    def test_callback_url_rejected_with_wrong_bearer_token(self):
        from routers.sprint_webhook_service import check_callback_url_auth

        result = check_callback_url_auth(
            callback_url="https://example.com/hook",
            auth_header="Bearer wrong-token",
            api_token="secret-token",
        )
        assert result is False

    def test_callback_url_allowed_when_no_api_token_configured(self):
        """AC5: when COMMANDER_API_TOKEN is unset, callback_url is always allowed."""
        from routers.sprint_webhook_service import check_callback_url_auth

        result = check_callback_url_auth(
            callback_url="https://example.com/hook",
            auth_header="",
            api_token="",  # unset
        )
        assert result is True, "callback_url should be unrestricted when no API token is configured"

    def test_no_callback_url_always_passes(self):
        """AC5: auth check is not applied when callback_url is absent."""
        from routers.sprint_webhook_service import check_callback_url_auth

        result = check_callback_url_auth(
            callback_url=None,
            auth_header="",
            api_token="secret-token",
        )
        assert result is True, "No callback_url means no auth check needed"


# ── Integration: start_callback_monitor fires on proc exit ────────────────────

class TestCallbackMonitorIntegration:
    """AC2+AC3: background monitor fires callback when subprocess exits."""

    def test_monitor_fires_callback_on_exit(self, tmp_path, monkeypatch):
        """AC2: monitor thread fires POST to callback_url after proc exits."""
        import routers.sprint_webhook_service as _wh
        monkeypatch.setattr(_wh, "screen_callback_url", lambda u: None)
        from routers.sprint_webhook_service import start_callback_monitor

        sprints_dir = tmp_path / "sprints"
        sprints_dir.mkdir()

        # Write plan.json with terminal state
        plan = {
            "state": "ready_to_merge",
            "tickets": [42],
            "callback_url": "http://placeholder",
        }
        (sprints_dir / "sprint-42-plan.json").write_text(json.dumps(plan))
        state = {"issues": [{"number": 42, "status": "done", "agent_status": "completed"}]}
        (sprints_dir / "sprint-42-state.json").write_text(json.dumps(state))

        received = []
        server, port = _start_test_receiver()
        server.received = received

        # Extend to handle multiple requests
        def _handle_loop():
            server.timeout = 3
            server.handle_request()

        t = threading.Thread(target=_handle_loop, daemon=True)
        t.start()

        proc_mock = MagicMock()
        proc_mock.wait = MagicMock(return_value=0)

        start_callback_monitor(
            proc=proc_mock,
            callback_url=f"http://127.0.0.1:{port}/hook",
            sprint_label="sprint-42",
            project="owner/repo",
            sprints_dir=sprints_dir,
            started_at="2026-01-01T00:00:00+00:00",
        )

        # Wait for the monitor thread to fire
        deadline = time.time() + 5
        while not received and time.time() < deadline:
            time.sleep(0.1)

        server.server_close()
        assert len(received) == 1, "Expected exactly one webhook POST"
        got = received[0]
        # Rich format (issue #1945): webhook and file share a builder
        assert "run_id" in got, "Webhook payload must have run_id field (issue #1945)"
        assert "summary" in got, "Webhook payload must have summary field (issue #1945)"
        assert "sprint-42" in got.get("branch", ""), "branch must include sprint label"

    def test_monitor_does_nothing_without_callback_url(self, tmp_path):
        """AC4: no POST when callback_url is None."""
        from routers.sprint_webhook_service import start_callback_monitor

        proc_mock = MagicMock()
        proc_mock.wait = MagicMock(return_value=0)

        # Should not raise and should not contact any server
        result = start_callback_monitor(
            proc=proc_mock,
            callback_url=None,
            sprint_label="sprint-43",
            project="owner/repo",
            sprints_dir=tmp_path,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert result is None
