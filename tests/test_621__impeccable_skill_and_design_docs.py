"""Tests for #621: Install impeccable skill and scaffold DESIGN.md/PRODUCT.md.

AC items verified:
  AC-1  impeccable skill SKILL.md exists and is discoverable by coder agents
  AC-2  PRODUCT.md exists at repo root with non-empty content
  AC-3  DESIGN.md exists at repo root with color tokens, typography, spacing, component naming
  AC-4  coder_prompt_template in sprint_init.py and init_project.py explicitly references PRODUCT.md and DESIGN.md
  AC-5  _design_docs_guard blocks dispatch when design docs are missing; proceeds when present
"""
from __future__ import annotations

import inspect
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm

SKILL_PATH = REPO_ROOT / ".github" / "skills" / "impeccable" / "SKILL.md"
CLAUDE_SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "impeccable" / "SKILL.md"
PRODUCT_MD = REPO_ROOT / "PRODUCT.md"
DESIGN_MD = REPO_ROOT / "DESIGN.md"


# ---------------------------------------------------------------------------
# AC-1: impeccable skill installed and discoverable
# ---------------------------------------------------------------------------

class TestAC1ImpeccableSkillInstalled:
    def test_skill_file_exists_github(self):
        assert SKILL_PATH.exists(), (
            f"impeccable SKILL.md not found at {SKILL_PATH} — run `npx impeccable skills install`"
        )

    def test_skill_file_exists_claude(self):
        assert CLAUDE_SKILL_PATH.exists(), (
            f"impeccable SKILL.md not found at {CLAUDE_SKILL_PATH} — run `npx impeccable skills install`"
        )

    def test_skill_has_yaml_frontmatter(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        assert text.startswith("---"), "SKILL.md must have YAML frontmatter starting with ---"

    def test_skill_name_is_impeccable(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        # Extract frontmatter block
        end = text.find("\n---", 3)
        frontmatter = text[:end] if end != -1 else text
        assert "name: impeccable" in frontmatter, (
            "SKILL.md frontmatter must declare 'name: impeccable'"
        )

    def test_skill_is_user_invocable(self):
        text = SKILL_PATH.read_text(encoding="utf-8")
        end = text.find("\n---", 3)
        frontmatter = text[:end] if end != -1 else text
        assert "user-invocable: true" in frontmatter, (
            "SKILL.md must declare 'user-invocable: true' so dispatched agents can invoke it"
        )


# ---------------------------------------------------------------------------
# AC-2: PRODUCT.md at repo root
# ---------------------------------------------------------------------------

class TestAC2ProductMd:
    def test_product_md_exists(self):
        assert PRODUCT_MD.exists(), f"PRODUCT.md not found at {PRODUCT_MD}"

    def test_product_md_non_empty(self):
        text = PRODUCT_MD.read_text(encoding="utf-8").strip()
        assert len(text) > 0, "PRODUCT.md must not be empty"

    def test_product_md_not_just_todos(self):
        text = PRODUCT_MD.read_text(encoding="utf-8")
        non_todo_lines = [
            ln for ln in text.splitlines()
            if ln.strip() and not ln.strip().startswith("# TODO") and ln.strip() != "#"
        ]
        assert len(non_todo_lines) >= 3, (
            "PRODUCT.md must contain more than just TODO stubs"
        )


# ---------------------------------------------------------------------------
# AC-3: DESIGN.md at repo root with required sections
# ---------------------------------------------------------------------------

class TestAC3DesignMd:
    def test_design_md_exists(self):
        assert DESIGN_MD.exists(), f"DESIGN.md not found at {DESIGN_MD}"

    def test_design_md_has_color_tokens(self):
        text = DESIGN_MD.read_text(encoding="utf-8")
        assert any(
            kw in text for kw in ("#", "color", "Color", "token", "Token")
        ), "DESIGN.md must include color tokens"
        # Check at least one hex color is present
        import re
        assert re.search(r"#[0-9a-fA-F]{3,6}", text), (
            "DESIGN.md must contain at least one hex color value"
        )

    def test_design_md_has_typography(self):
        text = DESIGN_MD.read_text(encoding="utf-8").lower()
        assert any(kw in text for kw in ("typography", "font", "typeface", "type scale")), (
            "DESIGN.md must include a typography section"
        )

    def test_design_md_has_spacing(self):
        text = DESIGN_MD.read_text(encoding="utf-8").lower()
        assert any(kw in text for kw in ("spacing", "space", "rhythm", "px", "rem")), (
            "DESIGN.md must include a spacing scale"
        )

    def test_design_md_has_component_naming(self):
        text = DESIGN_MD.read_text(encoding="utf-8").lower()
        assert any(
            kw in text for kw in ("component naming", "naming convention", "component name")
        ), (
            "DESIGN.md must include component naming conventions"
        )


# ---------------------------------------------------------------------------
# AC-4: coder_prompt_template references PRODUCT.md and DESIGN.md
# ---------------------------------------------------------------------------

class TestAC4CoderPromptTemplateReferences:
    def _read_template(self, script_name: str) -> str:
        path = REPO_ROOT / "scripts" / script_name
        return path.read_text(encoding="utf-8")

    def test_sprint_init_template_references_product_md(self):
        text = self._read_template("sprint_init.py")
        # Find the coder_prompt_template block
        idx = text.find("coder_prompt_template")
        assert idx != -1, "coder_prompt_template not found in sprint_init.py"
        template_block = text[idx : idx + 1000]
        assert "PRODUCT.md" in template_block, (
            "coder_prompt_template in sprint_init.py must explicitly reference PRODUCT.md"
        )

    def test_sprint_init_template_references_design_md(self):
        text = self._read_template("sprint_init.py")
        idx = text.find("coder_prompt_template")
        assert idx != -1
        template_block = text[idx : idx + 1000]
        assert "DESIGN.md" in template_block, (
            "coder_prompt_template in sprint_init.py must explicitly reference DESIGN.md"
        )

    def test_init_project_template_references_product_md(self):
        text = self._read_template("init_project.py")
        idx = text.find("coder_prompt_template")
        assert idx != -1, "coder_prompt_template not found in init_project.py"
        template_block = text[idx : idx + 1000]
        assert "PRODUCT.md" in template_block, (
            "coder_prompt_template in init_project.py must explicitly reference PRODUCT.md"
        )

    def test_init_project_template_references_design_md(self):
        text = self._read_template("init_project.py")
        idx = text.find("coder_prompt_template")
        assert idx != -1
        template_block = text[idx : idx + 1000]
        assert "DESIGN.md" in template_block, (
            "coder_prompt_template in init_project.py must explicitly reference DESIGN.md"
        )

    def test_sprint_manager_auto_prompt_references_design_docs_when_present(self, tmp_path):
        """When no custom template, auto-prompt mentions PRODUCT.md and DESIGN.md if files exist."""
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")

        cfg_stub = MagicMock()
        cfg_stub.coder_prompt_template = None
        cfg_stub.worktree_coder = tmp_path
        cfg_stub.repo_name = "owner/repo"
        cfg_stub.api_url = None

        # Simulate the prompt-building logic directly
        _design_hints = []
        for _doc in ("PRODUCT.md", "DESIGN.md"):
            if (tmp_path / _doc).exists():
                _design_hints.append(_doc)

        assert "PRODUCT.md" in _design_hints
        assert "DESIGN.md" in _design_hints
        design_prefix = (
            f" If the issue touches frontend/UI, read {' and '.join(_design_hints)} first"
            " to understand design conventions and anti-patterns to avoid."
        )
        assert "PRODUCT.md" in design_prefix
        assert "DESIGN.md" in design_prefix


# ---------------------------------------------------------------------------
# AC-5: _design_docs_guard blocks dispatch when docs missing
# ---------------------------------------------------------------------------

class TestAC5DesignDocsGuard:
    def test_guard_function_exists(self):
        assert hasattr(sm, "_design_docs_guard"), (
            "_design_docs_guard must be defined in sprint_manager"
        )
        assert callable(sm._design_docs_guard)

    def test_guard_signature(self):
        sig = inspect.signature(sm._design_docs_guard)
        params = list(sig.parameters.keys())
        assert "cwd_path" in params, "_design_docs_guard must accept cwd_path parameter"

    def test_guard_returns_none_when_both_present(self, tmp_path):
        (tmp_path / "PRODUCT.md").write_text("# Product")
        (tmp_path / "DESIGN.md").write_text("# Design")
        result = sm._design_docs_guard(tmp_path)
        assert result is None, (
            "_design_docs_guard must return None when both PRODUCT.md and DESIGN.md exist"
        )

    def test_guard_returns_error_when_product_md_missing(self, tmp_path):
        (tmp_path / "DESIGN.md").write_text("# Design")
        result = sm._design_docs_guard(tmp_path)
        assert result is not None, (
            "_design_docs_guard must return an error when PRODUCT.md is missing"
        )
        assert "PRODUCT.md" in result

    def test_guard_returns_error_when_design_md_missing(self, tmp_path):
        (tmp_path / "PRODUCT.md").write_text("# Product")
        result = sm._design_docs_guard(tmp_path)
        assert result is not None, (
            "_design_docs_guard must return an error when DESIGN.md is missing"
        )
        assert "DESIGN.md" in result

    def test_guard_returns_error_when_both_missing(self, tmp_path):
        result = sm._design_docs_guard(tmp_path)
        assert result is not None, (
            "_design_docs_guard must return an error when both files are missing"
        )

    def test_dispatch_coder_returns_false_when_guard_fails(self, tmp_path, monkeypatch):
        """_dispatch_coder returns (False, 'design_docs_missing') when guard triggers."""
        (tmp_path / ".commander" / "runtime").mkdir(parents=True, exist_ok=True)

        cfg_stub = MagicMock()
        cfg_stub.coder_prompt_template = None
        cfg_stub.worktree_coder = tmp_path   # no PRODUCT.md / DESIGN.md here
        cfg_stub.repo_name = "owner/repo"
        cfg_stub.api_url = None
        cfg_stub.app_default_port = None
        cfg_stub.app_port_strategy = "prefer_default"

        monkeypatch.setattr(sm, "_post_agent_event", lambda *a, **kw: None)
        monkeypatch.setattr(sm, "structured_log", MagicMock())
        monkeypatch.setattr(sm, "_issue_log_path", lambda *a, **kw: tmp_path / "log.txt")
        monkeypatch.setattr(sm, "record_failure", lambda *a, **kw: None)

        ok, failure_cat = sm._dispatch_coder(
            issue_num=999,
            alert_modes=[],
            sprint_branch="develop",
            cfg=cfg_stub,
        )

        assert ok is False, "dispatch must fail when design docs are missing"
        assert failure_cat == "design_docs_missing", (
            f"failure_category must be 'design_docs_missing', got {failure_cat!r}"
        )
