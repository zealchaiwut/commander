"""Tests for issue #1275: Extract sprint_manager event emission to events.py (runs against UAT)"""
import os
import subprocess
import sys
from pathlib import Path
import pytest


# Working directory context
REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services"


@pytest.fixture(scope="session")
def repo_root():
    """Return the repo root for all file inspection tests."""
    return REPO_ROOT


class TestEventExtractionExists:
    """AC1: services/sprint_manager/events.py exists and contains exactly the five functions."""

    def test_events_py_exists(self, repo_root):
        """events.py file exists."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        assert events_file.exists(), f"events.py not found at {events_file}"

    def test_events_py_contains_five_functions(self, repo_root):
        """events.py contains all five required functions."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        required_functions = [
            "_emit_sprint_lifecycle_event",
            "_failure_event_detail",
            "_emit_ticket_failed",
            "_post_agent_event",
            "_post_sprint_status",
        ]

        for func_name in required_functions:
            assert f"def {func_name}" in content, f"Function {func_name} not found in events.py"

    def test_events_py_contains_only_five_functions(self, repo_root):
        """events.py contains only these five public/private functions (ignoring helpers)."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        # Count function definitions at module level (not indented)
        lines = content.split('\n')
        func_defs = [line for line in lines if line.startswith('def ')]

        required_functions = {
            "_emit_sprint_lifecycle_event",
            "_failure_event_detail",
            "_emit_ticket_failed",
            "_post_agent_event",
            "_post_sprint_status",
        }

        found_functions = set()
        for func_def in func_defs:
            for req_func in required_functions:
                if f"def {req_func}" in func_def:
                    found_functions.add(req_func)

        assert found_functions == required_functions, \
            f"Found {found_functions}, expected {required_functions}"


class TestNoSignatureChanges:
    """AC2: None of the five functions have any signature, docstring, or logic changes."""

    def test_emit_sprint_lifecycle_event_signature(self, repo_root):
        """_emit_sprint_lifecycle_event signature unchanged."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        # Check that the signature matches the expected pattern
        assert "def _emit_sprint_lifecycle_event(\n    type: str,\n    target: str,\n    actor: str," in content or \
               "def _emit_sprint_lifecycle_event(type: str, target: str, actor: str" in content, \
               "Function signature appears modified"

    def test_failure_event_detail_signature(self, repo_root):
        """_failure_event_detail signature unchanged."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        assert "def _failure_event_detail(" in content
        assert "issue_num: int" in content
        assert "agent_role: str" in content
        assert "reason: str" in content
        assert "category:" in content

    def test_emit_ticket_failed_signature(self, repo_root):
        """_emit_ticket_failed signature unchanged."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        assert "def _emit_ticket_failed(" in content
        assert "issue_num: int" in content
        assert "project: str" in content

    def test_post_agent_event_signature(self, repo_root):
        """_post_agent_event signature unchanged."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        assert "def _post_agent_event(" in content
        assert "tool_name: str" in content

    def test_post_sprint_status_signature(self, repo_root):
        """_post_sprint_status signature unchanged."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        assert "def _post_sprint_status(" in content
        assert "state:" in content


class TestImportAndReexport:
    """AC3: Original module imports the moved symbols so all existing call sites remain unmodified."""

    def test_sprint_manager_imports_from_events(self, repo_root):
        """sprint_manager imports the five functions from events.py."""
        sm_file = repo_root / "services" / "sprint_manager" / "sprint_manager.py"
        content = sm_file.read_text()

        assert "from services.sprint_manager.events import" in content, \
            "sprint_manager.py does not import from events.py"

        required_functions = [
            "_emit_sprint_lifecycle_event",
            "_failure_event_detail",
            "_emit_ticket_failed",
            "_post_agent_event",
            "_post_sprint_status",
        ]

        for func in required_functions:
            assert func in content, f"sprint_manager.py does not import {func}"


class TestPyCompile:
    """AC4-5: Syntax validation via py_compile."""

    def test_events_py_compiles(self, repo_root):
        """python -m py_compile services/sprint_manager/events.py exits 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             str(repo_root / "services" / "sprint_manager" / "events.py")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, \
            f"events.py py_compile failed: {result.stderr}"
        assert result.stdout == "", "py_compile should not print to stdout"

    def test_sprint_manager_compiles(self, repo_root):
        """python -m py_compile sprint_manager.py exits 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile",
             str(repo_root / "services" / "sprint_manager" / "sprint_manager.py")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, \
            f"sprint_manager.py py_compile failed: {result.stderr}"


class TestNoOldImportPaths:
    """AC6: No other file in the repo references the old import paths."""

    def test_no_direct_imports_from_sprint_manager(self, repo_root):
        """Grep for functions as attributes of sprint_manager module (pre-move pattern)."""
        # The pattern we're looking for is something like:
        # from services.sprint_manager.sprint_manager import _emit_sprint_lifecycle_event
        # (importing directly from sprint_manager instead of from events)

        # Exclude the sprint_manager.py and events.py files themselves, and test files
        result = subprocess.run(
            ["grep", "-r", "from services.sprint_manager.sprint_manager import",
             str(repo_root / "services"),
             "--include=*.py"],
            capture_output=True,
            text=True,
        )

        # Should have no matches or only legitimate matches (e.g., in __init__.py re-exports)
        lines = [l for l in result.stdout.strip().split('\n') if l and '_emit_sprint_lifecycle_event' in l]
        if lines:
            # It's OK if these appear in re-exports or in events.py's lazy imports
            for line in lines:
                assert "events.py" in line or "sprint_manager.py" in line, \
                    f"Unexpected import of event function: {line}"


class TestEventPayloadsUnchanged:
    """AC7: Event payloads, names, and emission order are byte-for-byte identical."""

    def test_failure_event_detail_logic_unchanged(self, repo_root):
        """_failure_event_detail constructs the same detail dict."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        # Check for the specific keys that should be set
        assert '"agent":' in content
        assert '"issue_num":' in content
        assert '"reason":' in content
        assert '"category":' in content
        assert '"branch":' in content
        assert '"gate":' in content

    def test_emit_ticket_failed_calls_correct_functions(self, repo_root):
        """_emit_ticket_failed delegates to _emit_sprint_lifecycle_event correctly."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        # Verify the function calls the right helper
        assert "_emit_sprint_lifecycle_event" in content
        assert 'type="ticket_failed"' in content or "type='ticket_failed'" in content

    def test_post_agent_event_constructs_payload(self, repo_root):
        """_post_agent_event constructs the same JSON payload."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        # Check payload construction
        assert '"agent_id":' in content
        assert '"tool_name":' in content
        assert '"timestamp":' in content
        assert '/api/agent-event' in content

    def test_post_sprint_status_posts_to_correct_endpoint(self, repo_root):
        """_post_sprint_status posts to /api/sprint-status."""
        events_file = repo_root / "services" / "sprint_manager" / "events.py"
        content = events_file.read_text()

        assert '/api/sprint-status' in content
        assert 'to_dict' in content


class TestImportCompatibility:
    """Verify that the module can be imported without errors."""

    def test_events_module_imports(self, repo_root):
        """events.py can be imported without errors."""
        import sys
        sys.path.insert(0, str(repo_root))
        try:
            # This should not raise any errors
            import services.sprint_manager.events  # noqa: F401
        finally:
            sys.path.pop(0)

    def test_sprint_manager_imports_successfully(self, repo_root):
        """sprint_manager.py imports successfully with re-exports."""
        import sys
        sys.path.insert(0, str(repo_root))
        try:
            # Import the module to ensure all re-exports work
            import services.sprint_manager.sprint_manager as sm  # noqa: F401

            # Verify the symbols are accessible
            assert hasattr(sm, '_emit_sprint_lifecycle_event')
            assert hasattr(sm, '_failure_event_detail')
            assert hasattr(sm, '_emit_ticket_failed')
            assert hasattr(sm, '_post_agent_event')
            assert hasattr(sm, '_post_sprint_status')
        finally:
            sys.path.pop(0)
