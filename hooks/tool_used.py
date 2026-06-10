#!/usr/bin/env python3
"""PreToolUse hook — reports tool activity to the Commander dashboard."""
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path


def _detect_git_info(cwd: str) -> tuple[str, str]:
    """Return (repo_name, branch). Both empty string on failure."""
    repo = branch = ""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
        m = re.search(r"/([^/]+?)(?:\.git)?\s*$", url)
        if m:
            repo = m.group(1)
    except Exception:
        pass
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, cwd=cwd,
        ).stdout.strip()
    except Exception:
        pass
    return repo, branch


def _compose_name(session_id: str, working_dir: str) -> str:
    role   = os.environ.get("CLAUDE_AGENT_ROLE", "agent")
    repo, branch = _detect_git_info(working_dir)
    label  = repo or Path(working_dir).name or "unknown"
    short  = session_id[:6] if len(session_id) >= 6 else session_id
    name   = f"{role}·{label}·{branch}·#{short}"
    issue  = os.environ.get("CLAUDE_AGENT_ISSUE", "").strip()
    if issue:
        name += f"·issue-{issue}"
    return name


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    session_id  = payload.get("session_id") or "unknown"
    tool_name   = payload.get("tool_name") or "unknown"
    working_dir = (
        payload.get("cwd")
        or os.environ.get("CLAUDE_CWD")
        or os.environ.get("PWD")
        or "unknown"
    )

    event = {
        "session_id":  session_id,
        "name":        _compose_name(session_id, working_dir),
        "event_type":  "tool_use",
        "working_dir": working_dir,
        "tool_name":   tool_name,
        "status":      "working",
    }

    try:
        target = os.environ.get(
            "HOOK_POST_TARGET", "http://localhost:8000/api/agent-event"
        )
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
