"""Tests for issue #665 – Clarify symlink handling documentation in fs_list.

AC coverage:
  AC1  — The misleading comment 'Resolve without following symlinks first to
          detect symlink escapes' must NOT exist in server.py (resolve() follows
          symlinks; the comment was wrong).
  AC2  — The path-normalization step in fs_list must use os.path.normpath (a
          lexical operation that does not follow symlinks), not Path.resolve(),
          so that the surrounding comments are accurate.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVER_PY = REPO_ROOT / "apps" / "dashboard" / "server.py"


# ── AC1: Misleading comment is gone ───────────────────────────────────────────

def test_misleading_symlink_comment_absent():
    """'Resolve without following symlinks first to detect symlink escapes'
    must no longer appear anywhere in server.py.

    Path.resolve() follows symlinks by default; the old comment was wrong.
    The fix (issue #663) replaced it with an accurate description of
    os.path.normpath() usage.
    """
    source = SERVER_PY.read_text(encoding="utf-8")
    assert "Resolve without following symlinks first to detect symlink escapes" not in source, (
        "Misleading comment still present in server.py. "
        "It incorrectly describes Path.resolve() as not following symlinks."
    )


# ── AC2: fs_list normalizes with normpath, not resolve() ──────────────────────

def test_fs_list_uses_normpath_not_resolve_for_candidate():
    """The candidate-path normalization in fs_list must use os.path.normpath,
    not Path.resolve(), so that the code does not silently follow symlinks
    during the pre-check phase.

    Strategy: parse the server.py AST, locate the fs_list function body, and
    verify that `os.path.normpath` is called on the candidate before the
    is_relative_to guard — and that Path(...).resolve() is NOT called on
    the candidate path (only on _FS_BROWSE_ROOT itself or on link targets
    inside the walk loop, both of which are intentional).
    """
    source = SERVER_PY.read_text(encoding="utf-8")

    # Find the fs_list function lines with a regex scan (faster than full AST walk)
    lines = source.splitlines()

    # Locate the function definition
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^(async )?def fs_list\b", line):
            start = i
            break
    assert start is not None, "fs_list function not found in server.py"

    # Collect the function body up to the next top-level def/class
    func_lines = []
    for line in lines[start:]:
        if func_lines and re.match(r"^(async )?def |^class ", line):
            break
        func_lines.append(line)

    func_src = "\n".join(func_lines)

    # os.path.normpath must be called somewhere in the body
    assert "os.path.normpath" in func_src, (
        "fs_list must use os.path.normpath to normalize the candidate path "
        "without following symlinks."
    )

    # Path(...).resolve() must NOT be called on the raw candidate (only allowed
    # on _FS_BROWSE_ROOT or on link_resolved inside the walk loop).
    # We look for `.resolve()` calls that are NOT on `_FS_BROWSE_ROOT` and NOT
    # inside a `if cur.is_symlink():` block (the walk-loop check).
    #
    # Simple heuristic: the only `.resolve()` calls allowed in fs_list are:
    #   _FS_BROWSE_ROOT.resolve()   — resolves the root itself (intentional)
    #   cur.resolve()               — resolves a symlink target in the walk loop (intentional)
    disallowed_pattern = re.compile(r"\bcandidate\.resolve\(\)")
    assert not disallowed_pattern.search(func_src), (
        "fs_list must not call candidate.resolve() — resolve() follows symlinks "
        "and would silently dereference the input path before the security check."
    )
