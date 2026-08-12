"""Tests for issue #2237 — Decouple summary.py from modules slated for deletion.

AC coverage:
  AC1  summary.py no longer imports alerts, state, or timekeeping (structural + runtime)
  AC2  helpers actually used are moved into paths.py and importable from there
  AC3  imports of paths, retro, and agent_browser_runner are retained in summary.py
  AC4  generate_sprint_summary output is structurally correct (same sections as before)
"""
from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()


# ── helpers ───────────────────────────────────────────────────────────────────

def _module_level_imports(src: str) -> list[tuple[str, int]]:
    """Return (module_name, lineno) for all top-level imports in src.

    Imports inside ``if TYPE_CHECKING:`` blocks are excluded because those are
    not evaluated at runtime and do not create hard dependencies.
    """
    tree = ast.parse(src)
    results: list[tuple[str, int]] = []

    # Collect line ranges of TYPE_CHECKING blocks so we can exclude them.
    type_checking_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        # Detect `if TYPE_CHECKING:` — the test value is a Name("TYPE_CHECKING").
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)
        # Also handle `if typing.TYPE_CHECKING:`.
        elif isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
            for child in ast.walk(node):
                if hasattr(child, "lineno"):
                    type_checking_lines.add(child.lineno)

    for node in ast.walk(tree):
        if not hasattr(node, "lineno"):
            continue
        if node.lineno in type_checking_lines:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom) and node.module:
            results.append((node.module, node.lineno))

    return results


# ── AC1: no module-level imports of alerts, state, or timekeeping ────────────

