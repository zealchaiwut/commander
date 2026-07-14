"""Tests for issue #1916: [follow-up] fetch_issue stale-hit guard is inert (runs against UAT)"""
import os
import sys
import inspect
from pathlib import Path

# Resolved from UAT .env at runtime; see tester skill Step 0.
# Default kept only as a last-resort fallback if BASE_URL not exported.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )

# Add repo root to path to import estimate_issue
REPO_ROOT = Path(__file__).parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager import estimate_issue


# --- Acceptance Criteria Tests ---

def test_fetch_issue_no_dead_sync_ts_parameter():
    """AC 1: fetch_issue has no dead sync_ts parameter — it's either passed by caller or removed."""
    # Verify fetch_issue signature has sync_ts parameter with default None
    sig = inspect.signature(estimate_issue.fetch_issue)
    assert "sync_ts" in sig.parameters, \
        "fetch_issue must have sync_ts parameter"
    assert sig.parameters["sync_ts"].default is None, \
        "sync_ts must have default value None"


def test_fetch_issue_caller_passes_sync_ts():
    """AC 2: If sync_ts retained, production caller at line ~594 passes real sync_ts value."""
    # Read the estimate_issue.py file and verify that:
    # 1. _sync_ts is created before calling fetch_issue
    # 2. The call passes sync_ts=_sync_ts to fetch_issue

    estimate_module_path = Path(estimate_issue.__file__)
    content = estimate_module_path.read_text()

    # Verify main() creates _sync_ts before calling fetch_issue
    assert "_sync_ts = datetime.now(timezone.utc).strftime(" in content, \
        "main() must create _sync_ts timestamp before calling fetch_issue"

    # Verify the call passes sync_ts=_sync_ts to fetch_issue
    assert "issue_data = fetch_issue(args.issue, repo, sync_ts=_sync_ts)" in content, \
        "main() must pass sync_ts=_sync_ts to fetch_issue (not None)"


def test_fetch_issue_stale_guard_timestamp_format():
    """AC 3: sync_ts is created as ISO 8601 UTC timestamp for mirror staleness comparison."""
    estimate_module_path = Path(estimate_issue.__file__)
    content = estimate_module_path.read_text()

    # Verify the timestamp format matches ISO 8601 with Z suffix (UTC marker)
    assert '_sync_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")' in content, \
        "sync_ts must use ISO 8601 UTC format (YYYY-MM-DDTHH:MM:SSZ)"


def test_fetch_issue_docstring_documents_sync_ts():
    """AC 4: fetch_issue docstring documents sync_ts parameter and stale-hit behavior."""
    docstring = estimate_issue.fetch_issue.__doc__
    assert docstring is not None, "fetch_issue must have a docstring"
    assert "sync_ts" in docstring.lower(), \
        "Docstring must document sync_ts parameter"
    assert "stale" in docstring.lower(), \
        "Docstring must document stale-hit guard behavior"


def test_fetch_issue_live_fallback_unchanged():
    """AC 5: fetch_issue live-fallback behavior unchanged when mirror has no body."""
    # Verify that when mirror has no body, live gh api is used (existing behavior)
    # This is a source-text check that the logic flow is preserved

    estimate_module_path = Path(estimate_issue.__file__)
    content = estimate_module_path.read_text()

    # Verify the fallback condition exists
    assert "if _mirror is not None and _mirror.get(\"body\") is not None:" in content, \
        "Mirror availability check must guard the return path"

    # Verify live fetch is still called on mirror miss
    assert "subprocess.run(" in content and '["gh", "api"' in content, \
        "Live gh api fallback must be present for mirror miss"
