"""Tests for issue #1359: Remove accidentally committed .commander/settings_store.json"""
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def repo_root():
    return Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    )


def test_settings_store_not_tracked_by_git(repo_root):
    """AC1: .commander/settings_store.json must not appear in git ls-files."""
    result = subprocess.run(
        ["git", "ls-files", ".commander/settings_store.json"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "", (
        ".commander/settings_store.json is still tracked by git. "
        "Run `git rm --cached .commander/settings_store.json` to untrack it."
    )


def test_settings_store_in_gitignore(repo_root):
    """AC2: .commander/settings_store.json must be covered by .gitignore."""
    # Check via git check-ignore — the gold standard
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".commander/settings_store.json"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        ".commander/settings_store.json is not covered by any .gitignore rule. "
        "Add 'settings_store.json' or '.commander/settings_store.json' to .gitignore."
    )


def test_settings_store_not_in_head(repo_root):
    """AC5: git log confirms settings_store.json is no longer present in HEAD."""
    result = subprocess.run(
        ["git", "ls-tree", "HEAD", ".commander/settings_store.json"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "", (
        ".commander/settings_store.json still appears in HEAD tree. "
        "It must be removed via `git rm --cached` and committed."
    )


def test_settings_store_still_readable_by_settings_repo_if_present(repo_root, tmp_path, monkeypatch):
    """AC4: settings_repo.py can still read settings_store.json if it exists on disk.

    Untracked files are not deleted by git rm --cached. The file-based fallback
    in settings_repo._fallback_read_all() must still work when the JSON file exists.
    """
    import sys
    sys.path.insert(0, str(repo_root))

    from services.sprint_manager import settings_repo

    payload = {"global||log_level": "INFO", "global||coder_model": "claude-sonnet-4-6"}
    store_file = tmp_path / "settings_store.json"
    store_file.write_text(json.dumps(payload))

    # Patch both the path resolver and DB_PATH so it falls through to the JSON file
    monkeypatch.setattr(settings_repo, "_fallback_store_path", lambda: store_file)
    monkeypatch.delenv("DB_PATH", raising=False)

    data = settings_repo._fallback_read_all()
    assert isinstance(data, dict), "Expected dict from _fallback_read_all"
    assert data.get("global||log_level") == "INFO", (
        "settings_repo._fallback_read_all() could not read the JSON store. "
        "The file must still be readable even when untracked."
    )


def test_gitignore_entry_format(repo_root):
    """AC2 (extended): The .gitignore entry should be present in the root .gitignore."""
    gitignore_path = repo_root / ".gitignore"
    assert gitignore_path.exists(), ".gitignore not found at repo root"
    content = gitignore_path.read_text()
    # Accept either bare filename pattern or full relative path
    has_entry = (
        "settings_store.json" in content
        or ".commander/settings_store.json" in content
    )
    assert has_entry, (
        "Neither 'settings_store.json' nor '.commander/settings_store.json' "
        "found in root .gitignore. Add one of these patterns."
    )
