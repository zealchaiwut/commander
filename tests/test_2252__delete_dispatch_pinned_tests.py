"""Tests for issue #2252: Delete dispatch-pinned tests.

AC coverage:
- AC1: Tests importing the deleted orchestrator modules are removed
- AC2: agent_browser_runner tests retained
- AC3: Tests for state_machine, reconciliation, summary, Finish, Deploy retained
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
TESTS_DIR = REPO_ROOT / "tests"
DASH_TESTS_DIR = REPO_ROOT / "apps" / "dashboard" / "tests"

_DELETED_MODULES = (
    "sprint_manager",
    "dispatch",
    "pipeline",
    "concurrent_scheduler",
    "worktree_pool",
)


def _collect_errors() -> str:
    """Run pytest --collect-only and return stderr+stdout as a string."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TESTS_DIR), str(DASH_TESTS_DIR),
         "--co", "--tb=no", "-q"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    return result.stdout + result.stderr


# ── AC1: deleted-module imports are gone ──────────────────────────────────────

class TestDeletedModuleImportsRemoved:
    def test_no_sprint_manager_import_error(self):
        """AC1: no test file fails to import due to missing sprint_manager."""
        output = _collect_errors()
        assert "ModuleNotFoundError: No module named 'services.sprint_manager.sprint_manager'" \
               not in output, \
               "Found test(s) still importing the deleted sprint_manager.py module"

    def test_no_dispatch_import_error(self):
        """AC1: no test file fails to import due to missing dispatch module."""
        output = _collect_errors()
        assert "No module named 'services.sprint_manager.dispatch'" not in output, \
               "Found test(s) still importing the deleted dispatch.py module"
        assert "No module named 'dispatch'" not in output or \
               "No module named 'services.sprint_manager.dispatch'" not in output, \
               "Found test(s) still importing the deleted dispatch module"

    def test_no_pipeline_import_error(self):
        """AC1: no test file fails to import due to missing pipeline module."""
        output = _collect_errors()
        assert "No module named 'services.sprint_manager.pipeline'" not in output, \
               "Found test(s) still importing the deleted pipeline.py module"

    def test_no_concurrent_scheduler_import_error(self):
        """AC1: no test file fails to import due to missing concurrent_scheduler."""
        output = _collect_errors()
        assert "No module named 'services.sprint_manager.concurrent_scheduler'" not in output, \
               "Found test(s) still importing the deleted concurrent_scheduler.py module"

    def test_no_worktree_pool_import_error(self):
        """AC1: no test file fails to import due to missing worktree_pool."""
        output = _collect_errors()
        assert "No module named 'services.sprint_manager.worktree_pool'" not in output, \
               "Found test(s) still importing the deleted worktree_pool.py module"

    def test_collection_error_count_at_most_baseline(self):
        """AC1+AC4: collection error count ≤ 25 (the pre-existing baseline)."""
        output = _collect_errors()
        error_lines = [
            line for line in output.splitlines()
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
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             str(TESTS_DIR / "test_709__agent_browser_runner.py"),
             "--co", "--tb=no", "-q"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert "ERROR" not in (result.stdout + result.stderr), \
               "agent_browser_runner test file fails to collect: " + result.stdout + result.stderr
        assert result.returncode == 0, \
               "pytest --collect-only returned non-zero for test_709"


# ── AC3: retained-module tests still collect ──────────────────────────────────

class TestRetainedModuleTestsPresent:
    @pytest.mark.parametrize("test_file", [
        "test_508__state_machine.py",
        "test_2050__state_machine_approve_reject.py",
    ])
    def test_state_machine_tests_retained(self, test_file):
        """AC3: state_machine test files retained and collect OK."""
        path = TESTS_DIR / test_file
        assert path.exists(), f"State-machine test file missing: {test_file}"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--co", "--tb=no", "-q"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert "ERROR" not in (result.stdout + result.stderr), \
               f"{test_file} fails to collect:\n{result.stdout}{result.stderr}"

    @pytest.mark.parametrize("test_file", [
        "test_1163__sprint_summary_materialize.py",
        "test_2237__decouple_summary_from_deleted_modules.py",
        "test_2248__finish_flow_generates_summary.py",
    ])
    def test_summary_tests_retained(self, test_file):
        """AC3: summary test files retained and collect OK."""
        path = TESTS_DIR / test_file
        assert path.exists(), f"Summary test file missing: {test_file}"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--co", "--tb=no", "-q"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert "ERROR" not in (result.stdout + result.stderr), \
               f"{test_file} fails to collect:\n{result.stdout}{result.stderr}"

    @pytest.mark.parametrize("test_file", [
        "test_2086__finish_merge_safety.py",
        "test_2170__finish_flow_project_scoped.py",
        "test_2230__finish_feature_uat_transition.py",
    ])
    def test_finish_tests_retained(self, test_file):
        """AC3: Finish flow test files retained and collect OK."""
        path = TESTS_DIR / test_file
        assert path.exists(), f"Finish flow test file missing: {test_file}"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--co", "--tb=no", "-q"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert "ERROR" not in (result.stdout + result.stderr), \
               f"{test_file} fails to collect:\n{result.stdout}{result.stderr}"

    @pytest.mark.parametrize("test_file", [
        "test_722__deploy_config.py",
        "test_723__deploy_restart_actions.py",
        "test_726__deploy_tab.py",
    ])
    def test_deploy_tests_retained(self, test_file):
        """AC3: Deploy test files retained and collect OK."""
        path = TESTS_DIR / test_file
        assert path.exists(), f"Deploy test file missing: {test_file}"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--co", "--tb=no", "-q"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert "ERROR" not in (result.stdout + result.stderr), \
               f"{test_file} fails to collect:\n{result.stdout}{result.stderr}"

    @pytest.mark.parametrize("test_file", [
        "test_1162__reconcile_fix_sprint_counts.py",
        "test_1882__reconcile_latest_outcome_and_rework_label.py",
        "test_2167__reconcile_outcome_reclassification.py",
    ])
    def test_reconciliation_tests_retained(self, test_file):
        """AC3: reconciliation test files retained and collect OK."""
        path = TESTS_DIR / test_file
        assert path.exists(), f"Reconciliation test file missing: {test_file}"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--co", "--tb=no", "-q"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        assert "ERROR" not in (result.stdout + result.stderr), \
               f"{test_file} fails to collect:\n{result.stdout}{result.stderr}"
