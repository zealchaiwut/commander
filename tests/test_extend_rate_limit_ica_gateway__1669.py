"""Tests for issue #1669: Extend rate-limit detection to handle ICA/IBM gateway error formats

Acceptance Criteria coverage:
  AC1: _is_rate_limit_error recognises ICA/IBM gateway 429 responses and quota-exceeded payloads
  AC2: ICA-matched errors follow the exact same retry and exponential-backoff code path
  AC3: Unit tests cover ICA 429, Anthropic shape (regression), and non-retryable 4xx
  AC4: No behavioural change on the non-ICA (standard Anthropic) path
  AC5: All new and existing tests pass in CI

Tests verify that is_retryable_rate_limit (used by sprint_manager._is_rate_limit_error)
correctly identifies both Anthropic and ICA error formats and returns the same tuple shape.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.api_client import is_retryable_rate_limit


# AC1: ICA/IBM gateway 429 responses with quota-exceeded payloads
def test_ica_quota_exceeded_json_format():
    """AC1: ICA 429 with IBM quota-exceeded JSON body is detected as retryable."""
    output = (
        "HTTP/1.1 429 Too Many Requests\n"
        '{"error": "quota_exceeded", "message": "User token quota has been exceeded"}\n'
    )
    is_rl, retry_after = is_retryable_rate_limit(output)
    assert is_rl is True, "ICA quota_exceeded JSON should be detected"
    assert retry_after is None or isinstance(retry_after, int)


def test_ica_quota_exceeded_human_readable():
    """AC1: ICA 429 with human-readable quota-exceeded message is detected."""
    output = "Error from ICA gateway: quota exceeded — daily limit reached.\n"
    is_rl, retry_after = is_retryable_rate_limit(output)
    assert is_rl is True, "ICA 'quota exceeded' human-readable format should be detected"


def test_ica_token_limit_exceeded():
    """AC1: ICA token_limit_exceeded format is detected as retryable."""
    output = (
        "Status: 429\n"
        "token_limit_exceeded: You have consumed all available tokens for today.\n"
    )
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "ICA token_limit_exceeded should be detected"


def test_ica_usage_limit_exceeded():
    """AC1: ICA usage_limit_exceeded format is detected as retryable."""
    output = "IBM Cloud API error: usage_limit_exceeded\n"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "ICA usage_limit_exceeded should be detected"


def test_ica_usage_limit_exceeded_human():
    """AC1: ICA usage limit exceeded human-readable format is detected."""
    output = "gateway returned: usage limit exceeded for this API key\n"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "ICA 'usage limit exceeded' human format should be detected"


def test_ica_generic_429_status():
    """AC1: ICA 429 status code alone triggers retry (generic rate limit)."""
    output = "ICA proxy: received HTTP 429 from upstream\n"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "ICA 429 status should be detected"


# AC2: ICA and Anthropic both return the same (bool, Optional[int]) tuple
def test_ica_and_anthropic_return_same_tuple_shape():
    """AC2: Both ICA and Anthropic detections return identical tuple shape (bool, int|None)."""
    ica_result = is_retryable_rate_limit("quota_exceeded")
    anthropic_result = is_retryable_rate_limit("subscription rate limit exceeded")

    assert isinstance(ica_result, tuple) and len(ica_result) == 2
    assert isinstance(anthropic_result, tuple) and len(anthropic_result) == 2
    assert type(ica_result[0]) is type(anthropic_result[0])  # both bool
    assert ica_result[1] is None or isinstance(ica_result[1], int)
    assert anthropic_result[1] is None or isinstance(anthropic_result[1], int)


def test_ica_quota_returns_true_bool():
    """AC2: ICA quota detection returns True (bool), not a string or other type."""
    is_rl, _ = is_retryable_rate_limit("quota_exceeded: token limit")
    assert is_rl is True
    assert type(is_rl) is bool


def test_no_shared_branch_split_both_use_same_return():
    """AC2: No separate branching — both ICA and Anthropic return to same retry logic."""
    ica_output = "quota_exceeded\nRetry-After: 45"
    anthropic_output = "subscription rate limit exceeded\nRetry-After: 45"

    ica_is_rl, ica_retry = is_retryable_rate_limit(ica_output)
    anth_is_rl, anth_retry = is_retryable_rate_limit(anthropic_output)

    assert ica_is_rl is True and anth_is_rl is True
    assert ica_retry == 45 and anth_retry == 45


# AC3: Unit tests covering ICA, Anthropic regression, and non-retryable 4xx
def test_anthropic_subscription_rate_limit_regression():
    """AC3: Existing Anthropic OAuth shape still detected (regression)."""
    output = "claude.ai: subscription rate limit exceeded — please wait"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "Anthropic subscription rate limit should still be detected"


def test_anthropic_rate_limit_underscore_regression():
    """AC3: Anthropic rate_limit (with underscore) still detected (regression)."""
    output = 'API error {"type": "rate_limit", "message": "Rate limited"}'
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "Anthropic rate_limit (underscore) should be detected"


def test_anthropic_429_bare_regression():
    """AC3: Anthropic plain 429 status still detected (regression)."""
    output = "HTTP status 429 returned"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True, "Anthropic bare 429 should be detected"


def test_non_retryable_400_bad_request():
    """AC3: Non-retryable 400 Bad Request must NOT trigger retry."""
    output = "HTTP 400 Bad Request: invalid_request_error — prompt too long"
    is_rl, retry_after = is_retryable_rate_limit(output)
    assert is_rl is False, "400 Bad Request should not be retried"
    assert retry_after is None


def test_non_retryable_401_unauthorized():
    """AC3: Non-retryable 401 Unauthorized must NOT trigger retry."""
    output = "HTTP 401 Unauthorized: invalid api key"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is False, "401 Unauthorized should not be retried"


def test_non_retryable_403_forbidden():
    """AC3: Non-retryable 403 Forbidden must NOT trigger retry."""
    output = "403 Forbidden: your account does not have access"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is False, "403 Forbidden should not be retried"


def test_non_retryable_500_server_error():
    """AC3: Non-retryable 500 Server Error must NOT trigger retry."""
    output = "HTTP 500 Internal Server Error"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is False, "500 Server Error should not be retried"


def test_non_retryable_empty_output():
    """AC3: Empty output (success case) is not retryable."""
    is_rl, retry_after = is_retryable_rate_limit("")
    assert is_rl is False
    assert retry_after is None


# AC4: No behavioural change on the non-ICA (standard Anthropic) path
def test_anthropic_too_many_requests_phrase():
    """AC4: Anthropic 'Too Many Requests' phrase still works."""
    output = "Response: 429 Too Many Requests"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True


def test_anthropic_rate_limit_phrase_case_insensitive():
    """AC4: Anthropic 'rate limit' is detected case-insensitively."""
    output = "Error: RATE LIMIT hit"
    is_rl, _ = is_retryable_rate_limit(output)
    assert is_rl is True


def test_sprint_manager_delegates_to_api_client():
    """AC4: sprint_manager._is_rate_limit_error delegates to api_client unchanged."""
    import services.sprint_manager.sprint_manager as sm

    # Both should return the same result for the same input
    input_str = "quota_exceeded: daily limit"
    sm_result = sm._is_rate_limit_error(input_str)
    api_client_result = is_retryable_rate_limit(input_str)

    assert sm_result == api_client_result, "sprint_manager should delegate to api_client"


# AC5: All new and existing tests pass
def test_retry_after_parsing_with_ica_format():
    """AC5: Retry-After header is parsed even with ICA quota errors."""
    output = "quota_exceeded\nRetry-After: 30\n"
    is_rl, retry_after = is_retryable_rate_limit(output)
    assert is_rl is True
    assert retry_after == 30


def test_retry_after_parsing_with_anthropic_format():
    """AC5: Retry-After parsing works with Anthropic format too."""
    output = "subscription rate limit exceeded\nretry after: 120 seconds"
    is_rl, retry_after = is_retryable_rate_limit(output)
    assert is_rl is True
    assert retry_after == 120
