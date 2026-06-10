"""Tests for issue #706: Strengthen idempotency check in document_issue.py.

Original bug (document_issue.py:314): the idempotency guard in `_apply_change`
used a naive whole-file substring match — `if content in text: return False`.
If the same content appeared in a *different* section, or merely as a substring
of an unrelated line, a valid insertion was silently skipped.

AC coverage (derived from the ticket's stated Issue + Suggested fix):
  AC1 — content that already exists in a DIFFERENT section than the target does
         NOT block insertion into the target section.
  AC2 — content that already exists as an exact bullet WITHIN the target section
         IS skipped (true idempotency is preserved — no duplicate insert).
  AC3 — content that is only a SUBSTRING of a longer existing line (not an exact
         line/bullet) is NOT treated as a duplicate — it is inserted.
  AC4 — sectionless content is still deduped against an exact existing line
         (backward-compatible idempotency for the no-section path).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# ── import target module without side-effects ──────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

for _mod_name in (
    "github_client",
    "services.run_id",
    "services.logging",
    "dotenv",
):
    if _mod_name not in sys.modules:
        stub = types.ModuleType(_mod_name)
        if _mod_name == "services.run_id":
            stub.mint_run_id = lambda *a, **kw: "test-run-id"
        if _mod_name == "services.logging":
            stub.log = types.SimpleNamespace(set_context=lambda **kw: None)
        if _mod_name == "dotenv":
            stub.load_dotenv = lambda *a, **kw: None
        sys.modules[_mod_name] = stub

import services.sprint_manager.document_issue as di  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "DOC.md"
    p.write_text(body, encoding="utf-8")
    return p


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — same content in a DIFFERENT section must not block a valid insertion
# ═══════════════════════════════════════════════════════════════════════════════

def test_same_content_in_other_section_does_not_block(tmp_path):
    """AC1: bullet already present under '## Other' must still be inserted under
    the target '## Features' section — the old whole-file substring check
    wrongly skipped this."""
    doc = _write(
        tmp_path,
        "# Project\n\n## Features\n\n- alpha\n\n## Other\n\n- shared bullet\n",
    )
    change = {"type": "add_bullet", "section": "## Features", "content": "- shared bullet"}

    modified = di._apply_change(doc, change)

    text = doc.read_text(encoding="utf-8")
    assert modified is True, "insertion into target section must not be skipped"
    # The bullet must now appear in the Features section body, not only Other.
    features_block = text.split("## Other")[0]
    assert "- shared bullet" in features_block, (
        "bullet must be inserted into the target Features section"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — exact duplicate WITHIN the target section is skipped (idempotent)
# ═══════════════════════════════════════════════════════════════════════════════

def test_exact_duplicate_in_target_section_is_skipped(tmp_path):
    """AC2: an exact bullet already present in the target section must NOT be
    inserted again (returns False, file unchanged)."""
    body = "# Project\n\n## Features\n\n- alpha\n- shared bullet\n"
    doc = _write(tmp_path, body)
    change = {"type": "add_bullet", "section": "## Features", "content": "- shared bullet"}

    modified = di._apply_change(doc, change)

    assert modified is False, "exact duplicate in target section must be skipped"
    assert doc.read_text(encoding="utf-8") == body, "file must be unchanged on skip"
    assert doc.read_text(encoding="utf-8").count("- shared bullet") == 1, (
        "no duplicate bullet may be written"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — substring of a longer line is NOT a duplicate
# ═══════════════════════════════════════════════════════════════════════════════

def test_substring_of_longer_line_is_not_duplicate(tmp_path):
    """AC3: content that only appears as a substring of a longer existing line
    must not be treated as already-present — it is a distinct bullet."""
    doc = _write(
        tmp_path,
        "# Project\n\n## Features\n\n- Fixed the login bug in auth middleware completely\n",
    )
    change = {"type": "add_bullet", "section": "## Features", "content": "- Fixed the login bug"}

    modified = di._apply_change(doc, change)

    text = doc.read_text(encoding="utf-8")
    assert modified is True, "substring-only match must not block the insertion"
    assert "- Fixed the login bug\n" in text, "the distinct bullet must be inserted"


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — sectionless content still deduped against an exact existing line
# ═══════════════════════════════════════════════════════════════════════════════

def test_sectionless_exact_line_is_idempotent(tmp_path):
    """AC4: with no section, an exact existing line still skips (backward-compat
    idempotency for the append-at-end path)."""
    body = "# Project\n\n- standalone note\n"
    doc = _write(tmp_path, body)
    change = {"type": "add_bullet", "section": "", "content": "- standalone note"}

    modified = di._apply_change(doc, change)

    assert modified is False, "exact existing line must be deduped even without a section"
    assert doc.read_text(encoding="utf-8") == body, "file must be unchanged on skip"


def test_sectionless_new_line_is_appended(tmp_path):
    """AC4 (companion): a genuinely new sectionless line is still appended."""
    doc = _write(tmp_path, "# Project\n\n- standalone note\n")
    change = {"type": "add_bullet", "section": "", "content": "- brand new note"}

    modified = di._apply_change(doc, change)

    assert modified is True
    assert "- brand new note" in doc.read_text(encoding="utf-8")
