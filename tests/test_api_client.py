"""Tests for services/sprint_manager/api_client.py — issue #1669.

AC coverage:
  AC1  is_retryable_rate_limit recognises ICA/IBM gateway 429 quota-exceeded body
  AC1  is_retryable_rate_limit recognises ICA/IBM 429 generic rate-limit body
  AC2  ICA-matched errors use the same return shape as Anthropic — no separate branch
  AC3  Existing Anthropic OAuth shape still recognised (regression guard)
  AC3  Non-retryable 4xx must NOT trigger retry
  AC4  Retry-After value is parsed and returned when present
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ── import under test ─────────────────────────────────────────────────────────

from services.sprint_manager.api_client import is_retryable_rate_limit  # noqa: E402


# ── AC1: ICA/IBM quota-exceeded body ─────────────────────────────────────────

class TestICAQuotaExceeded:
    """ICA 429 with IBM quota-exceeded body triggers retry."""

    def test_quota_exceeded_keyword_detected(self):
        output = (
            "HTTP/1.1 429 Too Many Requests\n"
            '{"error": "quota_exceeded", "message": "User token quota has been exceeded"}\n'
        )
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True
        assert retry_after is None

    def test_quota_exceeded_human_readable_detected(self):
        output = (
            "Error from ICA gateway: quota exceeded — daily limit reached.\n"
            "Please wait before retrying.\n"
        )
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_usage_limit_exceeded_detected(self):
        output = (
            "IBM Cloud API error: usage_limit_exceeded\n"
            "Your account has reached its usage limit for this billing period.\n"
        )
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_usage_limit_exceeded_human_readable_detected(self):
        output = "gateway returned: usage limit exceeded for this API key\n"
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_token_limit_exceeded_detected(self):
        output = (
            "Status: 429\n"
            "token_limit_exceeded: You have consumed all available tokens for today.\n"
        )
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True


# ── AC1: ICA/IBM generic rate-limit body (429 with no IBM-specific code) ─────

class TestICAGenericRateLimit:
    """ICA 429 with generic rate-limit body triggers retry."""

    def test_ica_429_status_line_detected(self):
        output = "ICA proxy: received HTTP 429 from upstream\n"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_ica_too_many_requests_phrase_detected(self):
        output = "ICA gateway responded with: Too Many Requests (429)\n"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True


# ── AC2: ICA path returns same (bool, Optional[int]) shape as Anthropic ───────

class TestReturnShape:
    """Both ICA and Anthropic detections return (True, Optional[int])."""

    def test_ica_quota_returns_tuple(self):
        result = is_retryable_rate_limit("quota_exceeded")
        assert isinstance(result, tuple) and len(result) == 2

    def test_ica_quota_bool_is_true(self):
        is_rl, _ = is_retryable_rate_limit("quota_exceeded")
        assert is_rl is True

    def test_ica_no_retry_after_returns_none(self):
        _, retry_after = is_retryable_rate_limit("quota_exceeded")
        assert retry_after is None

    def test_anthropic_shape_also_tuple(self):
        result = is_retryable_rate_limit("subscription rate limit exceeded")
        assert isinstance(result, tuple) and len(result) == 2


# ── AC3: Anthropic OAuth shape regression ────────────────────────────────────

class TestAnthropicRegression:
    """Existing Anthropic OAuth rate-limit signals still detected after change."""

    def test_subscription_rate_limit_detected(self):
        output = "claude.ai: subscription rate limit exceeded — please wait"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_rate_limit_phrase_detected(self):
        output = "error: rate limit hit on claude.ai subscription"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_rate_underscore_limit_detected(self):
        output = 'API error {"type": "rate_limit", "message": "Rate limited"}'
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_429_bare_detected(self):
        output = "subprocess exited with code 1\nHTTP status 429\n"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True

    def test_too_many_requests_detected(self):
        output = "Response: 429 Too Many Requests"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is True


# ── AC3: Non-retryable 4xx must NOT trigger retry ────────────────────────────

class TestNonRetryable:
    """Non-rate-limit errors must not be treated as retryable."""

    def test_400_bad_request_not_retried(self):
        output = "HTTP 400 Bad Request: invalid_request_error — prompt too long"
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is False
        assert retry_after is None

    def test_401_unauthorized_not_retried(self):
        output = "HTTP 401 Unauthorized: invalid api key"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is False

    def test_403_forbidden_not_retried(self):
        output = "403 Forbidden: your account does not have access to this model"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is False

    def test_500_server_error_not_retried(self):
        output = "HTTP 500 Internal Server Error"
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is False

    def test_empty_output_not_retried(self):
        is_rl, retry_after = is_retryable_rate_limit("")
        assert is_rl is False
        assert retry_after is None

    def test_clean_success_output_not_retried(self):
        output = "Agent completed successfully. Pushed feature/1669-extend-rate-limit."
        is_rl, _ = is_retryable_rate_limit(output)
        assert is_rl is False


# ── AC4: Retry-After parsing ──────────────────────────────────────────────────

class TestRetryAfterParsing:
    """Retry-After seconds are extracted when present."""

    def test_retry_after_header_parsed(self):
        output = "HTTP 429 Too Many Requests\nRetry-After: 60\n"
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True
        assert retry_after == 60

    def test_retry_after_colon_space_parsed(self):
        output = "rate limit exceeded. retry after: 120 seconds"
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True
        assert retry_after == 120

    def test_quota_exceeded_with_retry_after(self):
        output = "quota_exceeded\nRetry-After: 30\n"
        is_rl, retry_after = is_retryable_rate_limit(output)
        assert is_rl is True
        assert retry_after == 30

    def test_no_retry_after_returns_none(self):
        output = "429 Too Many Requests"
        _, retry_after = is_retryable_rate_limit(output)
        assert retry_after is None


# ── sprint_manager backward-compat: _is_rate_limit_error still works ─────────

class TestSprintManagerBackwardCompat:
    """sprint_manager._is_rate_limit_error continues to work after extraction."""

    def test_sm_function_callable(self):
        import services.sprint_manager.sprint_manager as sm
        assert callable(sm._is_rate_limit_error)

    def test_sm_function_detects_anthropic_rate_limit(self):
        import services.sprint_manager.sprint_manager as sm
        is_rl, _ = sm._is_rate_limit_error("subscription rate limit exceeded")
        assert is_rl is True

    def test_sm_function_detects_ica_quota_exceeded(self):
        import services.sprint_manager.sprint_manager as sm
        is_rl, _ = sm._is_rate_limit_error("quota_exceeded: token limit reached")
        assert is_rl is True

    def test_sm_function_does_not_retry_400(self):
        import services.sprint_manager.sprint_manager as sm
        is_rl, _ = sm._is_rate_limit_error("HTTP 400 Bad Request")
        assert is_rl is False
