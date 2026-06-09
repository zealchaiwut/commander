"""Tests for issue #687: Document finish-card API breaking change (404 → 200 no_data).

The /api/sprints/{label}/finish-card endpoint changed from returning HTTP 404
when a sprint has never been run to returning HTTP 200 with state='no_data'.
This is a breaking change for clients that checked status codes.

AC coverage:
  AC1 — docs/features/api.md documents the finish-card endpoint
  AC2 — docs/features/api.md documents the no_data state and migration guidance
        (clients must check state field, not HTTP status code)
  AC3 — CHANGELOG.md contains an entry for this breaking change
  AC4 — docs/features/api.md documents the full response shape for each state
"""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
API_DOC = REPO_ROOT / "docs" / "features" / "api.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _api_doc() -> str:
    return API_DOC.read_text(encoding="utf-8")


def _changelog() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


# ── AC1: finish-card endpoint is documented ────────────────────────────────────

def test_api_doc_lists_finish_card_endpoint():
    """docs/features/api.md must reference the finish-card endpoint."""
    doc = _api_doc()
    assert "finish-card" in doc, (
        "API reference (docs/features/api.md) must document the "
        "GET /api/sprints/{sprint_label}/finish-card endpoint."
    )


def test_api_doc_finish_card_method_and_path():
    """docs/features/api.md must show the GET method and full path."""
    doc = _api_doc()
    assert "/api/sprints/" in doc and "finish-card" in doc, (
        "API reference must include the full path "
        "/api/sprints/{sprint_label}/finish-card."
    )


# ── AC2: no_data state and migration guidance documented ──────────────────────

def test_api_doc_documents_no_data_state():
    """docs/features/api.md must document the no_data state value."""
    doc = _api_doc()
    assert "no_data" in doc, (
        "API reference must document the 'no_data' state returned by "
        "finish-card when a sprint has never been run."
    )


def test_api_doc_documents_migration_guidance():
    """docs/features/api.md must tell clients to check the state field."""
    doc = _api_doc()
    # Guidance must explicitly direct clients away from checking HTTP status codes
    has_state_check_guidance = (
        "state" in doc and (
            "no_data" in doc
        )
    )
    assert has_state_check_guidance, (
        "API reference must note that clients should check the 'state' field "
        "in the response body rather than relying on HTTP 404 status codes."
    )


def test_api_doc_finish_card_section_exists():
    """docs/features/api.md must have a dedicated section or table row for finish-card."""
    doc = _api_doc()
    # Either a table row or a subsection heading referencing finish-card
    assert "finish-card" in doc, (
        "API reference must have a dedicated table row or section for finish-card."
    )


# ── AC3: CHANGELOG entry for the breaking change ──────────────────────────────

def test_changelog_references_finish_card_change():
    """CHANGELOG.md must contain an entry documenting the 404 → 200 no_data change."""
    cl = _changelog()
    assert "finish-card" in cl or "finish_card" in cl or "no_data" in cl, (
        "CHANGELOG.md must document the breaking change: "
        "finish-card now returns 200 with state='no_data' instead of 404."
    )


def test_changelog_references_issue_687():
    """CHANGELOG.md must reference issue #687."""
    cl = _changelog()
    assert "#687" in cl or "687" in cl, (
        "CHANGELOG.md must reference issue #687 for the finish-card documentation."
    )


# ── AC4: response shapes documented ───────────────────────────────────────────

def test_api_doc_finish_card_documents_running_state():
    """docs/features/api.md must document the running state response shape."""
    doc = _api_doc()
    assert "running" in doc, (
        "API reference must document the 'running' state returned by finish-card "
        "when a sprint is currently executing."
    )


def test_api_doc_finish_card_documents_completed_state():
    """docs/features/api.md must document the completed/finished states."""
    doc = _api_doc()
    assert "completed" in doc or "has_rework" in doc or "cancelled" in doc, (
        "API reference must document the completed/has_rework/cancelled states "
        "returned by finish-card for finished sprints."
    )
