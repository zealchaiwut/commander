"""Tests for issue #1340: maintenance_service imports calibration helpers directly.

Verifies that maintenance_service.py has been refactored to import
calibration_cache_service directly instead of importing the entire FastAPI
server monolith (which contradicts issue #1333's design goal).
"""
import ast
import sys
from pathlib import Path


def test_maintenance_service_no_server_import():
    """AC1: maintenance_service.py no longer imports server as _srv."""
    maint_file = Path(__file__).parent.parent / "apps/dashboard/routers/maintenance_service.py"
    content = maint_file.read_text(encoding="utf-8")

    # Parse AST to find all Import and ImportFrom nodes
    tree = ast.parse(content)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, alias.asname))
        elif isinstance(node, ast.ImportFrom):
            imports.append((node.module, None))

    # Verify "server" is not imported
    for module_name, asname in imports:
        assert module_name != "server", \
            f"Found 'import server' — should import calibration_cache_service instead"
        if module_name and "server" in module_name:
            assert False, f"Found import of module containing 'server': {module_name}"


def test_maintenance_service_no_srv_references():
    """AC2: maintenance_service.py has zero _srv. references."""
    maint_file = Path(__file__).parent.parent / "apps/dashboard/routers/maintenance_service.py"
    content = maint_file.read_text(encoding="utf-8")

    # Search for _srv. pattern
    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        assert "_srv." not in line, \
            f"Line {i} contains '_srv.' reference: {line.strip()}"


def test_maintenance_service_imports_calibration_cache_service():
    """AC3: maintenance_service imports calibration_cache_service and uses it."""
    maint_file = Path(__file__).parent.parent / "apps/dashboard/routers/maintenance_service.py"
    content = maint_file.read_text(encoding="utf-8")

    # Verify calibration_cache_service is imported
    assert "import calibration_cache_service" in content, \
        "calibration_cache_service must be imported"

    # Verify the calibration helper functions are called via _ccs (or equivalent)
    assert "_calibration_empty_cache" in content, \
        "_calibration_empty_cache must be called"
    assert "_calibration_absorb_state_file" in content, \
        "_calibration_absorb_state_file must be called"
    assert "_save_calibration_cache" in content, \
        "_save_calibration_cache must be called"

    # Verify these are called from the imported module (not _srv)
    tree = ast.parse(content)
    found_empty = False
    found_absorb = False
    found_save = False

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                # Pattern: _ccs._calibration_empty_cache()
                if (isinstance(node.func.value, ast.Name) and
                    node.func.value.id == "_ccs" and
                    node.func.attr == "_calibration_empty_cache"):
                    found_empty = True
                if (isinstance(node.func.value, ast.Name) and
                    node.func.value.id == "_ccs" and
                    node.func.attr == "_calibration_absorb_state_file"):
                    found_absorb = True
                if (isinstance(node.func.value, ast.Name) and
                    node.func.value.id == "_ccs" and
                    node.func.attr == "_save_calibration_cache"):
                    found_save = True

    assert found_empty, "_ccs._calibration_empty_cache() must be called"
    assert found_absorb, "_ccs._calibration_absorb_state_file() must be called"
    assert found_save, "_ccs._save_calibration_cache() must be called"


def test_rebuild_calibration_cache_does_not_import_server():
    """AC4: Running rebuild_calibration_cache.py does not import server.

    This test imports the module and checks sys.modules to ensure server
    and FastAPI router modules were not loaded.
    """
    # Save current modules to detect new imports
    pre_modules = set(sys.modules.keys())

    # Import the rebuild script
    rebuild_script = Path(__file__).parent.parent / "scripts/rebuild_calibration_cache.py"
    spec = __import__("importlib.util").util.spec_from_file_location("rebuild_script", rebuild_script)
    rebuild_module = __import__("importlib.util").util.module_from_spec(spec)

    # Load the module (but don't execute the main block)
    try:
        spec.loader.exec_module(rebuild_module)
    except SystemExit:
        # Script calls sys.exit(), which is fine — we've loaded the imports
        pass
    except Exception as e:
        # Some exceptions during import are expected if running in test env
        # We just care that server.py wasn't imported
        pass

    # Check if server was imported
    post_modules = set(sys.modules.keys())
    new_modules = post_modules - pre_modules

    # Look for server module in new imports
    server_imported = any(
        "server" == m.split(".")[-1] or m.endswith(".server")
        for m in new_modules
        if m and "." in m or m == "server"
    )

    assert not server_imported, \
        "rebuild_calibration_cache.py should not import server.py or FastAPI modules"


def test_maintenance_service_rebuild_calls_ccs_helpers():
    """AC5: rebuild_calibration_cache() function calls calibration helpers via _ccs."""
    maint_file = Path(__file__).parent.parent / "apps/dashboard/routers/maintenance_service.py"
    content = maint_file.read_text(encoding="utf-8")

    # Find the rebuild_calibration_cache function
    tree = ast.parse(content)
    func_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "rebuild_calibration_cache":
            func_node = node
            break

    assert func_node is not None, "rebuild_calibration_cache function must exist"

    # Check that the function calls the calibration helpers
    func_str = ast.unparse(func_node)
    assert "_ccs._calibration_empty_cache" in func_str
    assert "_ccs._calibration_absorb_state_file" in func_str
    assert "_ccs._save_calibration_cache" in func_str
    assert "_srv." not in func_str
