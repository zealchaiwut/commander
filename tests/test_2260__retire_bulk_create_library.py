"""Tests for issue #2260: Retire the bulk-create prompt library to lookout ideas.

AC coverage:
  AC1 - The DoR spec is migrated out before deletion (to lookout ideas or the milestone doc)
  AC2 - Any other still-relevant drafts are migrated to vault/ideas/ in lookout
  AC3 - docs/bulk-create/ removed, along with the convention note in CLAUDE.md
  AC4 - The Bulk Create UI still creates tickets normally
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"


# ---------------------------------------------------------------------------
# AC3 — docs/bulk-create/ directory removed
# ---------------------------------------------------------------------------

def test_bulk_create_docs_dir_deleted():
    """AC3: docs/bulk-create/ directory must not exist after retirement."""
    bulk_create_dir = REPO_ROOT / "docs" / "bulk-create"
    assert not bulk_create_dir.exists(), (
        f"docs/bulk-create/ still exists at {bulk_create_dir}. "
        "The bulk-create prompt library must be removed as part of issue #2260."
    )


# ---------------------------------------------------------------------------
# AC3 — CLAUDE.md convention note removed
# ---------------------------------------------------------------------------

def test_claude_md_bulk_create_note_removed():
    """AC3: CLAUDE.md must not contain the bulk-create/ convention entry."""
    claude_md = REPO_ROOT / "CLAUDE.md"
    text = claude_md.read_text(encoding="utf-8")
    assert "bulk-create/" not in text, (
        "CLAUDE.md still contains the bulk-create/ convention note. "
        "Remove the 'bulk-create/' line from the Standard Docs Structure section."
    )


# ---------------------------------------------------------------------------
# AC1 — DoR spec destination is documented
# ---------------------------------------------------------------------------

def test_dor_spec_referenced_in_milestone_or_issue():
    """AC1: The DoR spec must be referenced in the milestone doc or captured in issue #2262."""
    shrink_plan = REPO_ROOT / "docs" / "milestones" / "commander-shrink-2026-08.md"
    assert shrink_plan.exists(), "commander-shrink-2026-08.md milestone doc must exist"
    text = shrink_plan.read_text(encoding="utf-8")
    # The DoR spec was captured as issue #2262; the milestone must reference it.
    assert "#2262" in text or "2262" in text, (
        "Milestone doc does not reference issue #2262 (the DoR parser ticket). "
        "Add a note that the DoR spec was migrated to issue #2262."
    )


# ---------------------------------------------------------------------------
# AC4 — Bulk Create UI / router still works
# ---------------------------------------------------------------------------

def test_bulk_tickets_router_importable():
    """AC4: routers/bulk_tickets.py must still be importable (UI code retained)."""
    sys.path.insert(0, str(DASHBOARD_DIR))
    try:
        mod = importlib.import_module("routers.bulk_tickets")
        assert hasattr(mod, "router"), (
            "routers/bulk_tickets.py exists but has no 'router' attribute."
        )
    finally:
        sys.path.remove(str(DASHBOARD_DIR))


def test_bulk_tickets_file_exists():
    """AC4: The bulk_tickets.py source file must still be present (not deleted)."""
    bulk_tickets = DASHBOARD_DIR / "routers" / "bulk_tickets.py"
    assert bulk_tickets.exists(), (
        f"routers/bulk_tickets.py was deleted. The Bulk Create UI must be retained (issue #2260 scope excludes it)."
    )


# ---------------------------------------------------------------------------
# AC4 — HTTP test: Bulk Create endpoint is accessible
# ---------------------------------------------------------------------------

def test_bulk_create_endpoint_registered():
    """AC4: the Bulk Create endpoint must still be registered on the app.

    Probed in-process with TestClient using GET against a POST-only route: a
    registered route answers 405 Method Not Allowed, a removed one answers 404.
    This proves the endpoint survived without creating anything.

    The original version issued a live httpx POST to /api/tickets/bulk against
    UAT_BASE_URL, which (a) failed in any environment with no server listening
    and (b) risked creating real tickets when one was. See CLAUDE.md #1746.
    """
    import sys

    if str(DASHBOARD_DIR) not in sys.path:
        sys.path.insert(0, str(DASHBOARD_DIR))

    from fastapi.testclient import TestClient
    import server as srv

    client = TestClient(srv.app)
    r = client.get("/api/tickets/bulk")

    assert r.status_code != 404, (
        "POST /api/tickets/bulk is no longer registered — this ticket must "
        "retire only the prompt library, not the Bulk Create UI."
    )
    assert r.status_code == 405, (
        f"expected 405 Method Not Allowed from a registered POST-only route, got {r.status_code}"
    )
