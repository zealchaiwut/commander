"""Tests for issue #2252: Delete dispatch-pinned tests.

AC coverage:
- AC1: Tests importing the deleted orchestrator modules are removed
- AC2: agent_browser_runner tests retained
- AC3: Tests for state_machine, reconciliation, summary, Finish, Deploy retained

Issue #2345: this file used to spawn a full-tree ``pytest tests/ --co`` once
per AC1 assertion (6×). Those nested collects, when orphaned by an outer suite
timeout, were a major source of the runaway process tree. Collection is now cached once per module via a process-group-safe runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"
DASH_TESTS_DIR = REPO_ROOT / "apps" / "dashboard" / "tests"



@pytest.fixture(scope="module")
def collection_output() -> str:
    """Run full-tree ``--co`` once and cache. Process-group-safe (#2345)."""
    from services.sprint_manager.pytest_runner import run_pytest

    result = run_pytest(
        [str(TESTS_DIR), str(DASH_TESTS_DIR), "--co", "--tb=no", "-q"],
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    return (result.stdout or "") + (result.stderr or "")


# ── AC1: deleted-module imports are gone ──────────────────────────────────────

class TestDeletedModuleImportsRemoved:
    def test_no_sprint_manager_import_error(self, collection_output):
        """AC1: no test file fails to import due to missing sprint_manager."""
        assert "ModuleNotFoundError: No module named 'services.sprint_manager.sprint_manager'" \
               not in collection_output, \
               "Found test(s) still importing the deleted sprint_manager.py module"

    def test_no_dispatch_import_error(self, collection_output):
        """AC1: no test file fails to import due to missing dispatch module."""
        assert "No module named 'services.sprint_manager.dispatch'" not in collection_output, \
               "Found test(s) still importing the deleted dispatch.py module"

    def test_no_pipeline_import_error(self, collection_output):
        """AC1: no test file fails to import due to missing pipeline module."""
        assert "No module named 'services.sprint_manager.pipeline'" not in collection_output, \
               "Found test(s) still importing the deleted pipeline.py module"

    def test_no_concurrent_scheduler_import_error(self, collection_output):
        """AC1: no test file fails to import due to missing concurrent_scheduler."""
        assert "No module named 'services.sprint_manager.concurrent_scheduler'" not in collection_output, \
               "Found test(s) still importing the deleted concurrent_scheduler.py module"

    def test_no_worktree_pool_import_error(self, collection_output):
        """AC1: no test file fails to import due to missing worktree_pool."""
        assert "No module named 'services.sprint_manager.worktree_pool'" not in collection_output, \
               "Found test(s) still importing the deleted worktree_pool.py module"

    def test_collection_error_count_at_most_baseline(self, collection_output):
        """AC1+AC4: collection error count ≤ 25 (the pre-existing baseline)."""
        error_lines = [
            line for line in collection_output.splitlines()
            if line.startswith("ERROR ")
        ]
        assert len(error_lines) <= 25, (
            f"Expected ≤25 collection errors (baseline), got {len(error_lines)}:\n"
            + "\n".join(error_lines[:30])
        )


# ── AC2: agent_browser_runner tests retained ──────────────────────────────────

class TestAgentBrowserRunnerRetained:
    def test_709_file_exists(self):
        """AC2: tests/test_709__agent_browser_runner.py must still exist."""
        assert (TESTS_DIR / "test_709__agent_browser_runner.py").exists(), \
               "agent_browser_runner test file was accidentally deleted"

    def test_709_collects_without_error(self):
        """AC2: agent_browser_runner test file collects without import error."""
        from services.sprint_manager.pytest_runner import run_pytest

        result = run_pytest(
            [str(TESTS_DIR / "test_709__agent_browser_runner.py"),
             "--co", "--tb=no", "-q"],
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        assert "ERROR" not in out, \
               "agent_browser_runner test file fails to collect: " + out
        assert result.returncode == 0, \
               "pytest --collect-only returned non-zero for test_709"


# ── AC3: retained-module tests still collect ──────────────────────────────────

class TestRetainedModuleTestsPresent:
    @pytest.mark.parametrize("test_file", [
        "test_508__state_machine.py",
        "test_2050__state_machine_approve_reject.py",
    ])
    def test_state_machine_tests_retained(self, test_file):
        """AC3: state-machine test files retained and collect OK."""
        from services.sprint_manager.pytest_runner import run_pytest

        path = TESTS_DIR / test_file
        assert path.exists(), f"State-machine test file missing: {test_file}"
        result = run_pytest(
            [str(path), "--co", "--tb=no", "-q"],
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        assert "ERROR" not in out, \
               f"{test_file} fails to collect:\n{out}"

    @pytest.mark.parametrize("test_file", [
        "test_1163__sprint_summary_materialize.py",
        "test_2237__decouple_summary_from_deleted_modules.py",
        "test_2248__finish_flow_generates_summary.py",
    ])
    def test_summary_tests_retained(self, test_file):
        """AC3: summary test files retained and collect OK."""
        from services.sprint_manager.pytest_runner import run_pytest

        path = TESTS_DIR / test_file
        assert path.exists(), f"Summary test file missing: {test_file}"
        result = run_pytest(
            [str(path), "--co", "--tb=no", "-q"],
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        assert "ERROR" not in out, \
               f"{test_file} fails to collect:\n{out}"

    @pytest.mark.parametrize("test_file", [
        "test_2086__finish_merge_safety.py",
        "test_2170__finish_flow_project_scoped.py",
        "test_2230__finish_feature_uat_transition.py",
    ])
    def test_finish_tests_retained(self, test_file):
        """AC3: Finish flow test files retained and collect OK."""
        from services.sprint_manager.pytest_runner import run_pytest

        path = TESTS_DIR / test_file
        assert path.exists(), f"Finish flow test file missing: {test_file}"
        result = run_pytest(
            [str(path), "--co", "--tb=no", "-q"],
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        assert "ERROR" not in out, \
               f"{test_file} fails to collect:\n{out}"

    @pytest.mark.parametrize("test_file", [
        "test_722__deploy_config.py",
        "test_723__deploy_restart_actions.py",
        "test_726__deploy_tab.py",
    ])
    def test_deploy_tests_retained(self, test_file):
        """AC3: Deploy test files retained and collect OK."""
        from services.sprint_manager.pytest_runner import run_pytest

        path = TESTS_DIR / test_file
        assert path.exists(), f"Deploy test file missing: {test_file}"
        result = run_pytest(
            [str(path), "--co", "--tb=no", "-q"],
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        assert "ERROR" not in out, \
               f"{test_file} fails to collect:\n{out}"

    @pytest.mark.parametrize("test_file", [
        "test_1162__reconcile_fix_sprint_counts.py",
        "test_1882__reconcile_latest_outcome_and_rework_label.py",
        "test_2167__reconcile_outcome_reclassification.py",
    ])
    def test_reconciliation_tests_retained(self, test_file):
        """AC3: reconciliation test files retained and collect OK."""
        from services.sprint_manager.pytest_runner import run_pytest

        path = TESTS_DIR / test_file
        assert path.exists(), f"Reconciliation test file missing: {test_file}"
        result = run_pytest(
            [str(path), "--co", "--tb=no", "-q"],
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        out = (result.stdout or "") + (result.stderr or "")
        assert "ERROR" not in out, \
               f"{test_file} fails to collect:\n{out}"
