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


def test_2252_and_2253_do_not_spawn_full_tree_co_per_assertion():
    """AC1: deleted-module ACs use AST; full-tree --co is module-scoped once.

    Parses the test sources and asserts there is no per-test ``subprocess.run``
    of ``pytest <tests_dir> --co``. The only full-tree collect must live in a
    module-scoped fixture (or be absent).
    """
    for rel in (
        "tests/test_2252__delete_dispatch_pinned_tests.py",
        "tests/test_2253__post_cut_verification_pass.py",
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)

        # Must have an AST-based deleted-module check.
        assert "_imports_deleted_module" in src, (
            f"{rel} must use AST scanning for deleted-module imports (#2345)"
        )

        # collection_output fixture must be module-scoped.
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

        # No helper that builds a fresh full-tree --co command outside the
        # module-scoped fixture (the old _collect_errors pattern).
        assert "_collect_errors" not in src, (
            f"{rel} still defines _collect_errors — that was the 6×/4× full-tree "
            f"spawn source (#2345)"
        )


def test_polluting_fixtures_restore_services_modules_after_purge():
    """AC3: known polluters restore ``services.*`` after a temporary purge.

    The #2337 / #2345 failure class: later tests hold module objects bound at
    import time; a bare services.* purge rebuilds those modules under new ids
    so monkeypatches miss. Either remove the startswith purge, or use
    ``temporary_module_purge`` so originals are restored on exit.
    """
    offenders = []
    for rel in (
        "tests/test_643__editable_env_paths.py",
        "tests/test_773__prefill_env_field.py",
        "tests/test_693__git_rev_parse_timeout.py",
        "tests/test_681__scaffold_docs_settings.py",
        "tests/test_644__settings_sync.py",
        "tests/test_727__env_var_editor.py",
    ):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        has_startswith = (
            'startswith("services.")' in src or "startswith('services.')" in src
        )
        uses_restore = "temporary_module_purge" in src
        if has_startswith and not uses_restore:
            offenders.append(rel)
    assert not offenders, (
        "These fixtures still purge services.* without temporary_module_purge "
        f"and will reintroduce #2345 order pollution:\n" + "\n".join(offenders)
    )


def test_run_pytest_isolates_db_by_default(tmp_path):
    """Overlapping runs must not share /tmp/commander-pytest.db (#2345)."""
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
