#!/usr/bin/env python3
"""PostToolUse hook — records token usage to the Commander dashboard.

Claude Code calls this after every tool use. The hook reads the JSON
payload from stdin, extracts usage.input_tokens / usage.output_tokens,
and POSTs them to the dashboard. It exits 0 and never blocks Claude if
the dashboard is unreachable or the payload has no usage data.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    usage = payload.get("usage") or {}
    input_tokens  = usage.get("input_tokens",  0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0

    # Silently skip when no token data
    if not input_tokens and not output_tokens:
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
