#!/usr/bin/env python3
"""PostToolUse hook — records token usage to the Commander dashboard.

Claude Code calls this after every tool use. The hook reads the JSON
payload from stdin, extracts usage.input_tokens / usage.output_tokens,
and POSTs them to the dashboard. It exits 0 and never blocks Claude if
the dashboard is unreachable or the payload has no usage data.

Claude Code PostToolUse payload shape (as of 2025):
  {
    "session_id": "...",
    "tool_name": "...",
    "tool_input": {...},
    "tool_response": {...},
    "cwd": "...",
    "usage": {
      "input_tokens": 123,
      "output_tokens": 45,
      "cache_read_input_tokens": 0,
      "cache_creation_input_tokens": 0
    }
  }

The hook tries the top-level "usage" key first, then falls back to
"tool_result.usage" to handle any variation in the payload structure.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path


def _extract_usage(payload: dict) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from a PostToolUse payload.

    Tries payload["usage"] first (standard Claude Code structure), then
    falls back to payload["tool_result"]["usage"] as an alternative path.
    Returns (0, 0) if neither path yields token data.
    """
    # Primary path: top-level "usage" key
    usage = payload.get("usage") or {}
    input_tokens  = int(usage.get("input_tokens",  0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)

    if input_tokens or output_tokens:
        return input_tokens, output_tokens

    # Fallback path: nested under "tool_result"
    tool_result = payload.get("tool_result") or {}
    if isinstance(tool_result, dict):
        usage2 = tool_result.get("usage") or {}
        input_tokens  = int(usage2.get("input_tokens",  0) or 0)
        output_tokens = int(usage2.get("output_tokens", 0) or 0)

    return input_tokens, output_tokens


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    input_tokens, output_tokens = _extract_usage(payload)

    # AC-4: log to stderr when discarding (does not block Claude)
    if not input_tokens and not output_tokens:
        print("[post_tool_used] skipped: no token data in payload", file=sys.stderr)
        sys.exit(0)

    session_id  = payload.get("session_id") or "unknown"
    working_dir = (
        payload.get("cwd")
        or os.environ.get("CLAUDE_CWD")
        or os.environ.get("PWD")
        or "unknown"
    )

    event = {
        "session_id":    session_id,
        "event_type":    "token_usage",
        "working_dir":   working_dir,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
    }

    try:
        # Derive the token-usage URL from HOOK_POST_TARGET (swap the path) or use the default.
        base = os.environ.get("HOOK_POST_TARGET", "http://localhost:8000/api/agent-event")
        target = base.replace("/api/agent-event", "/api/token-usage")
        data = json.dumps(event).encode()
        req  = urllib.request.Request(
            target,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass  # never block Claude if the dashboard is down


if __name__ == "__main__":
    main()
