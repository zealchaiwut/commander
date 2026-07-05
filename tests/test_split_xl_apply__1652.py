"""Tests for issue #1652: remove dead if-ok guard in split_xl_apply (runs against UAT)"""
import os
import pytest
import httpx


# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
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

def test_split_xl_apply__dead_guard_removed(client):
    # AC: The dead `if result.get("ok"):` guard at apps/dashboard/routers/sprint_history.py:196 is removed or replaced with an unconditional block
    # Verify via source code inspection: read the file and confirm no `if result.get("ok"):` wraps the broadcast logic
    with open("/Users/zeal-server/dev/commander/tester/apps/dashboard/routers/sprint_history.py") as f:
        content = f.read()

    # Check that the function exists and the dead guard is gone
    # The old pattern was: raise HTTPException(...); if result.get("ok"): <broadcast>
    # The new pattern should be: raise HTTPException(...); try: <broadcast>

    # Search for the old pattern — should not exist
    import re
    # Old pattern: 'if result.get("ok"):' appearing within split_xl_apply function
    # Extract the split_xl_apply function
    match = re.search(r'async def split_xl_apply\(.*?\):\s*.*?(?=async def|\Z)', content, re.DOTALL)
    if match:
        func_body = match.group(0)
        # Look for the specific dead guard
        has_dead_guard = 'if result.get("ok"):' in func_body
        assert not has_dead_guard, f"Dead guard 'if result.get(\"ok\"):' should be removed but found in function"


def test_split_xl_apply__failure_path_intact(client):
    # AC: The HTTPException(500, ...) raise path remains intact and unchanged
    with open("/Users/zeal-server/dev/commander/tester/apps/dashboard/routers/sprint_history.py") as f:
        content = f.read()

    # Verify the failure guard still exists and raises HTTPException with the correct detail structure
    import re
    match = re.search(r'async def split_xl_apply\(.*?\):\s*.*?(?=async def|\Z)', content, re.DOTALL)
    if match:
        func_body = match.group(0)
        # Check for the failure path
        has_failure_guard = 'if not result.get("ok"):' in func_body
        has_http_exception = 'raise HTTPException(500,' in func_body
        has_detail_structure = (
            '"error": result.get("error")' in func_body
            and '"partial": result.get("partial"' in func_body
            and '"created": result.get("created_numbers"' in func_body
            and '"compensation": result.get("compensation")' in func_body
        )
        assert has_failure_guard, "Failure guard 'if not result.get(\"ok\"):' should exist"
        assert has_http_exception, "HTTPException(500, ...) should be raised for failures"
        assert has_detail_structure, "HTTPException detail structure should include error, partial, created, compensation"


def test_split_xl_apply__success_path_unconditional(client):
    # AC: The success-path logic previously inside the `if result.get("ok"):` block executes unconditionally after the failure guard
    with open("/Users/zeal-server/dev/commander/tester/apps/dashboard/routers/sprint_history.py") as f:
        content = f.read()

    import re
    match = re.search(r'async def split_xl_apply\(.*?\):\s*.*?(?=async def|\Z)', content, re.DOTALL)
    if match:
        func_body = match.group(0)

        # Verify the broadcast logic appears without an if-guard wrapping
        # After the HTTPException raise, should see: try: ... srv.broadcast(...) ... except
        has_broadcast = 'srv.broadcast' in func_body or 'await srv.broadcast' in func_body
        assert has_broadcast, "Broadcast logic should still exist"

        # Find the position of HTTPException raise and the broadcast
        exc_pos = func_body.find('raise HTTPException(500,')
        broadcast_pos = func_body.find('srv.broadcast', exc_pos) if exc_pos >= 0 else -1

        # Extract the section between raise and broadcast to check for nested if
        if exc_pos >= 0 and broadcast_pos > exc_pos:
            between = func_body[exc_pos:broadcast_pos]
            # After the raise, we should see try: not if result.get("ok"): try:
            # So check that there's no dead guard between raise and broadcast
            dead_guard_between = 'if result.get("ok"):' in between
            assert not dead_guard_between, "No dead guard should exist between HTTPException and broadcast"


def test_split_xl_apply__no_functional_behavior_changes(client):
    # AC: No functional behavior changes — only the dead conditional wrapper is removed
    # This is a structural check: the function should still have the same logic flow,
    # just without the unreachable conditional. Verify via source inspection.
    with open("/Users/zeal-server/dev/commander/tester/apps/dashboard/routers/sprint_history.py") as f:
        content = f.read()

    import re
    match = re.search(r'async def split_xl_apply\(.*?\):\s*.*?(?=async def|\Z)', content, re.DOTALL)
    if match:
        func_body = match.group(0)

        # Verify key functional elements are still present
        has_label_validation = '_SPRINT_LABEL_RE.match(label)' in func_body
        has_split_apply_call = 'split_xl_service.split_apply' in func_body
        has_error_response = 'result.get("error")' in func_body
        has_result_return = 'return result' in func_body

        assert has_label_validation, "Label validation should still exist"
        assert has_split_apply_call, "split_xl_service.split_apply call should still exist"
        assert has_error_response, "Error handling should still exist"
        assert has_result_return, "Function should return result"
