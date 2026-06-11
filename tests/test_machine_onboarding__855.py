"""Tests for issue #855 — Add machine onboarding runbook to docs.

Issue #855 restates the deliverable of #829 (the runbook ``docs/machine-onboarding.md``
already exists from that ticket). Rather than rewrite a UAT'd, working document,
this suite anchors #855's acceptance criteria to the existing runbook and adds
the coverage #829's test did not assert explicitly:

  * the Failure Signatures table is a three-column structure with a distinct,
    non-empty **root cause** cell per row (AC4 of #855 requires error text,
    root cause, AND remediation — three separate things per row); and
  * commands carry **placeholder annotations** for per-machine values such as
    token values and repo paths (AC6 of #855).

AC coverage (issue #855):
  AC1  — docs/machine-onboarding.md exists and is linked from the quickstart
  AC2  — runbook covers, in order: clone layout, venv per clone, claude install
         + setup-token, gh auth + token, install_launchd.sh with token args,
         doctor run, first-sprint smoke test
  AC3  — Failure Signatures table with the three required rows
  AC4  — each failure row carries exact error text, a root cause, and a fix
  AC6  — commands are copy-pasteable and use placeholder annotations for the
         values that vary per machine (token values, repo paths)

AC5 ("a person with no prior knowledge can follow it without help") is a
human-judged UAT criterion and is exercised by the UAT steps, not asserted here.
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


# The ordered steps AC2 requires, as case-insensitive substrings that must each
# appear in a heading, in this exact order.
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


# ── AC1: runbook exists and is linked from the quickstart ─────────────────────

def test_runbook_exists():
    assert RUNBOOK.exists(), "docs/machine-onboarding.md must exist in the repo"


def test_quickstart_links_to_runbook():
    text = QUICKSTART.read_text(encoding="utf-8")
    assert "machine-onboarding.md" in text, (
        "docs/quickstart.md must link to docs/machine-onboarding.md"
    )


# ── AC2: covers the required steps, in order ──────────────────────────────────

def test_runbook_covers_required_steps_in_order():
    headings_blob = [h.lower() for h in _headings(_runbook_text())]

    positions = []
    for step in ORDERED_STEPS:
        idx = next((i for i, h in enumerate(headings_blob) if step in h), None)
        assert idx is not None, (
            f"Runbook is missing a section heading for step: {step!r}"
        )
        positions.append(idx)

    assert positions == sorted(positions), (
        "Runbook sections must appear in the order required by AC2: "
        f"{ORDERED_STEPS}. Got heading order indices {positions}."
    )


def test_runbook_documents_the_required_commands():
    """Each ordered step must carry its concrete, named command/arg (AC2)."""
    text = _runbook_text()
    # venv create + activate
    assert re.search(r"python3\.12\s+-m\s+venv\s+venv", text)
    assert "source venv/bin/activate" in text
    # claude install + setup-token
    assert "@anthropic-ai/claude-code" in text
    assert "claude setup-token" in text
    # gh auth (interactive + headless token)
    assert "gh auth login" in text
    assert "GH_TOKEN" in text
    # install_launchd.sh with the token args
    assert re.search(r"install_launchd\.sh\b[^\n]*--gh-token", text)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text
    # doctor run
    assert "scripts/doctor.py" in text
    # first-sprint smoke test
    assert "sprint_manager.py" in text


# ── AC3 + AC4: Failure Signatures table — required rows, three columns ─────────

REQUIRED_SIGNATURES = [
    "claude CLI not found",
    "Not logged in",
    "repo inaccessible",
]


def _failure_table_rows(text: str) -> list:
    """Return the data rows of the Failure Signatures table (header + body)."""
    lines = text.splitlines()
    start = next(
        (i for i, ln in enumerate(lines)
         if "failure signatures" in ln.lower() and ln.lstrip().startswith("#")),
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
            if all(set(c) <= set("-: ") for c in cells):
                continue  # skip the |---|---| separator
            rows.append(cells)
    return rows


def test_failure_table_has_required_rows():
    rows = _failure_table_rows(_runbook_text())
    assert rows, "Failure Signatures table has no rows"
    joined = ["  ".join(r) for r in rows]
    for sig in REQUIRED_SIGNATURES:
        assert any(sig in line for line in joined), (
            f"Failure Signatures table is missing a row for: {sig!r}"
        )


def test_failure_table_is_three_columns_error_cause_fix():
    """AC4: each row must carry error text, a distinct root cause, and a fix.

    That is three separate, non-empty cells — not a two-column
    signature/fix table that folds cause into the remediation.
    """
    rows = _failure_table_rows(_runbook_text())
    assert rows, "Failure Signatures table has no rows"

    # Header row defines the columns; it must name a cause/root-cause column
    # between the signature and the fix.
    header = [c.lower() for c in rows[0]]
    assert len(header) >= 3, (
        "Failure Signatures table must have at least three columns "
        f"(signature, root cause, fix); header was {rows[0]!r}"
    )
    assert any("cause" in c for c in header), (
        "Failure Signatures table must have a dedicated root-cause column; "
        f"header was {rows[0]!r}"
    )

    data_rows = rows[1:]
    for sig in REQUIRED_SIGNATURES:
        match = next((r for r in data_rows if any(sig in c for c in r)), None)
        assert match is not None, f"No data row for signature {sig!r}"
        assert len(match) >= 3, (
            f"Row for {sig!r} must have signature, cause, and fix cells; "
            f"got {match!r}"
        )
        # The cause cell (middle) and the fix cell (last) must each be a
        # non-trivial, actionable sentence.
        cause_cell, fix_cell = match[1], match[-1]
        assert len(cause_cell) >= 10, (
            f"Signature {sig!r} must state a concrete root cause; "
            f"got {cause_cell!r}"
        )
        assert len(fix_cell) >= 10, (
            f"Signature {sig!r} must carry a concrete, actionable fix; "
            f"got {fix_cell!r}"
        )


# ── AC6: copy-pasteable commands with per-machine placeholder annotations ─────

def test_commands_use_placeholder_annotations_for_per_machine_values():
    """Values that vary per machine (tokens, repo paths) must be shown as
    placeholders, not as a single operator's real secrets/paths."""
    text = _runbook_text()
    # A gh token placeholder pattern (e.g. ghp_xxxxxxxx or <gh-token>).
    assert re.search(r"ghp_x{4,}", text) or re.search(r"<[^>]*gh[^>]*token[^>]*>",
                                                       text, re.IGNORECASE), (
        "Runbook must annotate the gh token as a placeholder (e.g. "
        "`ghp_xxxxxxxxxxxxxxxx` or `<gh-token>`)"
    )
    # A claude OAuth token placeholder (angle-bracket annotation).
    assert re.search(r"<[^>]*token[^>]*>", text), (
        "Runbook must annotate the claude OAuth token as a placeholder "
        "(e.g. `<token-from-claude-setup-token>`)"
    )


def test_commands_are_inside_copy_pasteable_code_fences():
    """AC6: the documented commands must live in fenced code blocks so they are
    copy-pasteable, not inlined into prose only."""
    text = _runbook_text()
    fences = text.count("```")
    assert fences >= 2 and fences % 2 == 0, (
        "Runbook must use balanced ``` fenced code blocks for its commands"
    )
    # Spot-check that key commands appear within a fenced block.
    blocks = re.findall(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL)
    blob = "\n".join(blocks)
    for needle in ("python3.12 -m venv venv", "claude setup-token",
                   "install_launchd.sh", "scripts/doctor.py"):
        assert needle in blob, (
            f"Command {needle!r} must appear inside a copy-pasteable code fence"
        )
