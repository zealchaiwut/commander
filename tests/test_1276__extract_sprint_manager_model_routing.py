"""Tests for issue #1276: Extract sprint_manager model routing to model_routing.py.

AC-1: model_routing.py exists and contains _resolve_coder_model, _effective_coder_backend,
      _select_coder_backend, and all size/risk routing logic
AC-2: Original module imports from model_routing.py; no logic duplicated across files
AC-3: Model selection output is identical before/after for all input combinations
AC-4: python -m py_compile services/sprint_manager/model_routing.py exits 0
AC-5: python -m py_compile on the original sprint_manager module exits 0
AC-6: No other module outside sprint_manager imports moved symbols from old path
AC-7: All existing tests pass without modification
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

import os
os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── AC-1: model_routing.py exists and contains required functions ──────────────

class TestModelRoutingModuleExists:
    """AC-1: model_routing.py exists and exports the required functions/constants."""

    def test_model_routing_module_is_importable(self):
        from services.sprint_manager import model_routing  # noqa: F401

    def test_model_routing_has_resolve_coder_model(self):
        from services.sprint_manager.model_routing import _resolve_coder_model
        assert callable(_resolve_coder_model)

    def test_model_routing_has_effective_coder_backend(self):
        from services.sprint_manager.model_routing import _effective_coder_backend
        assert callable(_effective_coder_backend)

    def test_model_routing_has_select_coder_backend(self):
        from services.sprint_manager.model_routing import _select_coder_backend
        assert callable(_select_coder_backend)

    def test_model_routing_has_is_docs_only(self):
        from services.sprint_manager.model_routing import _is_docs_only
        assert callable(_is_docs_only)

    def test_model_routing_has_default_coder_by_size(self):
        from services.sprint_manager.model_routing import _DEFAULT_CODER_BY_SIZE
        assert isinstance(_DEFAULT_CODER_BY_SIZE, dict)
        assert set(_DEFAULT_CODER_BY_SIZE.keys()) >= {"S", "M", "L", "XL"}

    def test_model_routing_has_docs_path_extensions(self):
        from services.sprint_manager.model_routing import _DOCS_PATH_EXTENSIONS
        assert isinstance(_DOCS_PATH_EXTENSIONS, frozenset)
        assert ".md" in _DOCS_PATH_EXTENSIONS

    def test_model_routing_has_code_path_extensions(self):
        from services.sprint_manager.model_routing import _CODE_PATH_EXTENSIONS
        assert isinstance(_CODE_PATH_EXTENSIONS, frozenset)
        assert ".py" in _CODE_PATH_EXTENSIONS


# ── AC-2: Signatures are identical (pure move) ────────────────────────────────

class TestFunctionSignatures:
    """AC-2: No signature changes — pure move."""

    def test_resolve_coder_model_signature(self):
        from services.sprint_manager.model_routing import _resolve_coder_model
        sig = inspect.signature(_resolve_coder_model)
        params = list(sig.parameters.keys())
        assert params == ["issue_num", "cfg", "estimate"]
        assert sig.parameters["estimate"].default is None

    def test_effective_coder_backend_signature(self):
        from services.sprint_manager.model_routing import _effective_coder_backend
        sig = inspect.signature(_effective_coder_backend)
        params = list(sig.parameters.keys())
        assert params == ["sprint_label", "cfg", "prior_failures"]

    def test_select_coder_backend_signature(self):
        from services.sprint_manager.model_routing import _select_coder_backend
        sig = inspect.signature(_select_coder_backend)
        params = list(sig.parameters.keys())
        assert params == ["issue_num", "cfg", "repo_name"]
        assert sig.parameters["repo_name"].default is None


# ── AC-2: Original module imports from model_routing.py ───────────────────────

class TestSprintManagerReExports:
    """AC-2: sprint_manager re-exports the moved symbols so call sites work unmodified."""

    def test_sm_resolve_coder_model_is_model_routing_version(self):
        from services.sprint_manager.model_routing import _resolve_coder_model
        assert sm._resolve_coder_model is _resolve_coder_model

    def test_sm_effective_coder_backend_is_model_routing_version(self):
        from services.sprint_manager.model_routing import _effective_coder_backend
        assert sm._effective_coder_backend is _effective_coder_backend

    def test_sm_select_coder_backend_is_model_routing_version(self):
        from services.sprint_manager.model_routing import _select_coder_backend
        assert sm._select_coder_backend is _select_coder_backend

    def test_sm_is_docs_only_is_model_routing_version(self):
        from services.sprint_manager.model_routing import _is_docs_only
        assert sm._is_docs_only is _is_docs_only


# ── AC-3: Model selection output identical for all input combinations ──────────

class TestRoutingOutputIdentical:
    """AC-3: routing output is identical to pre-refactor behavior for all inputs."""

    @pytest.fixture
    def minimal_cfg(self, tmp_path):
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()
        tester_dir = tmp_path / "tester"
        tester_dir.mkdir()
        yaml_text = textwrap.dedent(f"""
            repo_name: test/repo
            worktrees:
              coder: {coder_dir}
              tester: {tester_dir}
        """)
        config_path = tmp_path / "sprint.yaml"
        config_path.write_text(yaml_text)
        return sm.load_config(config_path)

    def test_size_s_routes_haiku(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate={"size": "S"})
        assert "haiku" in model.lower()
        assert reason == "size=S"

    def test_size_m_routes_sonnet(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate={"size": "M"})
        assert "sonnet" in model.lower()
        assert reason == "size=M"

    def test_size_l_routes_sonnet(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate={"size": "L"})
        assert "sonnet" in model.lower()
        assert reason == "size=L"

    def test_size_xl_routes_sonnet(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate={"size": "XL"})
        assert "sonnet" in model.lower()
        assert reason == "size=XL"

    def test_no_estimate_returns_default(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate=None)
        assert reason == "unestimated:default"

    def test_docs_only_flag_routes_haiku(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        estimate = {"size": "M", "risk_flags": ["docs-only"]}
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate=estimate)
        assert "haiku" in model.lower()
        assert reason == "docs-only:flag"

    def test_docs_only_paths_routes_haiku(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        estimate = {"size": "M", "files_likely_affected": ["docs/workflow.md", "CHANGELOG.md"]}
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate=estimate)
        assert "haiku" in model.lower()
        assert reason == "docs-only:paths"

    def test_xl_docs_only_routes_sonnet_not_haiku(self, minimal_cfg):
        from services.sprint_manager.model_routing import _resolve_coder_model
        estimate = {"size": "XL", "risk_flags": ["docs-only"]}
        model, reason = _resolve_coder_model(1, minimal_cfg, estimate=estimate)
        assert reason == "size=XL", "XL tickets must not be rerouted to Haiku by docs-only"

    def test_none_cfg_uses_fallback_default(self):
        from services.sprint_manager.model_routing import _resolve_coder_model
        model, reason = _resolve_coder_model(1, None, estimate=None)
        assert reason == "unestimated:default"
        assert "sonnet" in model.lower()

    def test_effective_coder_backend_no_failures_returns_base(self, minimal_cfg):
        from services.sprint_manager.model_routing import _effective_coder_backend
        backend = _effective_coder_backend(None, minimal_cfg, prior_failures=None)
        assert backend == minimal_cfg.coder_backend

    def test_effective_coder_backend_none_cfg_returns_claude_code(self):
        from services.sprint_manager.model_routing import _effective_coder_backend
        backend = _effective_coder_backend(None, None, prior_failures=None)
        assert backend == "claude-code"

    def test_effective_coder_backend_prior_failures_no_cline_flag(self, minimal_cfg):
        from services.sprint_manager.model_routing import _effective_coder_backend
        # use_cline_followups is False by default, so even with prior failures
        # should return base backend
        backend = _effective_coder_backend("sprint-1", minimal_cfg, prior_failures=["fail1"])
        assert backend == minimal_cfg.coder_backend

    def test_is_docs_only_flag(self):
        from services.sprint_manager.model_routing import _is_docs_only
        result, reason = _is_docs_only({"risk_flags": ["docs-only"]})
        assert result is True
        assert reason == "docs-only:flag"

    def test_is_docs_only_paths(self):
        from services.sprint_manager.model_routing import _is_docs_only
        result, reason = _is_docs_only({"files_likely_affected": ["docs/foo.md", "README.md"]})
        assert result is True
        assert reason == "docs-only:paths"

    def test_is_docs_only_mixed_paths_is_false(self):
        from services.sprint_manager.model_routing import _is_docs_only
        result, reason = _is_docs_only({"files_likely_affected": ["server.py", "docs/foo.md"]})
        assert result is False
        assert reason == ""

    def test_is_docs_only_none_estimate_is_false(self):
        from services.sprint_manager.model_routing import _is_docs_only
        result, reason = _is_docs_only(None)
        assert result is False


# ── AC-4: py_compile model_routing.py exits 0 ────────────────────────────────

class TestPyCompileModelRouting:
    """AC-4: model_routing.py has no syntax errors."""

    def test_py_compile_model_routing_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/model_routing.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_py_compile_model_routing_no_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/model_routing.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout == ""
        assert result.stderr == ""


# ── AC-5: py_compile sprint_manager module exits 0 ───────────────────────────

class TestPyCompileSprintManager:
    """AC-5: sprint_manager.py has no syntax errors after the refactor."""

    def test_py_compile_sprint_manager_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/sprint_manager.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_py_compile_sprint_manager_no_output(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             "services/sprint_manager/sprint_manager.py"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.stdout == ""
        assert result.stderr == ""


# ── AC-6: No outside module imports moved symbols from old path ───────────────

class TestNoExternalDirectImports:
    """AC-6: Code outside services/sprint_manager does not directly import the moved symbols
    from sprint_manager.py (or if it does, a re-export shim covers it)."""

    def _grep_outside_imports(self, symbol: str) -> list[str]:
        """Grep for direct sprint_manager imports of a symbol outside sprint_manager/."""
        import subprocess as _sp
        result = _sp.run(
            ["grep", "-r", "--include=*.py", "-l",
             f"from services.sprint_manager.sprint_manager import.*{symbol}"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        hits = [
            line for line in result.stdout.splitlines()
            if "services/sprint_manager" not in line
        ]
        return hits

    def test_no_outside_direct_import_resolve_coder_model(self):
        hits = self._grep_outside_imports("_resolve_coder_model")
        assert hits == [], f"Outside modules import _resolve_coder_model from sprint_manager: {hits}"

    def test_no_outside_direct_import_effective_coder_backend(self):
        hits = self._grep_outside_imports("_effective_coder_backend")
        assert hits == [], (
            f"Outside modules import _effective_coder_backend from sprint_manager: {hits}"
        )

    def test_no_outside_direct_import_select_coder_backend(self):
        hits = self._grep_outside_imports("_select_coder_backend")
        assert hits == [], (
            f"Outside modules import _select_coder_backend from sprint_manager: {hits}"
        )
