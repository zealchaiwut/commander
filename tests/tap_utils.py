"""Shared TAP output parsing utilities for Commander test files."""
from __future__ import annotations

import re


def _parse_tap_fail_count(output: str) -> int:
    """Return the failure count from Node's TAP '# fail N' summary line, or 0 if absent."""
    m = re.search(r"# fail\s+(\d+)", output)
    return int(m.group(1)) if m else 0
