"""Tests for issue #1428: Add .yml extension to docs-only routing heuristic.

AC-1: _DOCS_PATH_EXTENSIONS includes .yml alongside .md, .yaml, and .json
AC-2: A ticket with only .yml files is classified as docs-only
AC-3: A ticket with .yml and non-docs extensions is NOT classified as docs-only
AC-4: Existing .md, .yaml, .json behavior is unchanged (regression test)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_yml_extension_in_docs_path_extensions__1428_ac1():
    """AC-1: _DOCS_PATH_EXTENSIONS includes .yml"""
    from services.sprint_manager.model_routing import _DOCS_PATH_EXTENSIONS
    assert isinstance(_DOCS_PATH_EXTENSIONS, frozenset)
    assert ".yml" in _DOCS_PATH_EXTENSIONS, ".yml must be in _DOCS_PATH_EXTENSIONS"


def test_yml_only_files_are_docs_only__1428_ac2():
    """AC-2: A ticket whose files are exclusively .yml is classified as docs-only"""
    from services.sprint_manager.model_routing import _is_docs_only
    estimate = {"files_likely_affected": ["sprint.yml"]}
    is_docs, reason = _is_docs_only(estimate)
    assert is_docs is True, "sprint.yml should be classified as docs-only"
    assert reason == "docs-only:paths"


def test_yml_with_code_is_not_docs_only__1428_ac3():
    """AC-3: A ticket with both .yml and code files is NOT docs-only"""
    from services.sprint_manager.model_routing import _is_docs_only
    estimate = {"files_likely_affected": ["sprint.yml", "server.py"]}
    is_docs, reason = _is_docs_only(estimate)
    assert is_docs is False, "A ticket with code files should not be docs-only"
    assert reason == ""


def test_existing_extensions_unchanged__1428_ac4():
    """AC-4: Existing .md, .yaml, .json files continue to match docs-only heuristic"""
    from services.sprint_manager.model_routing import _is_docs_only

    # Test .md
    estimate_md = {"files_likely_affected": ["README.md"]}
    is_docs_md, _ = _is_docs_only(estimate_md)
    assert is_docs_md is True

    # Test .yaml
    estimate_yaml = {"files_likely_affected": ["config.yaml"]}
    is_docs_yaml, _ = _is_docs_only(estimate_yaml)
    assert is_docs_yaml is True

    # Test .json
    estimate_json = {"files_likely_affected": ["schema.json"]}
    is_docs_json, _ = _is_docs_only(estimate_json)
    assert is_docs_json is True

    # Test mixed existing extensions
    estimate_mixed = {"files_likely_affected": ["README.md", "config.yaml", "schema.json"]}
    is_docs_mixed, reason = _is_docs_mixed = _is_docs_only(estimate_mixed)
    assert is_docs_mixed is True


def test_yml_routing_via_resolve_coder_model():
    """Bonus: Confirm .yml docs-only routing cascades to model selection (Haiku not Sonnet)"""
    from services.sprint_manager.model_routing import _resolve_coder_model

    estimate = {"size": "M", "files_likely_affected": ["sprint.yml"]}
    model, reason = _resolve_coder_model(1, None, estimate=estimate)
    assert "haiku" in model.lower(), "M-sized docs-only (sprint.yml) should route to Haiku"
    assert reason == "docs-only:paths"
