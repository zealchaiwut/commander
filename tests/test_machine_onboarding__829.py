"""Tests for issue #829 — Add machine onboarding runbook to docs.

Each test is anchored to a specific acceptance criterion of the ticket and
validates the content of ``docs/machine-onboarding.md`` (and the quickstart
link to it). The runbook is a documentation artifact, so the contract is the
set of sections, the ordered step list, and the Failure Signatures table — all
of which are checked here.

AC coverage:
  AC1  — docs/machine-onboarding.md exists and covers every step in order
  AC2  — quickstart links to docs/machine-onboarding.md
  AC3  — clone layout section
  AC4  — venv per clone section with exact create/activate commands
  AC5  — claude install + setup-token section
  AC6  — gh auth + token section (interactive AND headless/token-based)
  AC7  — install_launchd.sh section showing the exact invocation with tokens
  AC8  — doctor run section with command and passing-output example
  AC9  — first sprint smoke test section
  AC10 — Failure Signatures table with the three required rows
  AC12 — every failure signature has a concrete, actionable fix step
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
RUNBOOK = REPO_ROOT / "docs" / "machine-onboarding.md"
QUICKSTART = REPO_ROOT / "docs" / "quickstart.md"


def _runbook_text() -> str:
    assert RUNBOOK.exists(), "docs/machine-onboarding.md must exist"
    return RUNBOOK.read_text(encoding="utf-8")


def _headings(text: str) -> list:
    """Return the ordered list of markdown section headings (## or ###)."""
    return [
        re.sub(r"^#+\s*", "", line).strip()
        for line in text.splitlines()
        if re.match(r"^#{2,3}\s+\S", line)
    ]


# The ordered steps the runbook must cover (AC1). Each entry is a
# case-insensitive substring that must appear in a heading, in this order.
ORDERED_STEPS = [
    "clone layout",
    "venv per clone",
    "claude install",          # "claude install + setup-token"
    "gh auth",                 # "gh auth + token"
    "install_launchd.sh",
    "doctor run",
    "first sprint smoke test",
    "failure signatures",
]


# ── AC1: file exists and covers every step in order ───────────────────────────

def test_runbook_exists():
    assert RUNBOOK.exists(), "docs/machine-onboarding.md must exist in the repo"


def test_runbook_covers_every_step_in_order():
    """Every documented step must appear as a heading, in the AC's order."""
    headings_blob = [h.lower() for h in _headings(_runbook_text())]

    positions = []
    for step in ORDERED_STEPS:
        idx = next(
            (i for i, h in enumerate(headings_blob) if step in h), None
        )
        assert idx is not None, (
            f"Runbook is missing a section heading for step: {step!r}"
        )
        positions.append(idx)

    assert positions == sorted(positions), (
        "Runbook sections must appear in the documented order: "
        f"{ORDERED_STEPS}. Got heading order indices {positions}."
    )


# ── AC2: quickstart links to the runbook ──────────────────────────────────────

def test_quickstart_links_to_runbook():
    text = QUICKSTART.read_text(encoding="utf-8")
    assert "machine-onboarding.md" in text, (
        "docs/quickstart.md must link to docs/machine-onboarding.md"
    )


# ── AC3: clone layout section ─────────────────────────────────────────────────

def test_clone_layout_section():
    text = _runbook_text().lower()
    assert "clone layout" in text
    # Must show the actual clone directory structure (prd/coder/tester clones).
    for clone in ("coder", "tester"):
        assert clone in text, (
            f"Clone layout must show the {clone!r} clone in the directory tree"
        )


# ── AC4: venv per clone section with exact commands ───────────────────────────

def test_venv_per_clone_section_has_create_and_activate_commands():
    text = _runbook_text()
    lower = text.lower()
    assert "venv per clone" in lower
    # Exact create command for the venv.
    assert re.search(r"python3\.12\s+-m\s+venv\s+venv", text), (
        "Venv section must show the exact create command "
        "`python3.12 -m venv venv`"
    )
    # Exact activate command.
    assert "source venv/bin/activate" in text, (
        "Venv section must show the exact activate command "
        "`source venv/bin/activate`"
    )


# ── AC5: claude install + setup-token section ─────────────────────────────────

def test_claude_install_and_setup_token_section():
    text = _runbook_text()
    lower = text.lower()
    assert "claude install" in lower or "claude install + setup-token" in lower
    # Exact install command for Claude Code.
    assert "@anthropic-ai/claude-code" in text, (
        "Claude section must show the exact npm install command for Claude Code"
    )
    # The setup-token invocation.
    assert "claude setup-token" in text, (
        "Claude section must show the `claude setup-token` invocation"
    )


# ── AC6: gh auth + token section (interactive AND headless) ───────────────────

def test_gh_auth_section_covers_interactive_and_headless():
    text = _runbook_text()
    lower = text.lower()
    assert "gh auth" in lower
    # Interactive path.
    assert "gh auth login" in text, (
        "gh section must document the interactive `gh auth login` path"
    )
    # Headless / token-based path.
    assert "GH_TOKEN" in text, (
        "gh section must document the headless GH_TOKEN-based path"
    )
    assert "headless" in lower, (
        "gh section must explicitly describe the headless (token-based) path"
    )


# ── AC7: install_launchd.sh section with exact token invocation ───────────────

def test_install_launchd_section_shows_token_invocation():
    text = _runbook_text()
    assert "install_launchd.sh" in text
    # Exact invocation passing the gh token argument.
    assert re.search(r"install_launchd\.sh\b[^\n]*--gh-token", text), (
        "install_launchd.sh section must show the exact invocation with the "
        "--gh-token argument"
    )
    # The claude OAuth token must also be documented for headless claude.
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text, (
        "install_launchd.sh section must document the CLAUDE_CODE_OAUTH_TOKEN "
        "headless token for claude"
    )


# ── AC8: doctor run section with command + passing output ─────────────────────

def test_doctor_run_section_shows_command_and_passing_output():
    text = _runbook_text()
    lower = text.lower()
    assert "doctor run" in lower
    # The command to run.
    assert "scripts/doctor.py" in text, (
        "Doctor section must show the `python scripts/doctor.py` command"
    )
    # A passing-output example: PASS lines and the all-passed summary.
    assert "[PASS]" in text, (
        "Doctor section must include a passing-output example with [PASS] lines"
    )
    assert re.search(r"All \d+ checks passed|host is ready", text), (
        "Doctor section must show what a passing run looks like "
        "(the all-checks-passed summary line)"
    )


# ── AC9: first sprint smoke test section ──────────────────────────────────────

def test_first_sprint_smoke_test_section():
    text = _runbook_text()
    lower = text.lower()
    assert "first sprint smoke test" in lower
    # The minimum command to verify the machine runs a sprint.
    assert "sprint_manager.py" in text, (
        "Smoke test section must show the sprint_manager.py command that "
        "verifies the machine can run a sprint"
    )


# ── AC10 + AC12: Failure Signatures table with required rows + fixes ───────────

REQUIRED_SIGNATURES = [
    "claude CLI not found",
    "Not logged in",
    "repo inaccessible",
]


def _failure_table_rows(text: str) -> list:
    """Return the data rows of the Failure Signatures table as raw strings."""
    lines = text.splitlines()
    # Find the Failure Signatures heading.
    start = next(
        (i for i, ln in enumerate(lines) if "failure signatures" in ln.lower()
         and ln.lstrip().startswith("#")),
        None,
    )
    assert start is not None, "Failure Signatures section heading not found"
    rows = []
    for ln in lines[start + 1:]:
        if ln.lstrip().startswith("#"):
            break  # next section
        stripped = ln.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Skip the header separator row (e.g. |---|---|).
            if all(set(c) <= set("-: ") for c in cells):
                continue
            rows.append(cells)
    return rows


def test_failure_signatures_table_has_required_rows():
    rows = _failure_table_rows(_runbook_text())
    assert rows, "Failure Signatures table has no rows"
    joined = ["  ".join(r) for r in rows]
    for sig in REQUIRED_SIGNATURES:
        assert any(sig in line for line in joined), (
            f"Failure Signatures table is missing a row for: {sig!r}"
        )


def test_every_failure_signature_has_a_concrete_fix():
    rows = _failure_table_rows(_runbook_text())
    # Header row is the first data-shaped row; treat all rows uniformly: each
    # row must have at least a signature cell and a non-empty fix cell.
    data_rows = [r for r in rows if not any(
        c.lower() in ("signature", "symptom", "cause") for c in r
    ) or len(r) >= 2]
    # Verify the three required signatures each carry a non-empty fix cell.
    for sig in REQUIRED_SIGNATURES:
        match = next(
            (r for r in data_rows if any(sig in c for c in r)), None
        )
        assert match is not None, f"No table row for signature {sig!r}"
        # The fix is the last cell; it must be non-trivial (actionable).
        fix_cell = match[-1]
        assert len(fix_cell) >= 10, (
            f"Failure signature {sig!r} must have a concrete, actionable fix "
            f"step; got an empty/too-short fix cell: {fix_cell!r}"
        )
