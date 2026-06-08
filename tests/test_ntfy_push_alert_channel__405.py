"""Tests for issue #405 — Add ntfy push-to-phone alert channel to sprint_manager.

AC-1  AlertMode.NTFY = "ntfy" defined; ALL_MODES includes it; --alert-mode ntfy passes validation.
AC-2  _alert_ntfy(title, body, category=None) implemented: reads NTFY_TOPIC_URL, returns silently
      if unset, POSTs with Title/Priority/Tags headers and ~5s timeout.
AC-3  Priority mapping: failure/needs-rework -> "4" (high); all others -> "3" (default).
AC-4  dispatch_alerts() routes AlertMode.NTFY to _alert_ntfy(title, body, category).
AC-5  apps/dashboard/.env.example has commented NTFY_TOPIC_URL example line.
AC-6  NTFY_TOPIC_URL unset -> _alert_ntfy returns silently, no exception.
AC-7  Exception in _alert_ntfy caught by dispatch_alerts, logged via structured_log.error,
      sprint continues (other alert modes still fire).
"""

import importlib
import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

TESTER_ROOT = Path(__file__).resolve().parents[1]
SM_PATH = TESTER_ROOT / "services" / "sprint_manager" / "sprint_manager.py"


def _load_sm():
    spec = importlib.util.spec_from_file_location("sprint_manager", SM_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sprint_manager"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── AC-1: AlertMode.NTFY defined and validation passes ───────────────────────

class TestAlertModeNtfyDefined(unittest.TestCase):
    def setUp(self):
        self.sm = _load_sm()

    def test_ntfy_value_is_ntfy_string(self):
        assert self.sm.AlertMode.NTFY == "ntfy"

    def test_ntfy_in_all_modes(self):
        assert "ntfy" in self.sm.AlertMode.ALL_MODES

    def test_ntfy_passes_validation_check(self):
        assert self.sm.AlertMode.NTFY in self.sm.AlertMode.ALL_MODES, \
            "AlertMode.NTFY must be in ALL_MODES so --alert-mode ntfy passes validation"


# ── AC-2: _alert_ntfy signature, URL read, POST, headers, timeout ─────────────

class TestAlertNtfyImplemented(unittest.TestCase):
    def setUp(self):
        self.sm = _load_sm()

    def test_function_exists(self):
        assert hasattr(self.sm, "_alert_ntfy"), "_alert_ntfy not found in sprint_manager"

    def test_signature_has_required_params(self):
        import inspect
        sig = inspect.signature(self.sm._alert_ntfy)
        params = list(sig.parameters.keys())
        assert "title" in params
        assert "body" in params
        assert "category" in params

    def test_category_has_default_none(self):
        import inspect
        sig = inspect.signature(self.sm._alert_ntfy)
        assert sig.parameters["category"].default is None

    def _post_captured(self, category=None):
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return MagicMock()
        with patch.dict(os.environ, {"NTFY_TOPIC_URL": "https://ntfy.sh/test-topic"}):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                self.sm._alert_ntfy("My Title", "My Body", category=category)
        return captured

    def test_uses_post_method(self):
        c = self._post_captured()
        assert c["req"].method == "POST"

    def test_posts_to_ntfy_topic_url(self):
        c = self._post_captured()
        assert c["req"].full_url == "https://ntfy.sh/test-topic"

    def test_title_header_set(self):
        c = self._post_captured()
        assert c["req"].get_header("Title") == "My Title"

    def test_priority_header_present(self):
        c = self._post_captured()
        assert c["req"].get_header("Priority") is not None

    def test_tags_header_present(self):
        c = self._post_captured()
        headers_lower = {k.lower(): v for k, v in c["req"].headers.items()}
        assert "tags" in headers_lower

    def test_timeout_lte_5s(self):
        c = self._post_captured()
        assert c["timeout"] is not None and c["timeout"] <= 5

    def test_body_encoded_as_bytes(self):
        c = self._post_captured()
        assert isinstance(c["req"].data, bytes)


# ── AC-3: Priority mapping ────────────────────────────────────────────────────

class TestAlertNtfyPriorityMapping(unittest.TestCase):
    def setUp(self):
        self.sm = _load_sm()

    def _priority_for(self, category):
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return MagicMock()
        with patch.dict(os.environ, {"NTFY_TOPIC_URL": "https://ntfy.sh/test-topic"}):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                self.sm._alert_ntfy("T", "B", category=category)
        return captured["req"].get_header("Priority")

    def test_failure_maps_to_4(self):
        assert self._priority_for("failure") == "4"

    def test_needs_rework_maps_to_4(self):
        assert self._priority_for("needs-rework") == "4"

    def test_success_maps_to_3(self):
        assert self._priority_for("success") == "3"

    def test_info_maps_to_3(self):
        assert self._priority_for("info") == "3"

    def test_none_category_maps_to_3(self):
        assert self._priority_for(None) == "3"

    def test_arbitrary_category_maps_to_3(self):
        assert self._priority_for("sprint-complete") == "3"


# ── AC-4: dispatch_alerts routes NTFY to _alert_ntfy ─────────────────────────

class TestDispatchAlertsRoutesNtfy(unittest.TestCase):
    def setUp(self):
        self.sm = _load_sm()

    def test_ntfy_mode_calls_alert_ntfy(self):
        with patch.object(self.sm, "_alert_ntfy") as mock_ntfy:
            self.sm.dispatch_alerts(
                alert_modes=[self.sm.AlertMode.NTFY],
                title="Sprint done",
                body="Details here",
                category="success",
            )
            mock_ntfy.assert_called_once_with("Sprint done", "Details here", "success")

    def test_ntfy_not_called_for_file_mode(self):
        with patch.object(self.sm, "_alert_ntfy") as mock_ntfy:
            with patch.object(self.sm, "_alert_file"):
                self.sm.dispatch_alerts(
                    alert_modes=[self.sm.AlertMode.FILE],
                    title="T",
                    body="B",
                )
            mock_ntfy.assert_not_called()

    def test_ntfy_not_called_for_none_mode(self):
        with patch.object(self.sm, "_alert_ntfy") as mock_ntfy:
            self.sm.dispatch_alerts(
                alert_modes=[self.sm.AlertMode.NONE],
                title="T",
                body="B",
            )
            mock_ntfy.assert_not_called()


# ── AC-5: .env.example has commented NTFY_TOPIC_URL ──────────────────────────

class TestEnvExampleNtfy(unittest.TestCase):
    ENV_EXAMPLE = TESTER_ROOT / "apps" / "dashboard" / ".env.example"

    def test_env_example_exists(self):
        assert self.ENV_EXAMPLE.exists(), f".env.example not found at {self.ENV_EXAMPLE}"

    def test_ntfy_topic_url_present(self):
        content = self.ENV_EXAMPLE.read_text()
        assert "NTFY_TOPIC_URL" in content

    def test_ntfy_line_is_commented(self):
        content = self.ENV_EXAMPLE.read_text()
        for line in content.splitlines():
            if "NTFY_TOPIC_URL" in line:
                assert line.strip().startswith("#"), \
                    f"NTFY_TOPIC_URL line must be commented out, got: {line!r}"
                break

    def test_ntfy_example_references_ntfy_sh(self):
        content = self.ENV_EXAMPLE.read_text()
        for line in content.splitlines():
            if "NTFY_TOPIC_URL" in line:
                assert "ntfy.sh" in line, f"Expected ntfy.sh URL in example, got: {line!r}"
                break


# ── AC-6: NTFY_TOPIC_URL unset -> silent return ───────────────────────────────

class TestAlertNtfySilentWhenUnset(unittest.TestCase):
    def setUp(self):
        self.sm = _load_sm()

    def _env_without_ntfy(self):
        return {k: v for k, v in os.environ.items() if k != "NTFY_TOPIC_URL"}

    def test_no_exception_when_unset(self):
        with patch.dict(os.environ, self._env_without_ntfy(), clear=True):
            self.sm._alert_ntfy("title", "body", category="failure")

    def test_no_http_request_when_unset(self):
        with patch.dict(os.environ, self._env_without_ntfy(), clear=True):
            with patch("urllib.request.urlopen") as mock_open:
                self.sm._alert_ntfy("title", "body")
                mock_open.assert_not_called()

    def test_empty_string_url_treated_as_unset(self):
        with patch.dict(os.environ, {"NTFY_TOPIC_URL": ""}):
            with patch("urllib.request.urlopen") as mock_open:
                self.sm._alert_ntfy("title", "body")
                mock_open.assert_not_called()


# ── AC-7: Exception caught, logged, sprint continues ─────────────────────────

class TestAlertNtfyExceptionIsolation(unittest.TestCase):
    def setUp(self):
        self.sm = _load_sm()

    def test_exception_does_not_propagate(self):
        with patch.object(self.sm, "_alert_ntfy", side_effect=Exception("connection refused")):
            with patch.object(self.sm.structured_log, "error"):
                self.sm.dispatch_alerts(
                    alert_modes=[self.sm.AlertMode.NTFY],
                    title="T",
                    body="B",
                    category="failure",
                )

    def test_exception_logged_via_structured_log_error(self):
        with patch.object(self.sm, "_alert_ntfy", side_effect=Exception("timeout")):
            with patch.object(self.sm.structured_log, "error") as mock_err:
                self.sm.dispatch_alerts(
                    alert_modes=[self.sm.AlertMode.NTFY],
                    title="T",
                    body="B",
                    category="failure",
                )
                mock_err.assert_called_once()

    def test_other_modes_still_fire_after_ntfy_exception(self):
        file_called = []
        with patch.object(self.sm, "_alert_ntfy", side_effect=RuntimeError("boom")):
            with patch.object(self.sm, "_alert_file",
                              side_effect=lambda *a, **kw: file_called.append(True)):
                with patch.object(self.sm.structured_log, "error"):
                    self.sm.dispatch_alerts(
                        alert_modes=[self.sm.AlertMode.NTFY, self.sm.AlertMode.FILE],
                        title="T",
                        body="B",
                        category="info",
                    )
        assert file_called, "_alert_file not called after ntfy exception — sprint interrupted"

    def test_unreachable_url_raises_and_is_caught(self):
        import urllib.error
        with patch.dict(os.environ, {"NTFY_TOPIC_URL": "https://unreachable.example.invalid/topic"}):
            with patch("urllib.request.urlopen",
                       side_effect=urllib.error.URLError("unreachable")):
                with patch.object(self.sm.structured_log, "error") as mock_err:
                    self.sm.dispatch_alerts(
                        alert_modes=[self.sm.AlertMode.NTFY],
                        title="T",
                        body="B",
                        category="failure",
                    )
                    mock_err.assert_called_once()


if __name__ == "__main__":
    unittest.main()
