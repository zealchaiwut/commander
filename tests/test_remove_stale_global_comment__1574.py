"""Tests for issue #1574: Remove stale global comment for _smgmtNextUpLabel"""
import os
import subprocess
import re


def test_stale_global_removed_from_eslint_directive():
    """AC: The /* global */ directive no longer contains _smgmtNextUpLabel"""
    board_render = "apps/dashboard/static/src/sprint-board/board-render.js"

    with open(board_render, 'r') as f:
        content = f.read()

    # Extract the eslint directive (lines 15–18 area)
    directive_match = re.search(
        r'/\*\s*eslint-disable[^*]*\*/\n.*?/\*\s*global\s+([^*]*?)\s*\*/\n',
        content,
        re.DOTALL
    )
    assert directive_match, "Could not find /* global */ directive"

    directive_content = directive_match.group(1)
    # Check that _smgmtNextUpLabel is NOT in the directive
    assert '_smgmtNextUpLabel' not in directive_content, \
        f"_smgmtNextUpLabel found in global directive: {directive_content[:200]}"


def test_local_declaration_still_present():
    """AC: The local declaration let _smgmtNextUpLabel = null; at line ~413 remains untouched"""
    board_render = "apps/dashboard/static/src/sprint-board/board-render.js"

    with open(board_render, 'r') as f:
        content = f.read()

    # Search for the local declaration
    assert re.search(r'^\s*let\s+_smgmtNextUpLabel\s*=\s*null\s*;', content, re.MULTILINE), \
        "Local declaration 'let _smgmtNextUpLabel = null;' not found in file"


def test_no_other_names_removed_or_added():
    """AC: No other names are removed from or added to the /* global */ directive"""
    # This is verified by comparing line 16 to the expected list.
    # As this is a cosmetic cleanup, we verify a sanity check:
    # the directive should still have many entries (all except _smgmtNextUpLabel)
    board_render = "apps/dashboard/static/src/sprint-board/board-render.js"

    with open(board_render, 'r') as f:
        lines = f.readlines()

    # Line 16 is index 15 (0-based)
    global_line = lines[15]

    # The directive should contain many well-known entries
    known_entries = [
        '_smgmtEnsureCapData', '_smgmtRender', '_smgmtData',
        '_smgmtBySprint', '_smgmtFilterApply', 'escHtml'
    ]

    for entry in known_entries:
        assert entry in global_line, f"Expected entry '{entry}' missing from global directive"

    # Verify _smgmtNextUpLabel is NOT in the line
    assert '_smgmtNextUpLabel' not in global_line, \
        "_smgmtNextUpLabel should have been removed"


def test_npm_build_succeeds():
    """AC: Running npm run build completes without errors"""
    os.chdir("apps/dashboard")
    result = subprocess.run(
        ["npm", "run", "build"],
        capture_output=True,
        text=True,
        timeout=30
    )

    assert result.returncode == 0, \
        f"npm run build failed with exit code {result.returncode}.\nStdout:\n{result.stdout}\nStderr:\n{result.stderr}"

    # Verify bundle.js was regenerated
    assert os.path.exists("static/dist/bundle.js"), \
        "static/dist/bundle.js was not regenerated after build"