class TestAC1NoForbiddenImports:
    """AC1: summary.py must not import alerts, state, or timekeeping outside TYPE_CHECKING."""

    @pytest.fixture(autouse=True)
    def _src(self):
        self._summary_src = (SM_DIR / "summary.py").read_text(encoding="utf-8")

    def test_no_alerts_import(self):
        bad = [
            (m, ln) for m, ln in _module_level_imports(self._summary_src)
            if "sprint_manager.alerts" in m or m == "alerts"
        ]
        assert not bad, (
            f"summary.py has runtime import of alerts at line(s) "
            f"{[ln for _, ln in bad]}; must be removed"
        )

    def test_no_state_import(self):
        bad = [
            (m, ln) for m, ln in _module_level_imports(self._summary_src)
            if "sprint_manager.state" in m or m == "state"
        ]
        assert not bad, (
            f"summary.py has runtime import of state at line(s) "
            f"{[ln for _, ln in bad]}; must be in TYPE_CHECKING block only"
        )

    def test_no_timekeeping_import(self):
        bad = [
            (m, ln) for m, ln in _module_level_imports(self._summary_src)
            if "timekeeping" in m
        ]
        assert not bad, (
            f"summary.py has runtime import of timekeeping at line(s) "
            f"{[ln for _, ln in bad]}; helpers must be moved to paths.py"
        )

    def test_summary_imports_cleanly_with_deleted_modules_blocked(self):
        """summary.py must import without error when alerts, state, timekeeping are absent."""
        script = textwrap.dedent(f"""\
            import sys, types
            from pathlib import Path

            repo = Path({str(REPO_ROOT)!r})
            sys.path.insert(0, str(repo))
            sys.path.insert(0, str(repo / "apps" / "dashboard"))
            sys.path.insert(0, str(repo / "services" / "sprint_manager"))

            # Provide a minimal github_client stub
            gc = types.ModuleType("github_client")
            gc.GitHubClient = object
            sys.modules["github_client"] = gc

            # Block the three modules being deleted
            BLOCKED = {{
                "services.sprint_manager.alerts",
                "services.sprint_manager.timekeeping",
                "services.sprint_manager.state",
            }}

            import importlib.abc, importlib.machinery

            class _Blocker(importlib.abc.MetaPathFinder, importlib.abc.Loader):
                def find_module(self, name, path=None):
                    return self if name in BLOCKED else None
                def find_spec(self, name, path, target=None):
                    if name in BLOCKED:
                        spec = importlib.machinery.ModuleSpec(name, self)
                        return spec
                    return None
                def create_module(self, spec): return None
                def exec_module(self, module):
                    raise ImportError(f"{{module.__name__}} is deleted (test block)")

            sys.meta_path.insert(0, _Blocker())

            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "_summary_2237",
                str(repo / "services" / "sprint_manager" / "summary.py"),
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            print("OK")
        """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"summary.py failed to import when deleted modules are blocked:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout


# ── AC2: timekeeping helpers now live in paths.py ────────────────────────────

class TestAC2HelpersInPaths:
    """AC2: the helpers used from timekeeping must now be in paths.py."""

    def test_sprints_dir_in_paths(self):
        from services.sprint_manager.paths import SPRINTS_DIR
        assert SPRINTS_DIR is not None
        assert isinstance(SPRINTS_DIR, Path)

    def test_bangkok_tz_in_paths(self):
        from services.sprint_manager.paths import _BANGKOK_TZ
        from datetime import timedelta, timezone
        assert _BANGKOK_TZ == timezone(timedelta(hours=7))

    def test_utcnow_in_paths(self):
        from services.sprint_manager.paths import _utcnow
        result = _utcnow()
        assert isinstance(result, str)
        # Must end in Z and have expected UTC format
        assert result.endswith("Z"), f"_utcnow() must end with Z: {result!r}"
        assert "T" in result

    def test_bangkok_now_in_paths(self):
        from services.sprint_manager.paths import _bangkok_now
        result = _bangkok_now()
        assert isinstance(result, str)
        assert "+07:00" in result, f"_bangkok_now() must include +07:00: {result!r}"

    def test_to_bangkok_in_paths(self):
        from services.sprint_manager.paths import _to_bangkok
        result = _to_bangkok("2026-01-15T10:00:00Z")
        assert "+07:00" in result, f"_to_bangkok result must include +07:00: {result!r}"
        # 10:00 UTC → 17:00 Bangkok (UTC+7)
        assert "17:00:00" in result, f"Expected 17:00:00 Bangkok time: {result!r}"

    def test_to_bangkok_fallback_on_invalid(self):
        from services.sprint_manager.paths import _to_bangkok
        # Invalid input must not raise — must return a valid Bangkok timestamp
        result = _to_bangkok("not-a-date")
        assert "+07:00" in result


# ── AC3: paths, retro, agent_browser_runner imports retained ─────────────────

class TestAC3RetainedImports:
    """AC3: summary.py must still import paths, retro, and agent_browser_runner."""

    def _summary_imports(self) -> list[str]:
        """Return all module names AND imported names from any import statement."""
        src = (SM_DIR / "summary.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        tokens: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                tokens.append(node.module)
                for alias in node.names:
                    tokens.append(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    tokens.append(alias.name)
        return tokens

    def test_paths_import_retained(self):
        mods = self._summary_imports()
        assert any("paths" in m for m in mods), (
            "summary.py must retain import from paths"
        )

    def test_retro_import_retained(self):
        mods = self._summary_imports()
        assert any("retro" in m for m in mods), (
            "summary.py must retain import from retro"
        )

    def test_agent_browser_runner_import_retained(self):
        mods = self._summary_imports()
        assert any("agent_browser_runner" in m for m in mods), (
            "summary.py must retain import of agent_browser_runner"
        )


# ── AC4: generate_sprint_summary output is structurally unchanged ─────────────

class TestAC4SummaryOutputUnchanged:
    """AC4: generate_sprint_summary must produce the same sections as before."""

    def _make_state(self):
        """Build a minimal SprintState for testing."""
        from services.sprint_manager.state import SprintState, IssueState
        state = SprintState(sprint_label="sprint-2237", sprint_number=2237)
        state.start_timestamp = "2026-01-15T10:00:00Z"
        issue = IssueState(number=1, title="Test feature", status="done")
        state.issues = [issue]
        state.total_tokens_in = 100
        state.total_tokens_out = 50
        return state

    def _setup_sm_ref(self):
        """Set up minimal sprint_manager mock so _sm_ref resolves."""
        import services.sprint_manager.sprint_manager as sm_mod
        return sm_mod

    def test_generate_sprint_summary_returns_string(self):
        """generate_sprint_summary must return a non-empty string."""
        if "github_client" not in sys.modules:
            sys.modules["github_client"] = MagicMock()

        from services.sprint_manager.summary import generate_sprint_summary
        state = self._make_state()

        with patch("services.sprint_manager.summary._sm_ref") as mock_ref:
            mock_ref._r = lambda repo: repo or "o/r"
            mock_ref.FailureCategory = MagicMock(
                HANG="HANG", CRASH="CRASH", GATE_FAIL="GATE_FAIL",
                TESTER_REJECTED="TESTER_REJECTED", RETRY_EXHAUSTED="RETRY_EXHAUSTED",
                CODER_NO_WORK="CODER_NO_WORK", PYTEST_FAIL="PYTEST_FAIL",
                LINT_FAIL="LINT_FAIL", MERGE_CONFLICT="MERGE_CONFLICT",
            )
            mock_ref._git_verified_shipped_issues = lambda s, t: [
                i for i in s.issues if i.status == "done"
            ]
            mock_ref._RATE_LIMIT_MAX_RETRIES = 3

            result = generate_sprint_summary(
                state,
                elapsed_secs=300.0,
                end_reason="complete",
                repo_name="o/r",
            )

        assert isinstance(result, str)
        assert len(result) > 0

    def test_generate_summary_contains_expected_sections(self):
        """Summary output must contain the standard markdown sections."""
        if "github_client" not in sys.modules:
            sys.modules["github_client"] = MagicMock()

        from services.sprint_manager.summary import generate_sprint_summary
        state = self._make_state()

        with patch("services.sprint_manager.summary._sm_ref") as mock_ref:
            mock_ref._r = lambda repo: repo or "o/r"
            mock_ref.FailureCategory = MagicMock(
                HANG="HANG", CRASH="CRASH", GATE_FAIL="GATE_FAIL",
                TESTER_REJECTED="TESTER_REJECTED", RETRY_EXHAUSTED="RETRY_EXHAUSTED",
                CODER_NO_WORK="CODER_NO_WORK", PYTEST_FAIL="PYTEST_FAIL",
                LINT_FAIL="LINT_FAIL", MERGE_CONFLICT="MERGE_CONFLICT",
            )
            mock_ref._git_verified_shipped_issues = lambda s, t: [
                i for i in s.issues if i.status == "done"
            ]
            mock_ref._RATE_LIMIT_MAX_RETRIES = 3

            result = generate_sprint_summary(
                state,
                elapsed_secs=600.0,
                end_reason="complete",
                repo_name="o/r",
            )

        expected_sections = [
            "## Sprint 2237",
            "## Pending UAT Review",
            "## What Didn't Ship",
            "## Stats",
            "## Per-Agent Durations",
            "## Carried Over",
            "## Key Learnings",
            "## Links",
            "## Next Step",
        ]
        for section in expected_sections:
            assert section in result, (
                f"Expected section {section!r} missing from generate_sprint_summary output"
            )

    def test_helpers_produce_identical_output_to_timekeeping(self):
        """_utcnow, _bangkok_now, _to_bangkok in paths.py must behave identically
        to the timekeeping originals (verified by calling both and comparing types
        and structural properties, since timestamps depend on current time)."""
        from services.sprint_manager.paths import (
            _utcnow as paths_utcnow,
            _bangkok_now as paths_bangkok_now,
            _to_bangkok as paths_to_bangkok,
        )
        from services.sprint_manager.timekeeping import (
            _utcnow as tk_utcnow,
            _bangkok_now as tk_bangkok_now,
            _to_bangkok as tk_to_bangkok,
        )

        # Same structural format
        assert paths_utcnow().endswith("Z")
        assert tk_utcnow().endswith("Z")
        assert "+07:00" in paths_bangkok_now()
        assert "+07:00" in tk_bangkok_now()

        ts = "2026-06-15T08:30:00Z"
        assert paths_to_bangkok(ts) == tk_to_bangkok(ts), (
            "paths._to_bangkok must produce identical output to timekeeping._to_bangkok"
        )
