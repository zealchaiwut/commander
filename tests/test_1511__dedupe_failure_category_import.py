"""Tests for #1511: Dedupe FailureCategory — import from failures.py in
pipeline.py and dispatch.py instead of redefining locally.

AC items verified:
  AC-1  pipeline.py does NOT define a local FailureCategory class (AST check)
  AC-2  dispatch.py does NOT define a local FailureCategory class (AST check)
  AC-3  pipeline.py imports FailureCategory from services.sprint_manager.failures
  AC-4  dispatch.py imports FailureCategory from services.sprint_manager.failures
  AC-5  FailureCategory accessible in pipeline is the same object as in failures
  AC-6  FailureCategory accessible in dispatch is the same object as in failures
  AC-7  _LOGIC_FAILURE_CATEGORIES in pipeline.py still references the (imported) class
  AC-8  python -m py_compile on pipeline.py exits 0
  AC-9  python -m py_compile on dispatch.py exits 0
  AC-10 failures.py does not import pipeline or dispatch (remains a leaf module)
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

PIPELINE_PATH = REPO_ROOT / "services" / "sprint_manager" / "pipeline.py"
DISPATCH_PATH = REPO_ROOT / "services" / "sprint_manager" / "dispatch.py"
FAILURES_PATH = REPO_ROOT / "services" / "sprint_manager" / "failures.py"


def _local_class_defs(path: Path) -> list[str]:
    """Return names of all top-level class definitions in file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]


def _imported_names_from(path: Path, module_suffix: str) -> list[str]:
    """Return all names imported from any module ending with module_suffix."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.endswith(module_suffix):
                names.extend(alias.name for alias in node.names)
    return names


def _module_level_imports(path: Path) -> list[str]:
    """Return all module names imported at the top level of file."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    return modules


# ---------------------------------------------------------------------------
# AC-1: pipeline.py must NOT define FailureCategory locally
# ---------------------------------------------------------------------------

class TestAC1NoPipelineLocalClass:
    def test_failure_category_not_defined_locally_in_pipeline(self):
        """pipeline.py must not contain a local class definition of FailureCategory."""
        local_classes = _local_class_defs(PIPELINE_PATH)
        assert "FailureCategory" not in local_classes, (
            "pipeline.py still defines FailureCategory locally — "
            "delete the local class and import from failures.py"
        )


# ---------------------------------------------------------------------------
# AC-2: dispatch.py must NOT define FailureCategory locally
# ---------------------------------------------------------------------------

class TestAC2NoDispatchLocalClass:
    def test_failure_category_not_defined_locally_in_dispatch(self):
        """dispatch.py must not contain a local class definition of FailureCategory."""
        local_classes = _local_class_defs(DISPATCH_PATH)
        assert "FailureCategory" not in local_classes, (
            "dispatch.py still defines FailureCategory locally — "
            "delete the local class and import from failures.py"
        )


# ---------------------------------------------------------------------------
# AC-3: pipeline.py imports FailureCategory from services.sprint_manager.failures
# ---------------------------------------------------------------------------

class TestAC3PipelineImportsFromFailures:
    def test_pipeline_imports_failure_category_from_failures(self):
        """pipeline.py must import FailureCategory from services.sprint_manager.failures."""
        imported = _imported_names_from(PIPELINE_PATH, "failures")
        assert "FailureCategory" in imported, (
            "pipeline.py does not import FailureCategory from "
            "services.sprint_manager.failures"
        )


# ---------------------------------------------------------------------------
# AC-4: dispatch.py imports FailureCategory from services.sprint_manager.failures
# ---------------------------------------------------------------------------

class TestAC4DispatchImportsFromFailures:
    def test_dispatch_imports_failure_category_from_failures(self):
        """dispatch.py must import FailureCategory from services.sprint_manager.failures."""
        imported = _imported_names_from(DISPATCH_PATH, "failures")
        assert "FailureCategory" in imported, (
            "dispatch.py does not import FailureCategory from "
            "services.sprint_manager.failures"
        )


