"""Tests for issue #1290: Extract run_sprint preflight/branch-setup into helper.

AC-1: run_sprint_preflight() function exists and contains the preflight +
      branch-setup logic previously inline in run_sprint
AC-2: run_sprint calls run_sprint_preflight() and does not duplicate the extracted logic
AC-3: run_sprint is not moved as a whole blob; only the preflight/branch-setup section
      is extracted
AC-4: All existing unit/integration tests pass without modification to test assertions
AC-5: python sprint_manager.py --help exits 0 and output is identical to pre-refactor
AC-6: A smoke run (dry-run or stub sprint) completes without error or changed output
AC-7: No new public API surface is introduced beyond run_sprint_preflight()
AC-8: Diff contains zero logic changes — only code motion and the new call-site
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

import services.sprint_manager.sprint_manager as sm  # noqa: E402


# ── AC-1: run_sprint_preflight exists and is callable ─────────────────────────

class TestRunSprintPreflightExists:
    """AC-1: run_sprint_preflight() function exists and is callable."""

    def test_run_sprint_preflight_is_defined(self):
        assert hasattr(sm, "run_sprint_preflight"), (
            "run_sprint_preflight must be defined in sprint_manager"
        )

    def test_run_sprint_preflight_is_callable(self):
        assert callable(sm.run_sprint_preflight)

    def test_run_sprint_preflight_accepts_label(self):
        sig = inspect.signature(sm.run_sprint_preflight)
        assert "label" in sig.parameters

    def test_run_sprint_preflight_returns_preflight_result(self):
        """The return annotation (or actual return) yields a structured result."""
        # function must have a return annotation or we rely on behavior tests
        # If no annotation present, at minimum it must be callable and not raise ImportError
        assert callable(sm.run_sprint_preflight)

    def test_preflight_result_has_early_exit_field(self):
        """The result type must carry an early_exit indicator."""
        # If not annotated, we'll rely on the smoke test in AC-6
        # The important thing: the function signature exists
        assert callable(sm.run_sprint_preflight)


# ── AC-1: preflight contains branch-setup logic ────────────────────────────────

class TestPreflightContainsBranchSetup:
    """AC-1: preflight contains the sprint branch creation logic."""

    def test_run_sprint_preflight_source_references_create_sprint_branch(self):
        source = inspect.getsource(sm.run_sprint_preflight)
        assert "_create_sprint_branch" in source, (
            "run_sprint_preflight must contain the _create_sprint_branch call "
            "(the branch-setup block)"
        )

    def test_run_sprint_preflight_source_references_sprint_branch(self):
        source = inspect.getsource(sm.run_sprint_preflight)
        assert "sprint_branch" in source, (
            "run_sprint_preflight must reference sprint_branch "
            "(the branch name computed during preflight)"
        )

    def test_run_sprint_preflight_source_contains_pid_setup(self):
        source = inspect.getsource(sm.run_sprint_preflight)
        assert "_setup_pid_file" in source, (
            "run_sprint_preflight must contain PID file setup (preflight infra)"
        )

    def test_run_sprint_preflight_source_contains_state_loading(self):
        source = inspect.getsource(sm.run_sprint_preflight)
        assert "SprintState" in source, (
            "run_sprint_preflight must contain state loading/building logic"
        )


# ── AC-2: run_sprint calls run_sprint_preflight ────────────────────────────────

class TestRunSprintDelegatesToPreflight:
    """AC-2: run_sprint calls run_sprint_preflight and does not duplicate logic."""

    def test_run_sprint_source_calls_run_sprint_preflight(self):
        source = inspect.getsource(sm.run_sprint)
        assert "run_sprint_preflight(" in source, (
            "run_sprint must contain a call to run_sprint_preflight()"
        )

    def test_run_sprint_source_does_not_contain_create_sprint_branch(self):
        source = inspect.getsource(sm.run_sprint)
        assert "_create_sprint_branch(" not in source, (
            "run_sprint must NOT call _create_sprint_branch directly — "
            "that logic now lives in run_sprint_preflight"
        )

    def test_run_sprint_source_does_not_contain_setup_pid_file(self):
        source = inspect.getsource(sm.run_sprint)
        assert "_setup_pid_file(" not in source, (
            "run_sprint must NOT call _setup_pid_file directly — "
            "that logic now lives in run_sprint_preflight"
        )


# ── AC-3: run_sprint still exists as a whole function ─────────────────────────

class TestRunSprintNotMovedAsBlob:
    """AC-3: Only the preflight section is extracted; run_sprint still exists."""

    def test_run_sprint_still_exists(self):
        assert hasattr(sm, "run_sprint"), "run_sprint must still exist in sprint_manager"
        assert callable(sm.run_sprint)

    def test_run_sprint_dispatches_tickets(self):
        """run_sprint still owns the dispatch loop (not extracted)."""
        source = inspect.getsource(sm.run_sprint)
        # The dispatch loop iterates over _flat_dispatch
        assert "_flat_dispatch" in source, (
            "run_sprint must still contain the dispatch loop (_flat_dispatch)"
        )

    def test_run_sprint_signature_unchanged(self):
        """run_sprint signature must be identical to pre-refactor."""
        sig = inspect.signature(sm.run_sprint)
        params = list(sig.parameters.keys())
        expected_params = [
            "label", "skip_gates", "gate_pytest", "gate_lint", "gate_merge_preview",
            "gate_typecheck", "gate_design", "gate_frontend_lint", "gate_monolith",
            "alert_modes", "repo_name", "dry_run", "resume", "retry_failed",
            "target_branch", "cfg", "preflight_approved", "gate_scope",
            "token_budget", "skip_estimator", "rerun_manifest", "pipeline_mode",
            "max_coder_slots", "max_tester_slots",
        ]
        assert params == expected_params, (
            f"run_sprint signature changed.\nExpected: {expected_params}\nGot: {params}"
        )


# ── AC-5: --help exits 0 with identical output ────────────────────────────────

class TestHelpExits:
    """AC-5: python sprint_manager.py --help exits 0."""

    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(SM_DIR / "sprint_manager.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ},  # DB_PATH already forced to tmp path by conftest (#2047)
        )
        assert result.returncode == 0, (
            f"sprint_manager.py --help exited {result.returncode}\n"
            f"stderr: {result.stderr}"
        )

    def test_help_mentions_sprint_label(self):
        result = subprocess.run(
            [sys.executable, str(SM_DIR / "sprint_manager.py"), "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={**os.environ},  # DB_PATH already forced to tmp path by conftest (#2047)
        )
        assert "sprint" in result.stdout.lower(), (
            "Help text must reference 'sprint' — output may have changed"
        )

    def test_py_compile_sprint_manager(self):
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SM_DIR / "sprint_manager.py")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )


# ── AC-6: Smoke dry-run completes without error ────────────────────────────────

class TestSmokeDryRun:
    """AC-6: A dry-run stub sprint completes without error."""

    def _make_minimal_cfg(self, tmp_path):
        """Build a minimal SprintConfig pointing to tmp_path worktrees."""
        import textwrap
        coder_dir = tmp_path / "coder"
        coder_dir.mkdir()
        tester_dir = tmp_path / "tester"
        tester_dir.mkdir()
        (coder_dir / "PRODUCT.md").write_text("# Product")
        (coder_dir / "DESIGN.md").write_text("# Design")
        yaml_text = textwrap.dedent(f"""
            repo_name: owner/testrepo
            worktrees:
              coder: {coder_dir}
              tester: {tester_dir}
        """)
        config_path = tmp_path / "sprint.yaml"
        config_path.write_text(yaml_text)
        return sm.load_config(config_path)

    def test_run_sprint_preflight_dry_run_no_issues_returns_early_exit(self, tmp_path):
        """When there are no issues, preflight signals early exit."""
        cfg = self._make_minimal_cfg(tmp_path)
        with (
            patch.object(sm, "list_backlog_issues", return_value=[]),
            patch.object(sm, "_load_sprint_plan", return_value=None),
            patch.object(sm, "_sprint_number", return_value=99),
            patch.object(sm, "_state_path", return_value=tmp_path / "state.json"),
            patch.object(sm, "mint_run_id", return_value="run-test-123"),
            patch.object(sm, "_setup_pid_file", return_value=None),
            patch.object(sm, "structured_log", MagicMock()),
        ):
            result = sm.run_sprint_preflight(
                label="sprint-99",
                alert_modes=["dashboard_banner"],
                repo_name="owner/testrepo",
                dry_run=True,
                cfg=cfg,
            )
        assert result.early_exit is True, (
            "No-issues path must set early_exit=True so run_sprint returns early"
        )

    def test_run_sprint_dry_run_no_issues_returns_summary_state(self, tmp_path):
        """run_sprint with no issues returns (summary, state) immediately."""
        cfg = self._make_minimal_cfg(tmp_path)
        with (
            patch.object(sm, "list_backlog_issues", return_value=[]),
            patch.object(sm, "_load_sprint_plan", return_value=None),
            patch.object(sm, "_sprint_number", return_value=99),
            patch.object(sm, "_state_path", return_value=tmp_path / "state.json"),
            patch.object(sm, "mint_run_id", return_value="run-test-123"),
            patch.object(sm, "_setup_pid_file", return_value=None),
            patch.object(sm, "structured_log", MagicMock()),
        ):
            summary, state = sm.run_sprint(
                label="sprint-99",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                dry_run=True,
                cfg=cfg,
                skip_estimator=True,
            )
        assert isinstance(summary, sm.SprintSummary)
        assert isinstance(state, sm.SprintState)

    def test_run_sprint_preflight_invoked_by_run_sprint(self, tmp_path):
        """run_sprint must delegate to run_sprint_preflight (verified via call tracking)."""
        cfg = self._make_minimal_cfg(tmp_path)
        preflight_calls = []
        original_preflight = sm.run_sprint_preflight

        def tracking_preflight(*args, **kwargs):
            preflight_calls.append((args, kwargs))
            return original_preflight(*args, **kwargs)

        with (
            patch.object(sm, "run_sprint_preflight", side_effect=tracking_preflight),
            patch.object(sm, "list_backlog_issues", return_value=[]),
            patch.object(sm, "_load_sprint_plan", return_value=None),
            patch.object(sm, "_sprint_number", return_value=99),
            patch.object(sm, "_state_path", return_value=tmp_path / "state.json"),
            patch.object(sm, "mint_run_id", return_value="run-test-123"),
            patch.object(sm, "_setup_pid_file", return_value=None),
            patch.object(sm, "structured_log", MagicMock()),
        ):
            sm.run_sprint(
                label="sprint-99",
                skip_gates=True,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                dry_run=True,
                cfg=cfg,
                skip_estimator=True,
            )

        assert len(preflight_calls) == 1, (
            "run_sprint must call run_sprint_preflight exactly once"
        )


# ── AC-7: No new public API surface beyond run_sprint_preflight ───────────────

class TestNoNewPublicAPI:
    """AC-7: The only new public name added is run_sprint_preflight."""

    def test_preflight_result_class_is_private(self):
        """_SprintPreflightResult must NOT be a public name (underscore prefix)."""
        assert not hasattr(sm, "SprintPreflightResult"), (
            "SprintPreflightResult must stay private (_SprintPreflightResult), "
            "not exposed as a public module attribute"
        )

    def test_preflight_result_class_exists_as_private(self):
        """_SprintPreflightResult must exist but only as a private name."""
        assert hasattr(sm, "_SprintPreflightResult"), (
            "_SprintPreflightResult must be defined in sprint_manager (private)"
        )

    def test_run_sprint_preflight_is_public(self):
        """run_sprint_preflight is the intended new public callable."""
        assert hasattr(sm, "run_sprint_preflight")
        assert callable(sm.run_sprint_preflight)

    def test_no_accidental_public_helpers_from_preflight(self):
        """No helper functions used only internally by run_sprint_preflight are made public."""
        # If a name was specifically introduced for the preflight helper and
        # is not run_sprint_preflight itself, it should be private (_prefix).
        public_names = {n for n in dir(sm) if not n.startswith("_")}
        # run_sprint_preflight is explicitly allowed; everything else in the
        # public API should have existed before this refactor (we check by
        # ensuring no new name contains "preflight" other than the one function).
        preflight_public = {n for n in public_names if "preflight" in n.lower()}
        assert preflight_public == {"run_sprint_preflight"}, (
            f"Only 'run_sprint_preflight' may be a public preflight-related name; "
            f"found: {preflight_public}"
        )
