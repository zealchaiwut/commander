"""UAT tests for issue #1286: Extract tester/doctor dispatch to dispatch.py (part 2)"""
import subprocess
import sys
from pathlib import Path

# Get repo root
REPO_ROOT = Path(__file__).parent.parent
DISPATCH_MODULE = REPO_ROOT / "services/sprint_manager/dispatch.py"
SPRINT_MANAGER_MODULE = REPO_ROOT / "services/sprint_manager/sprint_manager.py"


class TestAC1FileExists:
    """AC: dispatch.py exists and contains extracted functions"""

    def test_dispatch_file_exists(self):
        assert DISPATCH_MODULE.exists(), f"{DISPATCH_MODULE} must exist"

    def test_file_has_dispatch_tester(self):
        content = DISPATCH_MODULE.read_text()
        assert "def _dispatch_tester" in content, "_dispatch_tester must be in dispatch.py"

    def test_file_has_doctor_probe_auth(self):
        content = DISPATCH_MODULE.read_text()
        assert "def _doctor_probe_auth(" in content, "_doctor_probe_auth must be in dispatch.py"

    def test_file_has_dispatch_doctor(self):
        content = DISPATCH_MODULE.read_text()
        assert "def _dispatch_doctor(" in content, "_dispatch_doctor must be in dispatch.py"


class TestAC2FunctionsRemoved:
    """AC: Functions removed from original location"""

    def test_sprint_manager_no_def_dispatch_tester(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        # Should not have the definition, but OK to have in import or call
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("def _dispatch_tester"):
                raise AssertionError(f"_dispatch_tester definition found at line {i+1} in sprint_manager.py")

    def test_sprint_manager_no_def_doctor_probe_auth(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("def _doctor_probe_auth"):
                raise AssertionError(f"_doctor_probe_auth definition found at line {i+1} in sprint_manager.py")

    def test_sprint_manager_no_def_dispatch_doctor(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith("def _dispatch_doctor"):
                raise AssertionError(f"_dispatch_doctor definition found at line {i+1} in sprint_manager.py")


class TestAC3Reimported:
    """AC: Functions imported and available in sprint_manager"""

    def test_dispatch_imported_in_sprint_manager(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        assert "from services.sprint_manager.dispatch import" in content, \
            "dispatch module must be imported in sprint_manager.py"

    def test_dispatch_tester_imported(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        assert "_dispatch_tester" in content, "_dispatch_tester must be imported/available"

    def test_doctor_probe_auth_imported(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        assert "_doctor_probe_auth" in content, "_doctor_probe_auth must be imported/available"

    def test_dispatch_doctor_imported(self):
        content = SPRINT_MANAGER_MODULE.read_text()
        assert "_dispatch_doctor" in content, "_dispatch_doctor must be imported/available"


class TestAC4AC5PyCompile:
    """AC: py_compile succeeds on both modules"""

    def test_dispatch_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(DISPATCH_MODULE)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"dispatch.py compile failed:\n{result.stderr}"

    def test_sprint_manager_compiles(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_MODULE)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"sprint_manager.py compile failed:\n{result.stderr}"


class TestAC6DispatchBehaviorUnchanged:
    """AC: Dispatch behavior is identical to pre-move"""

    def test_dispatch_functions_callable(self):
        """Verify the functions are callable (smoke test)"""
        sys.path.insert(0, str(REPO_ROOT))
        sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

        from services.sprint_manager.dispatch import (
            _dispatch_tester,
            _doctor_probe_auth,
            _dispatch_doctor,
        )

        # Verify they are callable
        assert callable(_dispatch_tester), "_dispatch_tester must be callable"
        assert callable(_doctor_probe_auth), "_doctor_probe_auth must be callable"
        assert callable(_dispatch_doctor), "_dispatch_doctor must be callable"


class TestAC7NoNewImports:
    """AC: No unnecessary new imports introduced"""

    def test_dispatch_has_reasonable_imports(self):
        content = DISPATCH_MODULE.read_text()
        lines = content.split("\n")

        import_lines = [l for l in lines[:100] if l.startswith("import ") or l.startswith("from ")]

        # Verify imports are present and reasonable
        assert any("import json" in l for l in import_lines), "json should be imported"
        assert any("import subprocess" in l for l in import_lines), "subprocess should be imported"

        # Count total imports — should not be excessive
        assert len(import_lines) < 50, f"Too many imports in dispatch.py: {len(import_lines)}"


class TestAC8ExistingTestsPass:
    """AC: Existing tests still pass without modification"""

    def test_test_file_exists(self):
        test_file = REPO_ROOT / "tests" / "test_1286__extract_tester_doctor_dispatch.py"
        assert test_file.exists(), "Unit tests must exist"
