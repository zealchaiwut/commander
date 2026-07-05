"""Tests for issue #502: run_estimator() returns (result, error_type) tuple.

Acceptance Criteria:
  AC1 — run_estimator() returns (dict, None) on success (2-tuple on all paths)
  AC2 — Returns (None, "model_error") when claude exits non-zero after all retries
  AC3 — Returns (None, "parse_error") when output is not valid JSON after all retries
  AC4 — Returns (None, "network_error") on subprocess.TimeoutExpired after all retries
  AC5 — Returns (None, "missing_claude") immediately when claude CLI is not found
  AC6 — POST /api/issues/{id}/estimate returns 500 with {message, error_type}
        when estimation fails
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.estimate_issue import (  # noqa: E402
    run_estimator,
)

_ISSUE_DATA = {"title": "Test issue", "body": "Some body."}

_VALID_PAYLOAD = {
    "size": "S",
    "estimated_hours": 1,
    "confidence": "high",
    "files_likely_affected": [],
    "depends_on": [],
    "blocks": [],
    "risk_flags": [],
    "summary": "Small fix.",
}


def _ok(payload=None):
    p = MagicMock()
    p.returncode = 0
    p.stdout = json.dumps(payload or _VALID_PAYLOAD)
    p.stderr = ""
    return p


def _fail(returncode=1, stderr="model error"):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = ""
    p.stderr = stderr
    return p


def _parse_fail():
    p = MagicMock()
    p.returncode = 0
    p.stdout = "not json at all"
    p.stderr = ""
    return p


# ── AC1: success returns 2-tuple ──────────────────────────────────────────────

class TestAC1SuccessTuple:
    """AC1 — run_estimator() returns (dict, None) on success."""

    def test_success_returns_two_tuple(self):
        with patch("subprocess.run", return_value=_ok()), \
             patch("time.sleep"):
            result = run_estimator(1, _ISSUE_DATA)
        assert isinstance(result, tuple) and len(result) == 2

    def test_success_error_type_is_none(self):
        with patch("subprocess.run", return_value=_ok()), \
             patch("time.sleep"):
            _, error_type = run_estimator(1, _ISSUE_DATA)
        assert error_type is None

    def test_success_result_is_dict(self):
        with patch("subprocess.run", return_value=_ok()), \
             patch("time.sleep"):
            result, _ = run_estimator(1, _ISSUE_DATA)
        assert isinstance(result, dict)
        assert result.get("size") == "S"


# ── AC2: model_error ──────────────────────────────────────────────────────────

class TestAC2ModelError:
    """AC2 — Returns (None, "model_error") when claude exits non-zero after all retries."""

    def test_nonzero_exit_returns_model_error(self):
        with patch("subprocess.run", return_value=_fail(returncode=1)), \
             patch("time.sleep"):
            _, error_type = run_estimator(1, _ISSUE_DATA)
        assert error_type == "model_error"

    def test_model_error_result_is_none(self):
        with patch("subprocess.run", return_value=_fail(returncode=1)), \
             patch("time.sleep"):
            result, _ = run_estimator(1, _ISSUE_DATA)
        assert result is None


# ── AC3: parse_error ──────────────────────────────────────────────────────────

class TestAC3ParseError:
    """AC3 — Returns (None, "parse_error") when output is not valid JSON after all retries."""

    def test_invalid_json_returns_parse_error(self):
        with patch("subprocess.run", return_value=_parse_fail()), \
             patch("time.sleep"):
            _, error_type = run_estimator(1, _ISSUE_DATA)
        assert error_type == "parse_error"

    def test_non_json_wrapped_in_text_returns_parse_error(self):
        p = MagicMock()
        p.returncode = 0
        p.stdout = "Here is my answer: no JSON here at all"
        p.stderr = ""
        with patch("subprocess.run", return_value=p), \
             patch("time.sleep"):
            _, error_type = run_estimator(1, _ISSUE_DATA)
        assert error_type == "parse_error"


# ── AC4: network_error ────────────────────────────────────────────────────────

class TestAC4NetworkError:
    """AC4 — Returns (None, "network_error") on subprocess.TimeoutExpired after all retries."""

    def test_timeout_returns_network_error(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=180)), \
             patch("time.sleep"):
            _, error_type = run_estimator(1, _ISSUE_DATA)
        assert error_type == "network_error"

    def test_network_error_result_is_none(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=180)), \
             patch("time.sleep"):
            result, _ = run_estimator(1, _ISSUE_DATA)
        assert result is None


# ── AC5: missing_claude ───────────────────────────────────────────────────────

class TestAC5MissingClaude:
    """AC5 — Returns (None, "missing_claude") immediately when claude CLI is not found."""

    def test_file_not_found_returns_missing_claude(self):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            _, error_type = run_estimator(1, _ISSUE_DATA)
        assert error_type == "missing_claude"

    def test_missing_claude_does_not_retry(self):
        call_count = []
        def raise_fnf(*a, **k):
            call_count.append(1)
            raise FileNotFoundError()

        with patch("subprocess.run", side_effect=raise_fnf), \
             patch("time.sleep"):
            run_estimator(1, _ISSUE_DATA)

        assert len(call_count) == 1, (
            f"missing_claude must short-circuit without retrying; got {len(call_count)} call(s)"
        )


# ── AC6: API endpoint returns error_type in 500 ───────────────────────────────

class TestAC6ApiErrorType:
    """AC6 — POST /api/issues/{id}/estimate returns 500 with {message, error_type}."""

    def _client(self):
        from fastapi.testclient import TestClient
        from server import app
        return TestClient(app, raise_server_exceptions=False)

    def _post_estimate(self, error_type_val):
        """Helper: post to estimate endpoint with estimator returning (None, error_type_val)."""
        with (
            patch("server._ei_fetch_issue", return_value={"title": "T", "body": "B"}),
            patch("server._ei_run_estimator", return_value=(None, error_type_val)),
        ):
            client = self._client()
            return client.post("/api/issues/42/estimate?repo=test/repo")

    def test_500_includes_error_type_model_error(self):
        resp = self._post_estimate("model_error")
        assert resp.status_code == 500
        body = resp.json()
        assert body.get("detail", {}).get("error_type") == "model_error"

    def test_500_includes_error_type_parse_error(self):
        resp = self._post_estimate("parse_error")
        assert resp.status_code == 500
        body = resp.json()
        assert body.get("detail", {}).get("error_type") == "parse_error"

    def test_500_includes_error_type_network_error(self):
        resp = self._post_estimate("network_error")
        assert resp.status_code == 500
        body = resp.json()
        assert body.get("detail", {}).get("error_type") == "network_error"

    def test_500_includes_message(self):
        resp = self._post_estimate("model_error")
        assert resp.status_code == 500
        body = resp.json()
        assert "message" in body.get("detail", {})
        assert "42" in body["detail"]["message"]
