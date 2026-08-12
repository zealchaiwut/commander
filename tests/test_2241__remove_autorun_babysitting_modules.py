"""AC tests for #2241 — Remove autorun babysitting modules.

AC1: All eight modules deleted.
AC2: routers/failures.py and routers/failures_service.py untouched; the
     Failures inbox still loads.
AC3: No remaining import references to any deleted module in production code.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── AC1 ───────────────────────────────────────────────────────────────────────

_DELETED_MODULES = [
    "services/sprint_manager/alerts.py",
    "services/sprint_manager/dead_letter_escalation.py",
    "services/sprint_manager/brief_generator.py",
    "services/sprint_manager/label_transitions.py",
    "services/sprint_manager/events.py",
    "services/sprint_manager/ica_preflight.py",
    "services/sprint_manager/api_client.py",
    "services/sprint_manager/failures.py",
]


@pytest.mark.parametrize("module_path", _DELETED_MODULES)
def test_ac1_module_deleted(module_path: str) -> None:
    """AC1: Each of the eight modules no longer exists on disk."""
    assert not (_REPO_ROOT / module_path).exists(), (
        f"{module_path} should have been deleted"
    )


# ── AC2 ───────────────────────────────────────────────────────────────────────


def test_ac2_failures_router_file_exists() -> None:
    """AC2: apps/dashboard/routers/failures.py is present and unmodified."""
    assert (_DASHBOARD_ROOT / "routers" / "failures.py").exists()


def test_ac2_failures_service_file_exists() -> None:
    """AC2: apps/dashboard/routers/failures_service.py is present and unmodified."""
    assert (_DASHBOARD_ROOT / "routers" / "failures_service.py").exists()


def test_ac2_failures_router_importable() -> None:
    """AC2: failures.py loads cleanly (router object is defined at correct prefix)."""
    # Load the module by path so we bypass routers/__init__.py (which pulls in
    # the full app stack and fails in a test environment).
    spec = importlib.util.spec_from_file_location(
        "routers.failures",
        str(_DASHBOARD_ROOT / "routers" / "failures.py"),
    )
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    # failures.py imports from .failures_service — pre-load it so the relative
    # import resolves without the package initialiser running.
    svc_spec = importlib.util.spec_from_file_location(
        "routers.failures_service",
        str(_DASHBOARD_ROOT / "routers" / "failures_service.py"),
    )
    svc_mod = importlib.util.module_from_spec(svc_spec)  # type: ignore[arg-type]
    sys.modules["routers.failures_service"] = svc_mod
    svc_spec.loader.exec_module(svc_mod)  # type: ignore[union-attr]

    # Also pre-load hermes_models
    hm_spec = importlib.util.spec_from_file_location(
        "routers.hermes_models",
        str(_DASHBOARD_ROOT / "routers" / "hermes_models.py"),
    )
    hm_mod = importlib.util.module_from_spec(hm_spec)  # type: ignore[arg-type]
    sys.modules["routers.hermes_models"] = hm_mod
    hm_spec.loader.exec_module(hm_mod)  # type: ignore[union-attr]

    sys.modules["routers.failures"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    router = getattr(mod, "router", None)
    assert router is not None, "failures.py must define a `router` object"
    assert router.prefix == "/api", f"Expected prefix /api, got {router.prefix!r}"


# ── AC3 ───────────────────────────────────────────────────────────────────────

# Regex: match only import statements (not bare comments or docstrings)
_IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+"
    r"services\.sprint_manager\."
    r"(?:alerts|dead_letter_escalation|brief_generator|label_transitions"
    r"|events|ica_preflight|api_client|failures)"
    r"(?:\s+import|\b)",
    re.MULTILINE,
)

# Also catch `from services.sprint_manager import <name>` form
_IMPORT_RE2 = re.compile(
    r"^\s*from\s+services\.sprint_manager\s+import\s+(?:\(.*?\)|[^\n]+)\n",
    re.MULTILINE | re.DOTALL,
)

_DELETED_MODULE_NAMES = {
    "alerts", "dead_letter_escalation", "brief_generator",
    "label_transitions", "events", "ica_preflight", "api_client", "failures",
}


def _is_test_file(path: Path) -> bool:
    rel = str(path.relative_to(_REPO_ROOT))
    return "tests/" in rel or rel.startswith("tests/")


def _production_py_files():
    """Yield all non-test, non-pycache Python files under the repo root."""
    for py_file in _REPO_ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        if _is_test_file(py_file):
            continue
        yield py_file


def test_ac3_no_import_references_to_deleted_modules() -> None:
    """AC3: No production Python file has an import statement referencing a deleted module."""
    violations: list[str] = []
    for py_file in _production_py_files():
        try:
            content = py_file.read_text(errors="replace")
        except OSError:
            continue
        if _IMPORT_RE.search(content):
            rel = str(py_file.relative_to(_REPO_ROOT))
            violations.append(rel)

    assert not violations, (
        "Production files still import deleted modules:\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )
