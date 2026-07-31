"""Tests for issue #1428: Docs-only routing heuristic omits .yml extension.

AC items verified:
  AC1  _DOCS_PATH_EXTENSIONS includes '.yml' alongside '.md', '.yaml', '.json'
  AC2  A ticket with exclusively .yml files is classified as docs-only by _is_docs_only
  AC3  A ticket with .yml AND a non-docs extension (.py) is NOT classified as docs-only
  AC4  Existing behaviour for .md, .yaml, .json is unchanged
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

from services.sprint_manager.model_routing import _DOCS_PATH_EXTENSIONS, _is_docs_only  # noqa: E402


# ── AC1: _DOCS_PATH_EXTENSIONS includes '.yml' ────────────────────────────────

def test_docs_path_extensions_includes_yml():
    """AC1: _DOCS_PATH_EXTENSIONS must contain '.yml'."""
    assert ".yml" in _DOCS_PATH_EXTENSIONS, (
        f"'.yml' missing from _DOCS_PATH_EXTENSIONS; got {_DOCS_PATH_EXTENSIONS!r}"
    )


def test_docs_path_extensions_still_has_md_yaml_json():
    """AC1: existing extensions .md, .yaml, .json remain present."""
    for ext in (".md", ".yaml", ".json"):
        assert ext in _DOCS_PATH_EXTENSIONS, (
            f"'{ext}' was dropped from _DOCS_PATH_EXTENSIONS; got {_DOCS_PATH_EXTENSIONS!r}"
        )


def test_docs_path_extensions_is_frozenset():
    """AC1: _DOCS_PATH_EXTENSIONS is still a frozenset (type unchanged)."""
    assert isinstance(_DOCS_PATH_EXTENSIONS, frozenset)


# ── AC2: exclusively .yml files → docs-only ───────────────────────────────────

def test_is_docs_only_yml_only_is_true():
    """AC2: _is_docs_only returns True when all paths are .yml files."""
    result, reason = _is_docs_only({"files_likely_affected": ["sprint.yml"]})
    assert result is True, "Expected docs-only=True for a single .yml file"
    assert reason == "docs-only:paths"


def test_is_docs_only_multiple_yml_files_is_true():
    """AC2: _is_docs_only returns True for multiple .yml files."""
    result, reason = _is_docs_only({
        "files_likely_affected": ["sprint.yml", ".github/workflows/ci.yml"]
    })
    assert result is True
    assert reason == "docs-only:paths"


def test_is_docs_only_yml_routes_to_haiku(monkeypatch):
    """AC2: A .yml-only ticket is routed to Haiku (not Sonnet) via _resolve_coder_model."""
    from services.sprint_manager.model_routing import _resolve_coder_model

    estimate = {
        "size": "M",
        "files_likely_affected": ["sprint.yml"],
    }
    model, reason = _resolve_coder_model(issue_num=9999, cfg=None, estimate=estimate)
    assert "haiku" in model.lower(), (
        f"Expected Haiku model for .yml-only ticket, got {model!r} (reason={reason!r})"
    )
    assert reason == "docs-only:paths"


# ── AC3: .yml mixed with .py → NOT docs-only ──────────────────────────────────

def test_is_docs_only_yml_and_py_is_false():
    """AC3: _is_docs_only returns False when .yml and .py appear together."""
    result, reason = _is_docs_only({
        "files_likely_affected": ["sprint.yml", "server.py"]
    })
    assert result is False, "Mixed .yml + .py should NOT be docs-only"
    assert reason == ""


def test_is_docs_only_yml_and_ts_is_false():
    """AC3: _is_docs_only returns False when .yml and .ts appear together."""
    result, reason = _is_docs_only({
        "files_likely_affected": [".github/workflows/ci.yml", "app.ts"]
    })
    assert result is False
    assert reason == ""


# ── AC4: existing .md / .yaml / .json behaviour unchanged ─────────────────────

def test_is_docs_only_md_only_still_true():
    """AC4: .md files still classified as docs-only (regression)."""
    result, reason = _is_docs_only({"files_likely_affected": ["README.md"]})
    assert result is True
    assert reason == "docs-only:paths"


def test_is_docs_only_yaml_only_still_true():
    """AC4: .yaml files still classified as docs-only (regression)."""
    result, reason = _is_docs_only({"files_likely_affected": ["config.yaml"]})
    assert result is True
    assert reason == "docs-only:paths"


def test_is_docs_only_json_only_still_true():
    """AC4: .json files still classified as docs-only (regression)."""
    result, reason = _is_docs_only({"files_likely_affected": ["schema.json"]})
    assert result is True
    assert reason == "docs-only:paths"


def test_is_docs_only_mixed_existing_extensions_still_true():
    """AC4: .md + .yaml + .json together still classified as docs-only (regression)."""
    result, reason = _is_docs_only({
        "files_likely_affected": ["README.md", "config.yaml", "schema.json"]
    })
    assert result is True
    assert reason == "docs-only:paths"
