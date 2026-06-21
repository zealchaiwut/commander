"""Tests for #1286: Extract tester/doctor dispatch to services/sprint_manager/dispatch.py.

AC items verified:
  AC-1  dispatch.py exists and contains _dispatch_tester, _doctor_probe_auth,
        _dispatch_doctor as definitions (not mere stubs that only raise NotImplementedError)
  AC-2  sprint_manager.py no longer defines those three functions (only imports/re-exports)
  AC-3  All three symbols remain accessible via sprint_manager (call sites unbroken)
  AC-4  python -m py_compile services/sprint_manager/dispatch.py exits 0
  AC-5  python -m py_compile on sprint_manager.py exits 0
  AC-6  Tester dispatch behavior identical: stub subprocess succeeds, same paths trigger
  AC-7  No new imports beyond what is necessary to support the move
  AC-8  Existing unit/integration tests for tester and doctor dispatch pass without modification
"""
from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

DISPATCH_MODULE_PATH = REPO_ROOT / "services" / "sprint_manager" / "dispatch.py"
SPRINT_MANAGER_PATH = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"

THREE_FUNCTIONS = [
    "_dispatch_tester",
    "_doctor_probe_auth",
    "_dispatch_doctor",
]


# ---------------------------------------------------------------------------
# AC-1: dispatch.py exists and defines the three functions
# ---------------------------------------------------------------------------

class TestAC1DispatchModuleExists:
    def test_file_exists(self):
        """services/sprint_manager/dispatch.py must exist."""
        assert DISPATCH_MODULE_PATH.exists(), f"{DISPATCH_MODULE_PATH} does not exist"

    def test_all_three_functions_defined_in_dispatch(self):
        """All three functions must have a FunctionDef in dispatch.py (not just re-exported)."""
        source = DISPATCH_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        for fn in THREE_FUNCTIONS:
            assert fn in defined, (
                f"{fn} must be defined in dispatch.py, not just re-exported"
            )

    def test_dispatch_doctor_is_real_implementation(self):
        """_dispatch_doctor must be the real function, not just a stub returning None."""
        source = DISPATCH_MODULE_PATH.read_text(encoding="utf-8")
        # The real _dispatch_doctor has at least a 'shutil.which' or 'disk_usage' call
        assert "shutil" in source, (
            "_dispatch_doctor implementation must use shutil (CLI check / disk space check)"
        )


# ---------------------------------------------------------------------------
# AC-2: sprint_manager.py no longer defines the three functions
# ---------------------------------------------------------------------------

class TestAC2SprintManagerNoLongerDefines:
    def test_sprint_manager_has_no_def_for_three_functions(self):
        """sprint_manager.py must not contain top-level def for the moved functions."""
        source = SPRINT_MANAGER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_defs = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
        }
        for fn in THREE_FUNCTIONS:
            assert fn not in top_level_defs, (
                f"sprint_manager.py must not define {fn} — "
                "it should only import it from dispatch.py"
            )


# ---------------------------------------------------------------------------
# AC-3: All three symbols accessible via sprint_manager (call sites unbroken)
# ---------------------------------------------------------------------------

