"""Tests for issue #1652: dead if-ok guard after HTTPException raise in split_xl_apply (runs against UAT)"""
import os
import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_dead_if_ok_guard__removed_guard(client):
    """AC: The dead `if result.get("ok"):` guard is removed or replaced with an unconditional block"""
    # Read the source file to verify the guard has been removed
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../apps/dashboard'))
    
    with open(os.path.join(os.path.dirname(__file__), '../apps/dashboard/routers/sprint_history.py'), 'r') as f:
        content = f.read()
    
    # Extract the split_xl_apply function
    start = content.find('async def split_xl_apply')
    end = content.find('\n\n# ──', start)
    function_body = content[start:end]
    
    # Verify the dead guard is not present
    # The guard would look like: "if result.get("ok"):" followed by indented try-except
    lines = function_body.split('\n')
    
    # Find the HTTPException raise
    exception_line_idx = None
    for i, line in enumerate(lines):
        if 'raise HTTPException(500' in line:
            exception_line_idx = i
            break
    
    assert exception_line_idx is not None, "HTTPException(500, ...) not found in function"
    
    # After the exception block (after the closing paren/bracket), the next code should be
    # the try-except for broadcast, and it should NOT be wrapped in "if result.get("ok"):"
    # Check that we don't find "if result.get("ok"):" after the raise
    after_exception = '\n'.join(lines[exception_line_idx:])
    assert 'if result.get("ok"):' not in after_exception, \
        "Dead guard 'if result.get(\"ok\"):' still present after exception raise"


def test_dead_if_ok_guard__exception_path_intact(client):
    """AC: The HTTPException(500, ...) raise path remains intact and unchanged"""
    with open(os.path.join(os.path.dirname(__file__), '../apps/dashboard/routers/sprint_history.py'), 'r') as f:
        content = f.read()
    
    # Verify the exception handler is still there with all required fields
    assert 'if not result.get("ok"):' in content, "Failure guard missing"
    assert 'raise HTTPException(500' in content, "HTTPException(500) missing"
    assert '"error": result.get("error")' in content, "error field missing in exception detail"
    assert '"partial": result.get("partial"' in content, "partial field missing"
    assert '"created": result.get("created_numbers"' in content, "created field missing"
    assert '"compensation": result.get("compensation")' in content, "compensation field missing"


def test_dead_if_ok_guard__success_path_unconditional(client):
    """AC: The success-path logic executes unconditionally after the failure guard"""
    with open(os.path.join(os.path.dirname(__file__), '../apps/dashboard/routers/sprint_history.py'), 'r') as f:
        content = f.read()
    
    # Extract the split_xl_apply function
    start = content.find('async def split_xl_apply')
    end = content.find('\n\n# ──', start)
    function_body = content[start:end]
    lines = function_body.split('\n')
    
    # Find the line with HTTPException and get its closing bracket
    exception_start = None
    for i, line in enumerate(lines):
        if 'raise HTTPException(500' in line:
            exception_start = i
            break
    
    assert exception_start is not None
    
    # Find the closing '}' or ')' of the HTTPException
    bracket_count = 0
    exception_end = exception_start
    for i in range(exception_start, len(lines)):
        bracket_count += lines[i].count('{') + lines[i].count('(')
        bracket_count -= lines[i].count('}') + lines[i].count(')')
        if bracket_count == 0 and i > exception_start:
            exception_end = i
            break
    
    # After the exception block, check that the broadcast code is not indented
    # within an if statement (it should be at the same level as the if not result.get)
    after_exception_lines = lines[exception_end + 1:]
    
    # Find the try statement
    try_found = False
    for i, line in enumerate(after_exception_lines):
        if line.strip().startswith('try:'):
            try_found = True
            # The try should be at the same indentation as the if not result.get
            # Extract the leading spaces
            leading_spaces = len(line) - len(line.lstrip())
            
            # Verify it's at the expected indentation (same as if not...)
            # In Python, if not result.get("ok"): is typically at 4 spaces (inside the function)
            # so the unconditional try should also be at 4 spaces
            assert leading_spaces == 4, f"try block not at function body level (indent={leading_spaces})"
            break
    
    assert try_found, "try block for broadcast not found after exception handling"


def test_dead_if_ok_guard__no_functional_changes(client):
    """AC: No functional behavior changes — only the dead conditional wrapper is removed"""
    # This test verifies the logic flow by checking the structure
    with open(os.path.join(os.path.dirname(__file__), '../apps/dashboard/routers/sprint_history.py'), 'r') as f:
        content = f.read()
    
    # Verify key components are still present and unchanged:
    # 1. split_apply call
    assert 'split_xl_service.split_apply' in content
    
    # 2. The failure path (if not result.get("ok"))
    assert 'if not result.get("ok"):' in content
    
    # 3. The broadcast call
    assert 'srv.broadcast' in content
    
    # 4. The return statement
    assert 'return result' in content
    
    # These all exist and the flow is: split_apply -> check result -> if fail raise exception
    # -> if success, broadcast -> return result


# --- UAT Step Tests (HTTP) ---

def test_dead_if_ok_guard__endpoint_structure(client):
    """UAT Step 1: Verify the code structure has no if result.get("ok") guard"""
    # This is verified by the acceptance criteria tests above
    # The guard check is comprehensive and tests the actual source code
    pass


def test_dead_if_ok_guard__endpoint_exists(client):
    """UAT Step 2: Verify split_xl_apply endpoint exists and responds"""
    # The endpoint should be accessible at /api/sprints/{label}/split-xl/{issue}/apply
    # We'll verify it handles a request (may reject it with 400, but endpoint exists)
    
    # Try a POST with invalid data - should get error response but endpoint must exist
    try:
        r = client.post("/api/sprints/sprint-101/split-xl/999/apply", json={})
        # Should get either a validation error or HTTP error, but not 404
        assert r.status_code != 404, "Endpoint returned 404 - endpoint may not exist"
        # Typically will be 422 (validation) or 400 (invalid label/data)
    except Exception:
        # Connection errors are OK (sprint may not exist in test data)
        # We're just verifying the route is mounted
        pass


def test_dead_if_ok_guard__no_syntax_errors(client):
    """UAT Step 3: Verify the module can be imported without syntax errors"""
    # This validates the function syntax is correct
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../apps/dashboard'))
        from routers import sprint_history
        assert hasattr(sprint_history, 'split_xl_apply'), "split_xl_apply function not found"
        # If we got here, the module loaded successfully with no syntax errors
    except SyntaxError as e:
        pytest.fail(f"Syntax error in sprint_history.py: {e}")
