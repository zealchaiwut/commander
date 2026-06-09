"""Tests for issue #690: Document finish-card 200 with state="no_data" contract change.

The GET /api/sprints/{label}/finish-card endpoint returns HTTP 200 with
state="no_data" (not HTTP 404) when no sprint state file exists.
This was a subtle contract change that must be noted in:
  - The server.py endpoint docstring (for code readers)
  - SCHEMA.md API Endpoints section (for API reference readers)

AC coverage:
  AC1 — server.py docstring for get_sprint_finish_card documents the no_data state
  AC2 — server.py docstring notes the HTTP 200 (not 404) design decision
  AC3 — SCHEMA.md API Endpoints section documents the finish-card endpoint
  AC4 — SCHEMA.md documents the no_data state for the finish-card endpoint
"""
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVER_PY = REPO_ROOT / "apps" / "dashboard" / "server.py"
SCHEMA_MD = REPO_ROOT / "SCHEMA.md"


def _get_finish_card_docstring() -> str:
    source = SERVER_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_sprint_finish_card":
            docstring = ast.get_docstring(node)
            return docstring or ""
    raise AssertionError("get_sprint_finish_card function not found in server.py")


def _schema() -> str:
    return SCHEMA_MD.read_text(encoding="utf-8")


# ── AC1: docstring documents no_data state ────────────────────────────────────

def test_docstring_mentions_no_data_state():
    """get_sprint_finish_card docstring must mention the no_data state."""
    doc = _get_finish_card_docstring()
    assert "no_data" in doc, (
        "get_sprint_finish_card docstring must document the 'no_data' state "
        "returned when no sprint state file exists on disk."
    )


# ── AC2: docstring notes the 200 (not 404) design decision ───────────────────

def test_docstring_notes_200_not_404():
    """get_sprint_finish_card docstring must note that it returns 200 not 404."""
    doc = _get_finish_card_docstring()
    has_200_note = "200" in doc or "HTTP 200" in doc or "404" in doc
    assert has_200_note, (
        "get_sprint_finish_card docstring must note the design decision: "
        "returns HTTP 200 with state='no_data' instead of HTTP 404 when "
        "no sprint state file exists."
    )


# ── AC3: SCHEMA.md documents the finish-card endpoint ────────────────────────

def test_schema_md_lists_finish_card_endpoint():
    """SCHEMA.md API Endpoints section must reference the finish-card endpoint."""
    schema = _schema()
    assert "finish-card" in schema, (
        "SCHEMA.md API Endpoints section must document the "
        "GET /api/sprints/{sprint_label}/finish-card endpoint."
    )


def test_schema_md_finish_card_in_api_endpoints_section():
    """SCHEMA.md must list finish-card under the ## API Endpoints heading."""
    schema = _schema()
    api_section_start = schema.find("## API Endpoints")
    assert api_section_start != -1, "SCHEMA.md must have an ## API Endpoints section"
    api_section = schema[api_section_start:]
    assert "finish-card" in api_section, (
        "finish-card must appear under the ## API Endpoints section in SCHEMA.md."
    )


# ── AC4: SCHEMA.md documents no_data state for finish-card ───────────────────

def test_schema_md_documents_no_data_for_finish_card():
    """SCHEMA.md must document the no_data state for the finish-card endpoint."""
    schema = _schema()
    assert "no_data" in schema, (
        "SCHEMA.md must document the 'no_data' state for the finish-card endpoint "
        "so API reference readers understand the 200 vs 404 design decision."
    )
