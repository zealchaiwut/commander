"""Rate-limit and retryable-error detection for Claude API / ICA gateway responses.

Extracted from sprint_manager.py (issue #1669) so both Anthropic OAuth and
ICA/IBM gateway 429 / quota-exceeded shapes are detected in one place and
funnel into the same retry/backoff code path.
"""
from __future__ import annotations

import re
from typing import Optional

# Signals that appear in agent log output when a rate-limit or quota-exceeded
# error is returned.  Covers both Anthropic OAuth subscription paths and
# ICA/IBM Cloud API gateway paths — all detected errors follow the same
# retry/backoff logic with no provider-specific branching in callers.
_RATE_LIMIT_SIGNALS: tuple[str, ...] = (
    # Anthropic OAuth subscription rate-limit (existing)
    "429",
    "rate limit",
    "too many requests",
    "subscription rate limit",
    "rate_limit",
    # ICA / IBM Cloud API gateway quota-exceeded formats (issue #1669)
    "quota_exceeded",
    "quota exceeded",
    "token_limit_exceeded",
    "usage_limit_exceeded",
    "usage limit exceeded",
)


def is_retryable_rate_limit(output: str) -> tuple[bool, Optional[int]]:
    """Return (is_rate_limit, retry_after_secs) by inspecting subprocess output.

    Recognises both Anthropic OAuth subscription rate-limit shapes and
    ICA/IBM gateway 429 / quota-exceeded payloads.  Both provider paths
    return the same (True, retry_after) tuple so callers use identical
    retry/backoff logic regardless of which gateway produced the error.

    Args:
        output: Combined stdout+stderr text from a Claude Code subprocess.

    Returns:
        (True, retry_after) when a retryable rate-limit / quota error is
        detected.  retry_after is the integer from a Retry-After header/field,
        or None when no such value is present.
        (False, None) when the output contains no rate-limit signal.
    """
    lower = output.lower()
    if not any(sig in lower for sig in _RATE_LIMIT_SIGNALS):
        return False, None
    m = re.search(r"retry.?after[:\s]+(\d+)", output, re.IGNORECASE)
    retry_after = int(m.group(1)) if m else None
    return True, retry_after
