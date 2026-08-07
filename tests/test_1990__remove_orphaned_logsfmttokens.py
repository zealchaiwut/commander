"""Tests for issue #1990 — remove orphaned _logsFmtTokens dead code.

After #1870 removed _logsTicketStatsHtml (the only caller of _logsFmtTokens),
the wrapper function became dead code. This ticket deletes it.

AC coverage:
  AC1 — _logsFmtTokens function is not defined in project.html
  AC2 — no call sites for _logsFmtTokens remain in project.html or any
         frontend static file
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
PROJECT_HTML = (STATIC_DIR / "project.html").read_text(encoding="utf-8")


# ── AC1: function definition is gone ──────────────────────────────────────────

def test_logsfmttokens_definition_removed():
    """_logsFmtTokens must not be defined anywhere in project.html."""
    assert "function _logsFmtTokens(" not in PROJECT_HTML, (
        "_logsFmtTokens is still defined in project.html — delete it"
    )


# ── AC2: no remaining call sites ───────────────────────────────────────────────

def test_logsfmttokens_no_call_sites_in_project_html():
    """No references to _logsFmtTokens must remain in project.html."""
    assert "_logsFmtTokens" not in PROJECT_HTML, (
        "_logsFmtTokens is still referenced in project.html"
    )


def test_logsfmttokens_no_call_sites_in_static_files():
    """No references to _logsFmtTokens must remain in any frontend static file."""
    for path in STATIC_DIR.rglob("*.html"):
        content = path.read_text(encoding="utf-8")
        assert "_logsFmtTokens" not in content, (
            f"_logsFmtTokens still referenced in {path.relative_to(REPO_ROOT)}"
        )
    for path in STATIC_DIR.rglob("*.js"):
        content = path.read_text(encoding="utf-8")
        assert "_logsFmtTokens" not in content, (
            f"_logsFmtTokens still referenced in {path.relative_to(REPO_ROOT)}"
        )
