"""
Tests for issue #12 — docs/tutorial.md concise workflow guide.

Per the issue note for the tester, this is a documentation-only ticket.
Tests verify file existence, line count, required sections, and AC content.
"""

import pathlib
import re

# Resolve the tutorial file relative to the repo root.
# The feature branch may be checked out in work-tester worktree rather than
# the main worktree, so we probe both locations.
_MAIN_ROOT = pathlib.Path(__file__).resolve().parent.parent
_WORKTREE_ROOT = _MAIN_ROOT / "work-tester"

def _find_tutorial() -> pathlib.Path:
    for root in (_MAIN_ROOT, _WORKTREE_ROOT):
        candidate = root / "docs" / "tutorial.md"
        if candidate.exists():
            return candidate
    # Return the main-worktree path so the exist-check test gives a clear error
    return _MAIN_ROOT / "docs" / "tutorial.md"

TUTORIAL = _find_tutorial()


def read_tutorial():
    return TUTORIAL.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC 1: docs/tutorial.md exists with 5 sections and 200–300 lines
# ---------------------------------------------------------------------------

def test_tutorial_file_exists():
    assert TUTORIAL.exists(), f"docs/tutorial.md not found at {TUTORIAL}"


def test_tutorial_line_count_in_range():
    lines = TUTORIAL.read_text(encoding="utf-8").splitlines()
    assert 200 <= len(lines) <= 300, (
        f"Expected 200–300 lines, got {len(lines)}"
    )


def test_tutorial_has_five_sections():
    text = read_tutorial()
    # Each section starts with "## <number>."
    sections = re.findall(r"^## \d+\.", text, re.MULTILINE)
    assert len(sections) == 5, (
        f"Expected 5 top-level sections (## 1. … ## 5.), found {len(sections)}: {sections}"
    )


# ---------------------------------------------------------------------------
# AC 2: Section 1 contains a flow diagram
# ---------------------------------------------------------------------------

def test_section1_contains_flow_diagram():
    text = read_tutorial()
    # The diagram reference can be an image link or inline Excalidraw reference
    has_diagram = (
        "excalidraw" in text.lower()
        or re.search(r"!\[.*?\]\(.*?\)", text) is not None
    )
    assert has_diagram, (
        "Section 1 should contain a flow diagram (excalidraw reference or image link)"
    )


def test_section1_mentions_ba_coder_tester_uat_loop():
    text = read_tutorial()
    # Verify the core loop is described
    for keyword in ("BA", "Coder", "Tester", "UAT"):
        assert keyword in text, f"Section 1 should mention '{keyword}'"


# ---------------------------------------------------------------------------
# AC 3: Section 3 has working commands for starting PRD and UAT servers
# ---------------------------------------------------------------------------

def test_section3_has_prd_start_command():
    text = read_tutorial()
    assert "start_prd.sh" in text, (
        "Section 3 should include a command referencing start_prd.sh"
    )


def test_section3_has_uat_start_command():
    text = read_tutorial()
    assert "start_uat.sh" in text, (
        "Section 3 should include a command referencing start_uat.sh"
    )


def test_section3_mentions_port_8000_and_8001():
    text = read_tutorial()
    assert "8000" in text, "PRD port 8000 should be mentioned"
    assert "8001" in text, "UAT port 8001 should be mentioned"


# ---------------------------------------------------------------------------
# AC 4: Section 4 walks a complete example end-to-end with actual commands
# ---------------------------------------------------------------------------

def test_section4_exists_with_end_to_end_example():
    text = read_tutorial()
    assert "## 4." in text, "Section 4 header should exist"


def test_section4_has_ba_command():
    text = read_tutorial()
    assert "/ba" in text, "Section 4 should show the /ba command"


def test_section4_has_coder_command():
    text = read_tutorial()
    assert "/coder" in text, "Section 4 should show the /coder command"


def test_section4_has_tester_command():
    text = read_tutorial()
    assert "/tester" in text, "Section 4 should show the /tester command"


def test_section4_has_release_step():
    text = read_tutorial()
    # Release step should mention merging develop to master
    assert "master" in text, "Section 4 should reference the master branch in release step"
    assert "develop" in text, "Section 4 should reference develop in merge step"


# ---------------------------------------------------------------------------
# AC 5: Section 5 has exactly 5 best practices with WHY explanations
# ---------------------------------------------------------------------------

def test_section5_has_exactly_five_best_practices():
    text = read_tutorial()
    # Isolate section 5 text first, then count ### subsections within it
    match = re.search(r"## 5\.(.*?)(?=^## \d+\.|\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, "Section 5 not found"
    section5 = match.group(1)
    practices = re.findall(r"^### [a-z]\.", section5, re.MULTILINE)
    assert len(practices) == 5, (
        f"Expected exactly 5 best-practice subsections in section 5, found {len(practices)}: {practices}"
    )


def test_section5_each_practice_has_explanation():
    text = read_tutorial()
    # Locate section 5 content
    match = re.search(r"## 5\.(.*?)(?=^## \d+\.|\Z)", text, re.DOTALL | re.MULTILINE)
    assert match, "Section 5 not found"
    section5 = match.group(1)
    # Each subsection (### a–e) should have non-empty body text (>50 chars after heading)
    subsections = re.split(r"^### [a-e]\.", section5, flags=re.MULTILINE)
    # subsections[0] is text before first ### heading; [1]–[5] are the practices
    practice_bodies = [s.strip() for s in subsections[1:]]
    for i, body in enumerate(practice_bodies, start=1):
        assert len(body) > 50, (
            f"Best practice {chr(96+i)} looks too short — needs a WHY explanation"
        )


# ---------------------------------------------------------------------------
# AC 6: All shell commands are in code blocks (copy-pasteable)
# ---------------------------------------------------------------------------

def test_shell_commands_in_code_blocks():
    text = read_tutorial()
    # All bash code blocks should be present
    code_blocks = re.findall(r"```(?:bash|sh|json)?(.*?)```", text, re.DOTALL)
    assert len(code_blocks) >= 6, (
        f"Expected at least 6 code blocks covering setup, PRD/UAT, workflow steps; found {len(code_blocks)}"
    )
