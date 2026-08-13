"""Tests for issue #2249 — Delete the sprint orchestrator.

AC coverage:
  AC1  All eleven listed modules deleted from services/sprint_manager/
  AC2  state_machine.py, agent_browser_runner.py, reconciliation.py,
       paths.py, commander_paths.py, summary.py retained
  AC3  model_routing.py retained with only role-dispatch portion removed;
       apply_provider_env still callable and works correctly
  AC4  No retained dashboard entrypoint has a module-level hard import of
       any deleted module (dashboard starts clean)
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(SM_DIR))

# Stub heavy deps before any dashboard import
if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()

# ── constants ─────────────────────────────────────────────────────────────────

DELETED_MODULES = [
    "sprint_manager.py",
    "dispatch.py",
    "gates.py",
    "pipeline.py",
    "worktree.py",
    "worktree_pool.py",
    "concurrent_scheduler.py",
    "serialization.py",
    "timekeeping.py",
    "state.py",
    "config.py",
]

RETAINED_FILES = [
    "state_machine.py",
    "agent_browser_runner.py",
    "reconciliation.py",
    "paths.py",
    "commander_paths.py",
    "summary.py",
    "model_routing.py",
]

# Role-dispatch symbols that must NOT survive in model_routing.py
DISPATCH_ONLY_SYMBOLS = {
    "get_role_profile",
    "_plan_json_use_cline_followups",
    "_effective_coder_backend",
    "_is_docs_only",
    "_resolve_coder_model",
    "_resolve_cline_model",
    "_select_coder_backend",
    "ica_lean_cli_args",
    "_DEFAULT_CODER_BY_SIZE",
    "_DOCS_PATH_EXTENSIONS",
    "_CODE_PATH_EXTENSIONS",
}

# Symbols that must remain in model_routing.py
KEPT_SYMBOLS = {
    "apply_provider_env",
    "get_effective_llm_provider",
    "apply_ica_agent_env",
    "ICA_FORCED_MODEL",
    "_ica_allowed_roles",
    "_plan_json_llm_provider",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _top_level_imports(src: str) -> list[str]:
    """Return module names for hard imports at module scope only (not inside functions
    or try blocks), excluding TYPE_CHECKING if-blocks.

    Function-level lazy imports (inside try/except or function bodies) are not
    included; they only run when the function is called, not at module load time.
    """
    tree = ast.parse(src)
    modules: list[str] = []
    for node in ast.iter_child_nodes(tree):
        # Skip TYPE_CHECKING if-blocks at module level
        if isinstance(node, ast.If):
            test = node.test
            is_tc = (
                (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
                or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
            )
            if is_tc:
                continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def _defined_names(src: str) -> set[str]:
    """Return all top-level function/class/variable names defined in src."""
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


# ── AC1: All eleven modules deleted ──────────────────────────────────────────

@pytest.mark.parametrize("filename", DELETED_MODULES)
def test_deleted_module_absent(filename: str):
    """AC1: The file must not exist after the orchestrator is deleted."""
    path = SM_DIR / filename
    assert not path.exists(), (
        f"services/sprint_manager/{filename} still exists — must be deleted per AC1"
    )


# ── AC2: Retained files exist ─────────────────────────────────────────────────

@pytest.mark.parametrize("filename", RETAINED_FILES)
def test_retained_file_present(filename: str):
    """AC2/AC3: The file must be present after deletion."""
    path = SM_DIR / filename
    assert path.exists(), (
        f"services/sprint_manager/{filename} missing — must be retained per AC2/AC3"
    )


# ── AC3: model_routing.py stripped of role-dispatch, keeps shared exports ────

def test_model_routing_importable():
    """AC3: model_routing.py imports without error (no dep on deleted config.py)."""
    # Remove cached module so the fresh source is loaded
    for key in list(sys.modules):
        if "model_routing" in key:
            del sys.modules[key]
    try:
        import services.sprint_manager.model_routing as mr  # noqa: F401
    except ImportError as exc:
        pytest.fail(f"model_routing.py fails to import: {exc}")


def test_model_routing_dispatch_symbols_absent():
    """AC3: Role-dispatch only symbols must not exist in model_routing.py."""
    src = (SM_DIR / "model_routing.py").read_text(encoding="utf-8")
    defined = _defined_names(src)
    survivors = DISPATCH_ONLY_SYMBOLS & defined
    assert not survivors, (
        f"Role-dispatch symbols still present in model_routing.py: {survivors}"
    )


def test_model_routing_kept_symbols_present():
    """AC3: Shared symbols must still be defined in model_routing.py."""
    src = (SM_DIR / "model_routing.py").read_text(encoding="utf-8")
    defined = _defined_names(src)
    missing = KEPT_SYMBOLS - defined
    assert not missing, (
        f"Expected shared symbols missing from model_routing.py: {missing}"
    )


def test_apply_provider_env_returns_model_for_anthropic(monkeypatch):
    """AC3: apply_provider_env returns the passed model when provider is anthropic."""
    for key in list(sys.modules):
        if "model_routing" in key:
            del sys.modules[key]

    import services.sprint_manager.model_routing as mr

    env: dict = {}
    model = mr.apply_provider_env(env, "claude-sonnet-4-6", repo="test/repo", role="ba")
    assert model == "claude-sonnet-4-6"
    # No ICA env vars should be injected for the anthropic provider
    assert "ANTHROPIC_BASE_URL" not in env


def test_model_routing_no_config_import():
    """AC3: model_routing.py must not import from config.py at module level."""
    src = (SM_DIR / "model_routing.py").read_text(encoding="utf-8")
    imports = _top_level_imports(src)
    bad = [m for m in imports if "sprint_manager.config" in m or m == "config"]
    assert not bad, (
        f"model_routing.py has module-level import of deleted config.py: {bad}"
    )


# ── AC4: No retained entrypoint hard-imports a deleted module ────────────────

_DELETED_MODULE_QUALNAMES = {
    f"services.sprint_manager.{stem}" for stem in [m.removesuffix(".py") for m in DELETED_MODULES]
}
_DELETED_MODULE_QUALNAMES |= {m.removesuffix(".py") for m in DELETED_MODULES}


def _entrypoint_imports_deleted(filepath: Path) -> list[str]:
    """Return list of hard (non-TYPE_CHECKING) imports of any deleted module."""
    src = filepath.read_text(encoding="utf-8")
    imports = _top_level_imports(src)
    return [
        imp for imp in imports
        if any(imp == q or imp.startswith(q + ".") for q in _DELETED_MODULE_QUALNAMES)
    ]


@pytest.mark.parametrize("rel_path", [
    "apps/dashboard/startup.py",
    "apps/dashboard/routers/sprint_finish.py",
    "apps/dashboard/routers/bulk_tickets.py",
    "apps/dashboard/routers/split_xl_service.py",
    "apps/dashboard/routers/brief_summary.py",
    "services/sprint_manager/summary.py",
    "services/sprint_manager/reconciliation.py",
])
def test_no_deleted_module_hard_import(rel_path: str):
    """AC4: Retained entrypoints have no module-level import of any deleted module."""
    path = REPO_ROOT / rel_path
    if not path.exists():
        pytest.skip(f"{rel_path} not found")
    bad = _entrypoint_imports_deleted(path)
    assert not bad, (
        f"{rel_path} has hard import(s) of deleted module(s): {bad}"
    )
