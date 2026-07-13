"""Service logic for the agent operate guide endpoint (issue #1861).

Public API:
  GUIDE_PATH — Path to the guide file; override in tests via monkeypatching
  load_guide() → dict   raises HTTPException(404) if the file is missing
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import HTTPException

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Exposed at module level so tests can monkeypatch it.
GUIDE_PATH: Path = _REPO_ROOT / "docs" / "agent-guide.md"


def load_guide() -> dict:
    """Read docs/agent-guide.md and return {content, version}.

    version is a SHA-256 content fingerprint (16 hex chars) — stable for
    unchanged content, changes whenever the file is edited.
    """
    if not GUIDE_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Agent guide not found. "
                "Ensure docs/agent-guide.md exists in the repository root."
            ),
        )
    content = GUIDE_PATH.read_text(encoding="utf-8")
    version = hashlib.sha256(content.encode()).hexdigest()[:16]
    return {"content": content, "version": version}
