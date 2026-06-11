#!/usr/bin/env python3
"""PostToolUse hook — records token usage to the Commander dashboard.

Claude Code calls this after every tool use. The hook reads the JSON
payload from stdin (which includes transcript_path), then reads the
most recent assistant message from the JSONL transcript to get real
token counts.

NOTE: Claude Code does NOT include token usage fields in the PostToolUse
hook payload itself. Token data lives in the JSONL transcript file at
transcript_path, in entries with type="assistant" under message.usage.

Claude Code PostToolUse payload shape:
  {
    "session_id": "...",
    "transcript_path": "/path/to/session.jsonl",
    "hook_event_name": "PostToolUse",
    "tool_name": "...",
    "tool_input": {...},
    "tool_result": {...},
    "cwd": "..."
  }

JSONL transcript assistant entry (message.usage fields):
  {
    "type": "assistant",
    "sessionId": "...",
    "cwd": "...",
    "timestamp": "...",
    "message": {
      "usage": {
        "input_tokens": 123,
        "output_tokens": 45,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0
      }
    }
  }

The hook reads the last 50 lines of the transcript to find the most
recent assistant usage entry and POSTs those counts to the dashboard.
It exits 0 and never blocks Claude if the dashboard is unreachable.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path


def _read_last_usage_from_transcript(transcript_path: str) -> tuple[int, int]:
    """Read the most recent token usage from a JSONL transcript file.

    Scans the transcript file from the end, finds the last assistant
    entry with a non-zero usage block, and returns (input_tokens, output_tokens).
    Returns (0, 0) if no usable entry is found.
    """
    try:
        path = Path(transcript_path)
        if not path.exists():
            return 0, 0

        # Read the last 200 lines (large enough to catch recent assistant messages)
        lines = path.read_text(encoding="utf-8").splitlines()
        # Walk backwards to find the most recent assistant entry with usage
        for line in reversed(lines[-200:]):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "assistant":
                continue

            usage = entry.get("message", {}).get("usage", {})
            if not usage:
                continue

            input_tokens  = int(usage.get("input_tokens",  0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)

            # Also count cache_read as effective input (they're tokens processed)
            cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
            total_input = input_tokens + cache_read

            if total_input or output_tokens:
                return total_input, output_tokens

    except Exception as exc:
        print(f"[post_tool_used] error reading transcript: {exc}", file=sys.stderr)

    return 0, 0


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    transcript_path = payload.get("transcript_path") or ""
    input_tokens, output_tokens = _read_last_usage_from_transcript(transcript_path)

    # AC-4: log to stderr when discarding (does not block Claude)
    if not input_tokens and not output_tokens:
        print("[post_tool_used] skipped: no token data found in transcript", file=sys.stderr)
        sys.exit(0)

    session_id  = payload.get("session_id") or "unknown"
    working_dir = (
        payload.get("cwd")
        or os.environ.get("CLAUDE_CWD")
        or os.environ.get("PWD")
        or "unknown"
    )

    # AC-4: capture agent role and model name for per-agent, per-model tracking
    # CLAUDE_AGENT_ROLE is set by the launcher (e.g. ba, coder, tester)
    # CLAUDE_MODEL is set by Claude Code when a model is configured
    agent_role = os.environ.get("CLAUDE_AGENT_ROLE") or None
    model_name = os.environ.get("CLAUDE_MODEL") or None
    # COMMANDER_PROJECT (owner/repo) is set by the sprint dispatcher; without
    # it the server falls back to the working dir's basename ("uat", "coder"),
    # which can't split cost per project once several projects share a machine.
    project = os.environ.get("COMMANDER_PROJECT") or None

    event = {
        "session_id":    session_id,
        "event_type":    "token_usage",
        "working_dir":   working_dir,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "agent_role":    agent_role,
        "model_name":    model_name,
        "project":       project,
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
