"""Tests for issue #698: Extend documentor to update quick-start and tutorial docs.

AC coverage:
  AC1 — guide files discovered via known paths (docs/quickstart.md, docs/tutorial.md)
         and a configurable glob pattern
  AC2 — user-visible ticket triggers opening of discovered guide files
  AC3 — relevant sections updated; unchanged guides not modified
  AC4 — missing guide file → warning logged, no crash, other updates continue
  AC5 — new command/page ticket → guide section references it after run
  AC6 — existing CHANGELOG.md and README.md behavior unaffected
  AC7 — unit tests: guide found+updated, guide not found+skipped, no-user-visible+no-update
"""
from __future__ import annotations

import json
import sys
import types
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── import target module without side-effects ──────────────────────────────────
_REPO_ROOT = Path(__file__).parent.parent
_SVC = _REPO_ROOT / "services" / "sprint_manager"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

# Stub external dependencies so import doesn't fail in unit-test context
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
            _log = types.SimpleNamespace(set_context=lambda **kw: None)
            stub.log = _log
        if _mod_name == "dotenv":
            stub.load_dotenv = lambda *a, **kw: None
        sys.modules[_mod_name] = stub

import services.sprint_manager.document_issue as di  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_tmpdir(tmp_path: Path) -> Path:
    """Create a minimal fake repo inside tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".commander").mkdir()
    (repo / "README.md").write_text("# Project\n\n## Features\n\n- existing\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n", encoding="utf-8")
    (repo / "docs").mkdir()
    return repo


def _agent_result(guide_changes: dict | None = None, is_user_visible: bool = True) -> str:
    payload: dict = {
        "readme_changes": [],
        "claude_md_changes": [],
        "uat_comment_markdown": "",
        "is_user_visible": is_user_visible,
        "guide_changes": guide_changes or {},
    }
    return json.dumps(payload)


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — guide discovery
# ═══════════════════════════════════════════════════════════════════════════════

def test_discover_known_paths_found(tmp_path):
    """AC1: Known paths docs/quickstart.md + docs/tutorial.md are discovered."""
    repo = _make_tmpdir(tmp_path)
    (repo / "docs" / "quickstart.md").write_text("# Quick Start\n", encoding="utf-8")
    (repo / "docs" / "tutorial.md").write_text("# Tutorial\n", encoding="utf-8")

    found = di._discover_guide_files(repo, guide_glob=None)

    paths = [str(f.relative_to(repo)) for f in found]
    assert any("quickstart" in p.lower() for p in paths), (
        "_discover_guide_files must return docs/quickstart.md when it exists"
    )
    assert any("tutorial" in p.lower() for p in paths), (
        "_discover_guide_files must return docs/tutorial.md when it exists"
    )


def test_discover_glob_pattern(tmp_path):
    """AC1: Custom glob pattern discovers additional guide files."""
    repo = _make_tmpdir(tmp_path)
    guides_dir = repo / "docs" / "guides"
    guides_dir.mkdir()
    (guides_dir / "ONBOARDING.md").write_text("# Onboarding\n", encoding="utf-8")

    found = di._discover_guide_files(repo, guide_glob="docs/guides/*.md")

    paths = [str(f.relative_to(repo)) for f in found]
    assert any("ONBOARDING" in p for p in paths), (
        "_discover_guide_files with glob must find docs/guides/ONBOARDING.md"
    )


def test_discover_missing_known_paths(tmp_path):
    """AC1: Missing known paths do not cause an error; they are simply absent from results."""
    repo = _make_tmpdir(tmp_path)
    # docs dir exists but no quickstart/tutorial
    found = di._discover_guide_files(repo, guide_glob=None)
    # Should not crash and should return empty (or only glob hits)
    assert isinstance(found, list)


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — user-visible classification
# ═══════════════════════════════════════════════════════════════════════════════

def test_is_user_visible_new_command():
    """AC2: Ticket body mentioning new command is classified as user-visible."""
    body = "## What & Why\n\nAdds `--export` command to CLI.\n\n## Acceptance Criteria\n- [ ] New command ships"
    assert di._is_user_visible({"body": body, "title": "Add --export command"}) is True


def test_is_user_visible_new_ui_feature():
    """AC2: Ticket body mentioning new UI feature is classified as user-visible."""
    body = "Adds a new dashboard page for analytics."
    assert di._is_user_visible({"body": body, "title": "Add analytics page"}) is True


def test_is_user_visible_refactor_is_false():
    """AC2: Pure internal refactor is not classified as user-visible."""
    body = "Refactors internal caching layer. No user-visible change."
    assert di._is_user_visible({"body": body, "title": "Refactor cache"}) is False


def test_is_user_visible_changed_flow():
    """AC2: Ticket about changed user flow is user-visible."""
    body = "Changes the sprint creation flow to require confirmation."
    assert di._is_user_visible({"body": body, "title": "Update sprint creation flow"}) is True


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 + AC5 — guide found and updated; no-change guide not modified
# ═══════════════════════════════════════════════════════════════════════════════

def test_guide_updated_when_found(tmp_path):
    """AC3+AC5: Guide file updated with new content when agent returns guide_changes."""
    repo = _make_tmpdir(tmp_path)
    qs = repo / "docs" / "quickstart.md"
    qs.write_text("# Quick Start\n\n## Install\n\n- step 1\n", encoding="utf-8")

    agent_response = _agent_result(guide_changes={
        "docs/quickstart.md": [
            {"type": "add_bullet", "section": "## Install", "content": "- `--export` flag: run `cmd --export`"}
        ]
    })

    with patch.object(di, "_invoke_agent", return_value=agent_response), \
         patch.object(di, "_get_issue", return_value={"title": "Add --export", "body": "Adds new CLI command --export", "labels": []}), \
         patch.object(di, "_get_diff", return_value="diff content"), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        result = di.run_documentor(
            issue_num=698,
            repo="zealchaiwut/commander",
            mode="readme",
            skip_uat_comment=True,
            skip_changelog=True,
            git_root=repo,
        )

    content = qs.read_text(encoding="utf-8")
    assert "--export" in content, (
        "docs/quickstart.md must contain the new --export entry after documentor runs"
    )


def test_guide_not_modified_when_no_changes(tmp_path):
    """AC3: Guide not modified when agent returns no changes for it."""
    repo = _make_tmpdir(tmp_path)
    qs = repo / "docs" / "quickstart.md"
    original = "# Quick Start\n\n## Install\n\n- step 1\n"
    qs.write_text(original, encoding="utf-8")

    agent_response = _agent_result(guide_changes={})

    with patch.object(di, "_invoke_agent", return_value=agent_response), \
         patch.object(di, "_get_issue", return_value={"title": "Refactor", "body": "Refactors internal caching. No user-visible change.", "labels": []}), \
         patch.object(di, "_get_diff", return_value=""), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        di.run_documentor(
            issue_num=698,
            repo="zealchaiwut/commander",
            mode="readme",
            skip_uat_comment=True,
            skip_changelog=True,
            git_root=repo,
        )

    assert qs.read_text(encoding="utf-8") == original, (
        "Guide file must not be modified when agent returns no guide_changes for it"
    )


def test_no_user_visible_guides_not_opened(tmp_path):
    """AC7: No-user-visible ticket → guide files not updated."""
    repo = _make_tmpdir(tmp_path)
    qs = repo / "docs" / "quickstart.md"
    original = "# Quick Start\n\n- existing\n"
    qs.write_text(original, encoding="utf-8")

    # Agent says not user-visible
    agent_response = _agent_result(guide_changes={}, is_user_visible=False)

    invoke_calls = []

    def mock_invoke(prompt):
        invoke_calls.append(prompt)
        return agent_response

    with patch.object(di, "_invoke_agent", side_effect=mock_invoke), \
         patch.object(di, "_get_issue", return_value={"title": "Refactor cache", "body": "Pure internal refactor. No user-visible change.", "labels": []}), \
         patch.object(di, "_get_diff", return_value=""), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        di.run_documentor(
            issue_num=698,
            repo="zealchaiwut/commander",
            mode="readme",
            skip_uat_comment=True,
            skip_changelog=True,
            git_root=repo,
        )

    assert qs.read_text(encoding="utf-8") == original, (
        "Guide file must remain unmodified for non-user-visible tickets"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — missing guide → warning, no crash
# ═══════════════════════════════════════════════════════════════════════════════

def test_missing_guide_warns_and_skips(tmp_path, capsys):
    """AC4: Missing guide file → documentor logs warning, does not crash."""
    repo = _make_tmpdir(tmp_path)
    # quickstart.md does NOT exist

    agent_response = _agent_result(guide_changes={
        "docs/quickstart.md": [
            {"type": "add_bullet", "section": "## Install", "content": "- new step"}
        ]
    })

    with patch.object(di, "_invoke_agent", return_value=agent_response), \
         patch.object(di, "_get_issue", return_value={"title": "Add command", "body": "Adds new CLI command", "labels": []}), \
         patch.object(di, "_get_diff", return_value="diff"), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        # Must not raise
        di.run_documentor(
            issue_num=698,
            repo="zealchaiwut/commander",
            mode="readme",
            skip_uat_comment=True,
            skip_changelog=True,
            git_root=repo,
        )

    captured = capsys.readouterr()
    assert "warn" in captured.out.lower() or "skip" in captured.out.lower() or \
           "not found" in captured.out.lower(), (
        "documentor must log a warning when a guide file is missing"
    )


def test_missing_guide_readme_still_updated(tmp_path):
    """AC4+AC6: Missing guide does not prevent README.md from being updated."""
    repo = _make_tmpdir(tmp_path)
    readme = repo / "README.md"
    readme.write_text("# Project\n\n## Features\n\n- existing\n", encoding="utf-8")

    agent_response = json.dumps({
        "readme_changes": [
            {"type": "add_bullet", "section": "## Features", "content": "- new feature"}
        ],
        "claude_md_changes": [],
        "uat_comment_markdown": "",
        "is_user_visible": True,
        "guide_changes": {
            "docs/quickstart.md": [
                {"type": "add_bullet", "section": "## Install", "content": "- new step"}
            ]
        },
    })

    with patch.object(di, "_invoke_agent", return_value=agent_response), \
         patch.object(di, "_get_issue", return_value={"title": "Add feature", "body": "Adds new UI feature", "labels": []}), \
         patch.object(di, "_get_diff", return_value="diff"), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        di.run_documentor(
            issue_num=698,
            repo="zealchaiwut/commander",
            mode="readme",
            skip_uat_comment=True,
            skip_changelog=True,
            git_root=repo,
        )

    assert "new feature" in readme.read_text(encoding="utf-8"), (
        "README.md must still be updated even when a guide file is missing"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — existing CHANGELOG + README behavior unaffected
# ═══════════════════════════════════════════════════════════════════════════════

def test_changelog_entry_still_created(tmp_path):
    """AC6: CHANGELOG.md still gets an entry when guide logic is active."""
    repo = _make_tmpdir(tmp_path)
    changelog = repo / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n", encoding="utf-8")
    (repo / "docs" / "quickstart.md").write_text("# Quick Start\n", encoding="utf-8")

    agent_response = _agent_result(guide_changes={})

    # We need git for _append_changelog_entry; mock commit helper
    with patch.object(di, "_invoke_agent", return_value=agent_response), \
         patch.object(di, "_get_issue", return_value={"title": "Add feature", "body": "Adds new UI feature", "labels": []}), \
         patch.object(di, "_get_diff", return_value=""), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        di.run_documentor(
            issue_num=698,
            repo="zealchaiwut/commander",
            mode="readme",
            skip_uat_comment=True,
            skip_changelog=False,
            git_root=repo,
        )

    content = changelog.read_text(encoding="utf-8")
    assert "#698" in content, (
        "CHANGELOG.md must still contain an entry for the issue after guide logic runs"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 idempotency — second run does not duplicate content
# ═══════════════════════════════════════════════════════════════════════════════

def test_guide_update_idempotent(tmp_path):
    """AC3: Running documentor twice does not duplicate guide content."""
    repo = _make_tmpdir(tmp_path)
    qs = repo / "docs" / "quickstart.md"
    qs.write_text("# Quick Start\n\n## Install\n\n- step 1\n", encoding="utf-8")

    new_bullet = "- `--export` flag: run `cmd --export`"
    agent_response = _agent_result(guide_changes={
        "docs/quickstart.md": [
            {"type": "add_bullet", "section": "## Install", "content": new_bullet}
        ]
    })

    kwargs = dict(
        issue_num=698,
        repo="zealchaiwut/commander",
        mode="readme",
        skip_uat_comment=True,
        skip_changelog=True,
        git_root=repo,
    )

    with patch.object(di, "_invoke_agent", return_value=agent_response), \
         patch.object(di, "_get_issue", return_value={"title": "Add --export", "body": "Adds new CLI command --export", "labels": []}), \
         patch.object(di, "_get_diff", return_value="diff"), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        di.run_documentor(**kwargs)

    # Simulate second run: agent would return same changes but file already has them
    content_after_first = qs.read_text(encoding="utf-8")

    # Build agent response that returns the same bullet (already present in file)
    agent_response2 = _agent_result(guide_changes={
        "docs/quickstart.md": [
            {"type": "add_bullet", "section": "## Install", "content": new_bullet}
        ]
    })

    with patch.object(di, "_invoke_agent", return_value=agent_response2), \
         patch.object(di, "_get_issue", return_value={"title": "Add --export", "body": "Adds new CLI command --export", "labels": []}), \
         patch.object(di, "_get_diff", return_value="diff"), \
         patch.object(di, "_find_feature_branch", return_value="feature/698-test"), \
         patch.object(di, "_post_uat_comment", return_value=None), \
         patch.object(di, "_commit_doc_changes", return_value=None):

        di.run_documentor(**kwargs)

    final = qs.read_text(encoding="utf-8")
    assert final.count(new_bullet) == 1, (
        f"Guide must not duplicate content on second run. "
        f"Expected 1 occurrence, got {final.count(new_bullet)}"
    )
