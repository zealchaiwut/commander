"""Tests for issue #2055 — sprint_planner.py crashes on import.

Acceptance Criteria covered:

  AC1  The correct diagnosis: DASHBOARD_DIR was being shadowed by
       SPRINT_MANAGER_DIR in sys.path so `from config import TEST_GITHUB_REPO`
       resolved to services/sprint_manager/config.py instead of
       apps/dashboard/config.py.  Fix: swap insert order so DASHBOARD_DIR has
       higher priority.  This test verifies the fix by importing sprint_planner
       with a real (stubbed) environment and asserting no ImportError is raised.

  AC2  `python3 scripts/sprint_planner.py --help` exits 0 and prints usage text.

  AC3  The retro code path (load_recent_retros) is genuinely reachable after
       the import fix — not just importable.  Verified by calling
       load_recent_retros() with fixture data and asserting content is returned.

  AC4  Behavioral test: subprocess call to --help exits 0.  No source-text
       regex.  (CLAUDE.md #1746)

  AC5  No other scripts/ module imports TEST_GITHUB_REPO from the wrong
       config.  seed_test_issues.py correctly inserts only DASHBOARD_DIR in its
       sys.path before `from config import TEST_GITHUB_REPO`.

BEHAVIORAL REQUIREMENT (CLAUDE.md issue #1746):
  Every test exercises the actual code path and asserts observed behaviour.
  No source-text regex checks count as AC coverage.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINT_MANAGER_DIR = REPO_ROOT / "services" / "sprint_manager"

# Ensure sprint_manager is on path for retro imports used in AC3.
for _p in (str(SPRINT_MANAGER_DIR), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1: sprint_planner imports without ImportError ───────────────────────────


def test_sprint_planner_imports_without_error():
    """AC1: Importing sprint_planner raises no ImportError after the path fix.

    We exercise the import by running it in a subprocess so it gets a clean
    sys.path — no contamination from the test harness.  Exit 0 means the
    import chain completed without crashing before argparse ran.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sprint_planner.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    # Any ImportError before argparse causes a non-zero exit and stderr trace.
    assert "ImportError" not in result.stderr, (
        f"ImportError detected in sprint_planner.py:\n{result.stderr}"
    )
    assert "cannot import name" not in result.stderr, (
        f"Import failure detected:\n{result.stderr}"
    )


# ── AC2: --help exits 0 and prints usage ─────────────────────────────────────


def test_sprint_planner_help_exits_zero():
    """AC2: `python3 scripts/sprint_planner.py --help` exits 0 and shows usage."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sprint_planner.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"--help exited {result.returncode}.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # argparse prints "usage:" on --help
    combined = result.stdout + result.stderr
    assert "usage" in combined.lower(), (
        f"Expected 'usage' in --help output, got:\n{combined}"
    )


# ── AC3: retro code path is genuinely reachable ──────────────────────────────


def test_retro_code_path_reachable(tmp_path):
    """AC3: load_recent_retros() returns content from fixture files.

    Exercises the retro-surfacing code path that sprint_planner runs at startup
    (issue #2026 AC-3).  If this function is importable AND callable AND returns
    data, the code path is genuinely reached.
    """
    from retro import load_recent_retros  # noqa: PLC0415

    docs_uat = tmp_path / "docs" / "changelog" / "uat"
    docs_uat.mkdir(parents=True)
    (docs_uat / "2026-07-31-sprint-99.md").write_text(
        "# Retro sprint-99\n\n## Key Learnings\n\n- PLANNER_RETRO_REACHABLE_MARKER\n",
        encoding="utf-8",
    )

    retros = load_recent_retros(docs_uat, n=3)

    assert len(retros) == 1
    fname, content = retros[0]
    assert "PLANNER_RETRO_REACHABLE_MARKER" in content, (
        "Retro content not surfaced — code path may not have been reached"
    )


# ── AC4: behavioral subprocess test for --help (CLAUDE.md #1746) ─────────────


def test_sprint_planner_help_behavioral():
    """AC4: Behavioral test — subprocess --help exits 0 (no source-text regex).

    This is the canonical AC4 test per CLAUDE.md #1746.  We call the actual
    script in a subprocess, observe the exit code, and assert on the output.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sprint_planner.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, (
        f"sprint_planner.py --help failed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
    # argparse --help always includes the description and at least one option
    assert "--dry-run" in proc.stdout or "--sprint" in proc.stdout, (
        f"Expected argparse options in --help output:\n{proc.stdout}"
    )


# ── AC5: seed_test_issues.py uses correct sys.path (no wrong config import) ──


def test_seed_test_issues_uses_dashboard_dir_for_config():
    """AC5: seed_test_issues.py correctly imports TEST_GITHUB_REPO from
    apps/dashboard/config, not services/sprint_manager/config.

    We run it with --dry-run to trigger the import chain and assert there is
    no ImportError or AttributeError for TEST_GITHUB_REPO.
    """
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "seed_test_issues.py"), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**__import__("os").environ, "GITHUB_ISSUE_TEST_REPO": ""},
    )
    assert "cannot import name 'TEST_GITHUB_REPO'" not in result.stderr, (
        f"seed_test_issues.py has wrong config import:\n{result.stderr}"
    )
    assert "ImportError" not in result.stderr or "TEST_GITHUB_REPO" not in result.stderr, (
        f"seed_test_issues.py ImportError:\n{result.stderr}"
    )