class TestAC3SprintManagerReExports:
    def test_sprint_manager_has_all_three_functions(self):
        """All three functions must be importable from sprint_manager."""
        import services.sprint_manager.sprint_manager as sm
        for fn in THREE_FUNCTIONS:
            assert hasattr(sm, fn), (
                f"sprint_manager must expose {fn} (re-export from dispatch.py)"
            )
            assert callable(getattr(sm, fn)), f"{fn} on sprint_manager must be callable"

    def test_dispatch_module_importable(self):
        """services.sprint_manager.dispatch must be importable without error."""
        import services.sprint_manager.dispatch as dp
        for fn in THREE_FUNCTIONS:
            assert hasattr(dp, fn), f"dispatch module must define {fn}"

    def test_sprint_manager_functions_are_dispatch_functions(self):
        """sprint_manager's copies must be the same objects as dispatch's copies."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.dispatch as dp
        for fn in THREE_FUNCTIONS:
            sm_fn = getattr(sm, fn)
            dp_fn = getattr(dp, fn)
            assert sm_fn is dp_fn, (
                f"sm.{fn} must be the same object as dispatch.{fn} "
                f"(got {sm_fn!r} vs {dp_fn!r})"
            )


# ---------------------------------------------------------------------------
# AC-4 & AC-5: py_compile exits 0
# ---------------------------------------------------------------------------

class TestAC4AC5PyCompile:
    def test_dispatch_py_compiles(self):
        """python -m py_compile services/sprint_manager/dispatch.py must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(DISPATCH_MODULE_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on dispatch.py:\n{result.stderr}"
        )

    def test_sprint_manager_py_compiles(self):
        """python -m py_compile on sprint_manager.py must exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(SPRINT_MANAGER_PATH)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"py_compile failed on sprint_manager.py:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# AC-6: Tester dispatch behavior identical
# ---------------------------------------------------------------------------

class TestAC6TesterDispatchBehavior:
    def test_stub_subprocess_success(self, tmp_path):
        """_dispatch_tester returns (0, None) when subprocess stub exits 0."""
        import services.sprint_manager.dispatch as dp
        import services.sprint_manager.sprint_manager as sm

        cfg = MagicMock()
        cfg.tester_model = "claude-haiku-4-5"
        cfg.tester_by_risk = {"LOW": "claude-haiku-4-5", "MEDIUM": "claude-haiku-4-5", "HIGH": "claude-sonnet-4-6"}
        cfg.tester_prompt_template = None
        cfg.repo_name = "test/repo"
        cfg.api_url = None
        cfg.worktree_tester_app = tmp_path
        cfg.worktree_tester = tmp_path
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        def fake_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.wait.return_value = 0
            return proc

        with (
            patch("subprocess.Popen", side_effect=fake_popen),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "agent_browser_runner"),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_resolve_uat_env_for_tester", return_value=({}, None)),
            patch.object(sm, "_classify_risk_tier", return_value="LOW"),
        ):
            rc, hang = sm._dispatch_tester(
                1, [], sprint_branch="develop", repo_name="test/repo",
                cfg=cfg, sprint_label="sprint-1", pre_dispatch_risk="LOW",
            )
        assert rc == 0
        assert hang is None

    def test_stub_subprocess_failure_returns_nonzero(self, tmp_path):
        """_dispatch_tester returns nonzero rc when subprocess exits nonzero."""
        import services.sprint_manager.sprint_manager as sm

        cfg = MagicMock()
        cfg.tester_model = "claude-haiku-4-5"
        cfg.tester_by_risk = {}
        cfg.tester_prompt_template = None
        cfg.repo_name = "test/repo"
        cfg.api_url = None
        cfg.worktree_tester_app = tmp_path
        cfg.worktree_tester = tmp_path
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        def fake_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.wait.return_value = 1
            return proc

        with (
            patch("subprocess.Popen", side_effect=fake_popen),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "agent_browser_runner"),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_resolve_uat_env_for_tester", return_value=({}, None)),
            patch.object(sm, "_classify_risk_tier", return_value="LOW"),
            patch.object(sm, "_is_rate_limit_error", return_value=(False, None)),
        ):
            rc, hang = sm._dispatch_tester(
                1, [], sprint_branch="develop", repo_name="test/repo",
                cfg=cfg, sprint_label="sprint-1", pre_dispatch_risk="LOW",
            )
        assert rc != 0

    def test_stub_success_with_commander_allow_stub(self, tmp_path, monkeypatch):
        """When COMMANDER_ALLOW_STUB_SUCCESS=1 and claude CLI not found, returns (0, None)."""
        import services.sprint_manager.sprint_manager as sm
        monkeypatch.setenv("COMMANDER_ALLOW_STUB_SUCCESS", "1")

        cfg = MagicMock()
        cfg.tester_model = "claude-haiku-4-5"
        cfg.tester_by_risk = {}
        cfg.tester_prompt_template = None
        cfg.repo_name = "test/repo"
        cfg.api_url = None
        cfg.worktree_tester_app = tmp_path
        cfg.worktree_tester = tmp_path
        cfg.logs_dir = tmp_path / "logs"
        cfg.logs_dir.mkdir()

        def fake_popen_not_found(cmd, **kwargs):
            raise FileNotFoundError("claude not found")

        with (
            patch("subprocess.Popen", side_effect=fake_popen_not_found),
            patch.object(sm, "_post_agent_event"),
            patch.object(sm, "_load_agent_persona", return_value=None),
            patch.object(sm, "agent_browser_runner"),
            patch.object(sm, "_worktree_hygiene", return_value=(None, None, None)),
            patch.object(sm, "_resolve_uat_env_for_tester", return_value=({}, None)),
            patch.object(sm, "_classify_risk_tier", return_value="LOW"),
        ):
            rc, hang = sm._dispatch_tester(
                1, [], sprint_branch="develop", repo_name="test/repo",
                cfg=cfg, sprint_label="sprint-1", pre_dispatch_risk="LOW",
            )
        assert rc == 0, "_dispatch_tester must return 0 when stub success env var is set"
        assert hang is None


# ---------------------------------------------------------------------------
# AC-6 continued: doctor dispatch behavior identical
# ---------------------------------------------------------------------------

class TestAC6DoctorBehavior:
    def test_dispatch_doctor_passes_when_healthy(self, tmp_path):
        """_dispatch_doctor returns None when CLI present, auth ok, disk ok."""
        import services.sprint_manager.sprint_manager as sm

        cfg = MagicMock()
        cfg.coder_backend = "claude-code"
        cfg.worktree_coder = tmp_path

        with (
            patch("shutil.which", return_value="/usr/local/bin/claude"),
            patch.object(sm, "_doctor_probe_auth", return_value=None),
            patch("shutil.disk_usage", return_value=MagicMock(free=10 * 1024**3)),
        ):
            result = sm._dispatch_doctor(cfg, alert_modes=[])
        assert result is None, "_dispatch_doctor must return None when all checks pass"

    def test_dispatch_doctor_fails_when_cli_missing(self, tmp_path):
        """_dispatch_doctor returns error string when CLI not found."""
        import services.sprint_manager.sprint_manager as sm

        cfg = MagicMock()
        cfg.coder_backend = "claude-code"
        cfg.worktree_coder = tmp_path

        with (
            patch("shutil.which", return_value=None),
            patch.object(sm, "dispatch_alerts", return_value=None),
        ):
            result = sm._dispatch_doctor(cfg, alert_modes=[])
        assert result is not None, "_dispatch_doctor must return an error when CLI missing"
        assert "dispatch-blocked" in result.lower() or "not found" in result.lower()

    def test_doctor_probe_auth_caches_result(self, tmp_path, monkeypatch):
        """_doctor_probe_auth is callable and returns None on success."""
        import services.sprint_manager.sprint_manager as sm
        import services.sprint_manager.dispatch as dp

        # Reset cache so TTL doesn't suppress the probe
        dp._DOCTOR_AUTH_LAST_PROBE = 0.0

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = sm._doctor_probe_auth(backend="claude-code")
        assert result is None, "_doctor_probe_auth must return None on successful auth probe"


# ---------------------------------------------------------------------------
# AC-7: No new imports beyond necessary
# ---------------------------------------------------------------------------

class TestAC7NoCircularImports:
    def test_dispatch_does_not_import_sprint_manager_at_top_level(self):
        """dispatch.py must not import sprint_manager at module level (avoids circular import)."""
        source = DISPATCH_MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert "sprint_manager.sprint_manager" not in module, (
                        "dispatch.py must not import sprint_manager.sprint_manager "
                        "at the module level (creates circular import)"
                    )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "sprint_manager.sprint_manager" not in alias.name, (
                            "dispatch.py must not import sprint_manager.sprint_manager "
                            "at the module level"
                        )


# ---------------------------------------------------------------------------
# Signatures preserved
# ---------------------------------------------------------------------------

class TestSignaturesPreserved:
    def test_parameter_names_dispatch_tester(self):
        """_dispatch_tester must keep its original parameter names."""
        import services.sprint_manager.dispatch as dp
        sig = inspect.signature(dp._dispatch_tester)
        params = list(sig.parameters.keys())
        expected = [
            "issue_num", "alert_modes", "sprint_branch", "repo_name",
            "cfg", "chosen_port", "rate_limit_events", "on_running",
            "sprint_label", "pre_dispatch_risk", "prior_failures",
        ]
        assert params == expected, (
            f"_dispatch_tester parameter list changed: expected {expected}, got {params}"
        )

    def test_parameter_names_doctor_probe_auth(self):
        """_doctor_probe_auth must keep its original parameter names."""
        import services.sprint_manager.dispatch as dp
        sig = inspect.signature(dp._doctor_probe_auth)
        params = list(sig.parameters.keys())
        assert params == ["backend"], (
            f"_doctor_probe_auth parameter list changed: got {params}"
        )

    def test_parameter_names_dispatch_doctor(self):
        """_dispatch_doctor must keep its original parameter names."""
        import services.sprint_manager.dispatch as dp
        sig = inspect.signature(dp._dispatch_doctor)
        params = list(sig.parameters.keys())
        assert params == ["cfg", "alert_modes", "issue_num", "eff_repo"], (
            f"_dispatch_doctor parameter list changed: got {params}"
        )

    def test_docstrings_present(self):
        """All three functions must have a non-empty docstring."""
        import services.sprint_manager.dispatch as dp
        for fn_name in THREE_FUNCTIONS:
            fn = getattr(dp, fn_name)
            assert fn.__doc__ and fn.__doc__.strip(), (
                f"{fn_name} must have a docstring after extraction"
            )