# ---------------------------------------------------------------------------
# AC-5: FailureCategory in pipeline is the canonical one from failures.py
# ---------------------------------------------------------------------------

class TestAC5PipelineUsesCanonicalClass:
    def test_pipeline_failure_category_is_same_object_as_failures(self):
        """FailureCategory in pipeline must be the same class object as in failures."""
        import importlib
        pipeline = importlib.import_module("services.sprint_manager.pipeline")
        failures = importlib.import_module("services.sprint_manager.failures")
        assert pipeline.FailureCategory is failures.FailureCategory, (
            "pipeline.FailureCategory is not the same object as "
            "failures.FailureCategory — the local class was not fully removed"
        )


# ---------------------------------------------------------------------------
# AC-6: FailureCategory in dispatch is the canonical one from failures.py
# ---------------------------------------------------------------------------

class TestAC6DispatchUsesCanonicalClass:
    def test_dispatch_failure_category_is_same_object_as_failures(self):
        """FailureCategory in dispatch must be the same class object as in failures."""
        import importlib
        dispatch = importlib.import_module("services.sprint_manager.dispatch")
        failures = importlib.import_module("services.sprint_manager.failures")
        assert dispatch.FailureCategory is failures.FailureCategory, (
            "dispatch.FailureCategory is not the same object as "
            "failures.FailureCategory — the local class was not fully removed"
        )


# ---------------------------------------------------------------------------
# AC-7: _LOGIC_FAILURE_CATEGORIES in pipeline still references the class
# ---------------------------------------------------------------------------

class TestAC7LogicFailureCategoriesStillWorks:
    def test_logic_failure_categories_present_and_correct(self):
        """_LOGIC_FAILURE_CATEGORIES must still be a frozenset with the expected values."""
        import importlib
        pipeline = importlib.import_module("services.sprint_manager.pipeline")
        lfc = pipeline._LOGIC_FAILURE_CATEGORIES
        assert isinstance(lfc, frozenset), "_LOGIC_FAILURE_CATEGORIES must be a frozenset"
        assert pipeline.FailureCategory.CODER_NO_WORK in lfc
        assert pipeline.FailureCategory.MERGE_CONFLICT in lfc
        assert pipeline.FailureCategory.LINT_FAIL in lfc
        assert pipeline.FailureCategory.PYTEST_FAIL in lfc


# ---------------------------------------------------------------------------
# AC-8: pipeline.py compiles without errors
# ---------------------------------------------------------------------------

class TestAC8PipelineCompiles:
    def test_pipeline_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(PIPELINE_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on pipeline.py:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC-9: dispatch.py compiles without errors
# ---------------------------------------------------------------------------

class TestAC9DispatchCompiles:
    def test_dispatch_py_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(DISPATCH_PATH)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on dispatch.py:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC-10: failures.py does not import pipeline or dispatch (leaf module)
# ---------------------------------------------------------------------------

class TestAC10FailuresIsLeaf:
    def test_failures_does_not_import_pipeline(self):
        """failures.py must not import from pipeline.py."""
        imports = _module_level_imports(FAILURES_PATH)
        for mod in imports:
            assert "pipeline" not in mod, (
                f"failures.py imports from '{mod}' which contains 'pipeline' — "
                "failures.py must remain a leaf module"
            )

    def test_failures_does_not_import_dispatch(self):
        """failures.py must not import from dispatch.py."""
        imports = _module_level_imports(FAILURES_PATH)
        for mod in imports:
            assert "dispatch" not in mod, (
                f"failures.py imports from '{mod}' which contains 'dispatch' — "
                "failures.py must remain a leaf module"
            )

    def test_failures_has_no_circular_import_with_pipeline_or_dispatch(self):
        """failures.py must not import pipeline or dispatch (would be circular)."""
        imports = _module_level_imports(FAILURES_PATH)
        bad = [m for m in imports if "pipeline" in m or "dispatch" in m]
        assert not bad, (
            f"failures.py imports from {bad} — "
            "this would create a circular dependency with the modules that import FailureCategory from failures"
        )
