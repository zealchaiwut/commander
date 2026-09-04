"""Behavioral tests for issue #2345 — pytest runaway spawn + non-determinism.

AC coverage (behavioral, no live HTTP):
- AC1/AC2: ``run_pytest`` kills the whole process group on timeout, so a nested
  grandchild pytest cannot survive as a PPID-1 orphan.
- AC1: meta-tests #2252/#2253 no longer call full-tree ``pytest --co`` per
  assertion — deleted-module checks use AST; collection is module-scoped once.
- AC3: fixtures that used to purge every ``services.*`` module no longer do so
  (the #2337 / #2345 order-pollution class).
"""
from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services.sprint_manager.pytest_runner import run_pytest  # noqa: E402


def test_run_pytest_kills_process_group_on_timeout(tmp_path):
    """AC1/AC2: on timeout, grandchildren in the same session are SIGKILL'd.

    Reproduces the #2345 orphan path: outer pytest (via run_pytest) spawns a
    long-lived child; TimeoutExpired must leave no survivors.
    """
    child_script = tmp_path / "long_child.py"
    child_script.write_text(textwrap.dedent("""\
        import os, time, sys
        # Print our pid so the parent can verify we die.
        print(f"CHILD_PID={os.getpid()}", flush=True)
        time.sleep(60)
        print("CHILD_SURVIVED", flush=True)
    """))

    # A tiny "suite" that itself spawns the long child via Popen (no wait) —
    # mimicking a meta-test that starts a nested pytest and gets orphaned.
    nest_test = tmp_path / "test_nest.py"
    nest_test.write_text(textwrap.dedent(f"""\
        import subprocess, sys, time
        def test_spawn_orphan():
            proc = subprocess.Popen(
                [sys.executable, {str(child_script)!r}],
                start_new_session=False,  # stay in parent's process group
            )
            # Keep the pytest worker alive long enough for the outer timeout
            # to fire while the grandchild is still running.
            time.sleep(30)
            proc.wait(timeout=1)
    """))

    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_pytest(
            [str(nest_test), "-q", "--tb=no"],
            cwd=str(tmp_path),
            timeout=2,
            isolate_db=False,
        )
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"timeout did not fire promptly: {elapsed:.1f}s"

    # Give the kernel a beat to reap, then assert no leftover Python is still
    # sleeping on our child_script.
    time.sleep(0.5)
    check = subprocess.run(
        ["pgrep", "-af", str(child_script)],
        capture_output=True, text=True,
    )
    survivors = [
        line for line in (check.stdout or "").splitlines()
        if str(child_script) in line and "pgrep" not in line
    ]
    assert not survivors, (
        f"grandchild survived the process-group kill (PPID-1 orphan risk):\n"
        + "\n".join(survivors)
    )


def test_2252_and_2253_cache_full_tree_co_once():
    """AC1: full-tree ``--co`` is module-scoped once, not per assertion.

    Parses the test sources and asserts there is no `_collect_errors` helper
    (the old 6×/4× full-tree spawn source). Collection must live in a
    module-scoped ``collection_output`` fixture.
    """
    for rel in (
        "tests/test_2252__delete_dispatch_pinned_tests.py",
        "tests/test_2253__post_cut_verification_pass.py",
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)

        found_module_scoped = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "collection_output":
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and any(
                        isinstance(kw, ast.keyword)
                        and kw.arg == "scope"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value == "module"
                        for kw in dec.keywords
                    ):
                        found_module_scoped = True
        assert found_module_scoped, (
            f"{rel}: collection_output must be @pytest.fixture(scope='module')"
        )
        assert "_collect_errors" not in src, (
            f"{rel} still defines _collect_errors — that was the 6×/4× full-tree "
            f"spawn source (#2345)"
        )


def test_suite_launchers_use_process_group_runner():
    """AC1/AC2: finish_feature / baseline / gate / health use run_pytest (#2345)."""
    files = (
        "scripts/finish_feature.py",
        "scripts/record_test_baseline.py",
        "services/sprint_manager/dispatch_runner.py",
        "services/sprint_manager/suite_health_gate.py",
    )
    for rel in files:
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "pytest_runner" in src or "run_pytest" in src, (
            f"{rel} must launch the suite via pytest_runner.run_pytest so "
            f"timeouts kill the whole process group (#2345)"
        )


def test_run_pytest_isolates_db_by_default(tmp_path):
    """AC3 amplifier: overlapping runs must not share /tmp/commander-pytest.db."""
    probe = tmp_path / "test_probe_db.py"
    probe.write_text(textwrap.dedent("""\
        import os
        def test_db_path_is_isolated():
            db = os.environ.get("COMMANDER_TEST_DB") or os.environ.get("DB_PATH")
            assert db, "COMMANDER_TEST_DB/DB_PATH must be set"
            assert db != "/tmp/commander-pytest.db", db
            assert "commander-pytest-db-" in db or db.endswith(".db")
    """))
    result = run_pytest([str(probe), "-q"], cwd=str(tmp_path), timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
