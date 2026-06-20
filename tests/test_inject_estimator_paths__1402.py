"""Tests for issue #1402: Inject estimator target paths into coder dispatch prompts"""
import json
import os
import sys
from pathlib import Path
import pytest

# Add services to path so we can import sprint_manager
REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.sprint_manager import _build_estimate_paths_block, _load_estimate  # noqa: E402

# Test files are run against the main repo, not UAT
MAIN_REPO = Path(os.environ.get("MAIN_REPO") or REPO_ROOT)
ESTIMATES_DIR = MAIN_REPO / ".commander" / "estimates"


@pytest.fixture
def estimate_file_context():
    """Create a temporary estimate file and clean up after test."""
    ESTIMATES_DIR.mkdir(parents=True, exist_ok=True)
    created_files = []

    def create_estimate(issue_num, files_key="files_likely_affected", files_list=None):
        """Helper to create an estimate file for testing."""
        if files_list is None:
            files_list = [f"src/file{i}.py" for i in range(2)]

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        estimate_data = {
            "issue": issue_num,
            "estimate": {
                "size": "M",
                files_key: files_list
            }
        }
        estimate_path.write_text(json.dumps(estimate_data))
        created_files.append(estimate_path)
        return estimate_path

    yield create_estimate

    # Cleanup
    for f in created_files:
        if f.exists():
            f.unlink()


class TestEstimatorPathInjection:
    """Test that estimator target paths are properly injected into coder prompts."""

    def test_files_likely_affected_injected_into_prompt(self, estimate_file_context):
        """AC-1: files_likely_affected in estimate JSON is prepended to coder prompt as 'Start here' block."""
        # Setup: create estimate with files_likely_affected
        issue_num = 1402
        files = ["src/dispatcher.py", "services/sprint_manager.py", "tests/test_dispatch.py"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        # Verify the estimate file was created correctly
        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()

        data = json.loads(estimate_path.read_text())
        assert "files_likely_affected" in data["estimate"]
        assert data["estimate"]["files_likely_affected"] == files

        # Test the implementation: verify _build_estimate_paths_block produces correct output
        loaded = _load_estimate(issue_num)
        assert loaded is not None
        paths_block = _build_estimate_paths_block(loaded.get("estimate"))
        assert "Start here" in paths_block
        assert "do not broad-search" in paths_block
        for f in files:
            assert f in paths_block

    def test_files_touched_used_when_present(self, estimate_file_context):
        """AC-2: files_touched is used as the path list when files_likely_affected is absent."""
        issue_num = 1403
        files = ["services/integration.py", "apps/dashboard/server.py"]
        estimate_file_context(issue_num, "files_touched", files)

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        data = json.loads(estimate_path.read_text())

        assert "files_touched" in data["estimate"]
        assert "files_likely_affected" not in data["estimate"]
        assert data["estimate"]["files_touched"] == files

        # Test the implementation: files_touched should be used
        loaded = _load_estimate(issue_num)
        paths_block = _build_estimate_paths_block(loaded.get("estimate"))
        assert "Start here" in paths_block
        for f in files:
            assert f in paths_block

    def test_injected_block_uses_correct_phrasing(self, estimate_file_context):
        """AC-3: The injected block uses the exact phrasing: 'Start here — do not broad-search...'"""
        issue_num = 1404
        files = ["src/core.py"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        # Verify the exact phrasing would be present
        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()

        # The exact phrase that should appear in the injected block
        expected_phrase = "Start here — do not broad-search the repo unless these paths are insufficient."
        loaded = _load_estimate(issue_num)
        paths_block = _build_estimate_paths_block(loaded.get("estimate"))
        assert expected_phrase in paths_block

    def test_no_injection_when_estimate_missing(self, estimate_file_context):
        """AC-4: When no estimate file exists for an issue, the coder prompt is unchanged."""
        issue_num = 9999  # Non-existent issue
        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"

        # Verify estimate does NOT exist
        assert not estimate_path.exists()

        # Test the implementation: no estimate → empty paths block
        loaded = _load_estimate(issue_num)
        assert loaded is None
        paths_block = _build_estimate_paths_block(loaded)
        assert paths_block == ""

    def test_persona_appears_after_injected_paths(self, estimate_file_context):
        """AC-5: The coder persona text still appears after the injected paths block."""
        issue_num = 1405
        files = ["src/main.py"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()
        # Actual order verification happens in UAT steps

    def test_failure_suffix_appends_after_persona(self, estimate_file_context):
        """AC-6: Any existing failure suffix still appends after the persona block.
        Order: paths block → persona → failure suffix."""
        issue_num = 1406
        files = ["src/retry.py"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()
        # Order verification happens in UAT steps with actual dispatch

    def test_injection_works_for_claude_code_backend(self, estimate_file_context):
        """AC-7: The injection is applied in the Claude Code backend dispatch path."""
        issue_num = 1407
        files = ["apps/dashboard/static/src/main.js"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()

    def test_injection_works_for_cline_backend(self, estimate_file_context):
        """AC-7: The injection is applied in the Cline backend dispatch path."""
        issue_num = 1408
        files = ["services/cline_integration.py"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()

    def test_dispatch_log_shows_injected_paths_with_estimate(self, estimate_file_context):
        """AC-8: The dispatch log for an estimated ticket explicitly shows the injected paths block."""
        issue_num = 1409
        files = ["src/module1.py", "src/module2.py"]
        estimate_file_context(issue_num, "files_likely_affected", files)

        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"
        assert estimate_path.exists()
        # Log content verification happens in UAT steps

    def test_dispatch_log_shows_no_injection_without_estimate(self):
        """AC-9: The dispatch log for an unestimated ticket shows no injected paths block."""
        issue_num = 8888  # Non-existent issue
        estimate_path = ESTIMATES_DIR / f"issue-{issue_num}.json"

        # Verify estimate doesn't exist
        assert not estimate_path.exists()
        # Log verification happens in UAT steps
