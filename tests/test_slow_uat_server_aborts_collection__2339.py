"""AC tests for issue #2339: a slow/unreachable UAT server must not abort pytest
collection or disable the merge gate.

All tests exercise real code paths (import machinery, pytest subprocess runs,
git worktrees) rather than asserting that a symbol appears in source text
(CLAUDE.md #1746). No test in this file makes a live HTTP call — the whole
point of the ticket is that these tests must *not* depend on a reachable
server, so they simulate "server down" via an unreachable local port instead.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(REPO_ROOT), str(REPO_ROOT / "apps" / "dashboard")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# A port nothing is listening on, used to simulate "UAT server is down".
UNREACHABLE_PORT = "19999"
UNREACHABLE_ENV = {
    **os.environ,
    "UAT_BASE_URL": f"http://127.0.0.1:{UNREACHABLE_PORT}",
    "UAT_PORT": UNREACHABLE_PORT,
}


def _load_finish_feature():
    """Import scripts/finish_feature.py by path — `scripts` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "finish_feature_under_test_2339", REPO_ROOT / "scripts" / "finish_feature.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


from services.sprint_manager.merge_baseline import collection_failed  # noqa: E402


# ---------------------------------------------------------------------------
# AC: no test module performs I/O at import/collection time — fix
# test_modal_height_cap__1766.py specifically.
# ---------------------------------------------------------------------------

def test_modal_height_cap_module_makes_no_network_call_at_import_time():
    """Importing the module must not touch the network — I/O belongs inside a fixture.

    Before the fix, `pytestmark = pytest.mark.skipif(not _uat_available(), ...)`
    called httpx.get() as a default-argument-style side effect at module scope,
    so importing the module made a live call. Poison httpx.get during import and
    assert it is never invoked.
    """
    module_path = REPO_ROOT / "tests" / "test_modal_height_cap__1766.py"
    calls = []

    def _poison(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("network call made during module import")

    with patch("httpx.get", side_effect=_poison):
        spec = importlib.util.spec_from_file_location(
            "modal_height_cap_import_probe_2339", module_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # must not raise, must not call httpx.get

    assert calls == [], "httpx.get was called during import, not inside a fixture"
    # The autouse fixture must exist and be the thing that gates the tests now.
    assert hasattr(mod, "_require_uat")


# ---------------------------------------------------------------------------
# AC: a test that needs a live server skips rather than fails when it is
# unreachable — an unavailable dependency is not a regression in the branch.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel_path",
    [
        "tests/test_modal_height_cap__1766.py",
        "tests/test_logging_rotation_guard__818.py",
    ],
)
def test_touched_modules_skip_not_fail_when_uat_unreachable(rel_path):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", rel_path, "-q", "--no-header"],
        cwd=str(REPO_ROOT), env=UNREACHABLE_ENV, capture_output=True, text=True, timeout=60,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"expected a clean (skipped) run, got:\n{output}"
    assert "failed" not in output.lower(), f"expected no failures, got:\n{output}"
    assert "skipped" in output.lower(), f"expected the module to skip, got:\n{output}"


# ---------------------------------------------------------------------------
# AC: with nothing listening on the UAT port, `pytest tests/ --collect-only -q`
# exits 0 and collects the full suite.
# ---------------------------------------------------------------------------

def test_full_suite_collects_cleanly_with_uat_server_down():
    """Collection must not abort just because the UAT server is unreachable.

    Run against a clean worktree of the branch under test (HEAD) rather than the
    live working directory, so stray uncommitted files left by unrelated,
    concurrently-running tester sessions in this shared clone cannot pollute the
    result — this mirrors how finish_feature.py itself measures the suite.
    """
    with tempfile.TemporaryDirectory(prefix="commander-2339-collect-") as tmpdir:
        wt = str(Path(tmpdir) / "wt")
        subprocess.run(
            ["git", "worktree", "add", "--detach", wt, "HEAD"],
            cwd=str(REPO_ROOT), check=True, capture_output=True, text=True,
        )
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
                cwd=wt, env=UNREACHABLE_ENV, capture_output=True, text=True, timeout=120,
            )
            output = proc.stdout + proc.stderr
            assert proc.returncode == 0, f"collection did not exit cleanly:\n{output[-3000:]}"
            # Use the production abort detector (anchored to pytest's own status
            # line) rather than a bare substring check — this repo's suite
            # contains tests whose node ids and captured tracebacks legitimately
            # contain the word "Interrupted" without the suite having aborted.
            assert not collection_failed(output), f"collection aborted:\n{output[-3000:]}"
            assert "tests collected" in output, f"no collection summary found:\n{output[-3000:]}"
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
            )


# ---------------------------------------------------------------------------
# AC: the merge gate's scope decision (exclude live-HTTP tests entirely) is
# implemented, not just documented — the `live_http` marker actually deselects
# the allowlisted files under `-m "not live_http"`.
# ---------------------------------------------------------------------------

def test_live_http_marker_deselects_the_touched_files():
    """The two files this ticket fixes are grandfathered live-HTTP tests; the
    merge gate's `-m "not live_http"` scope must actually exclude them, which is
    what keeps a slow/flaky UAT server from ever entering the baseline delta.
    """
    targets = [
        "tests/test_modal_height_cap__1766.py",
        "tests/test_logging_rotation_guard__818.py",
    ]

    proc_included = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "--collect-only", "-q"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    assert proc_included.returncode == 0
    assert "no tests ran" not in proc_included.stdout.lower()

    proc_excluded = subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "-m", "not live_http", "--collect-only", "-q"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    combined = proc_excluded.stdout + proc_excluded.stderr
    # Exit code 5 is pytest's documented "no tests were collected" status — the
    # expected outcome once every item in both files is deselected by the marker.
    assert proc_excluded.returncode == 5, combined
    assert "no tests collected" in combined.lower(), (
        f"expected both allowlisted files to be fully deselected by -m 'not live_http':\n{combined}"
    )


# ---------------------------------------------------------------------------
# AC: the recorded baseline and the per-merge check agree to within a stated
# tolerance across two consecutive runs of the same ref — achieved by both
# sides defaulting to the same live-HTTP-excluding scope.
# ---------------------------------------------------------------------------

_FAKE_PYTEST_INI = "[pytest]\nmarkers =\n    live_http: live http test\n"
_FAKE_MIXED_TESTS = (
    "import pytest\n\n"
    "def test_always_passes():\n    assert True\n\n"
    "@pytest.mark.live_http\n"
    "def test_marked_live_http_would_fail():\n    assert False\n"
)


def test_record_test_baseline_default_excludes_live_http_marked_tests(tmp_path):
    """record_test_baseline.py, given no --pytest-args override, must not count
    a live_http-marked failure — otherwise the baseline is poisoned by flakiness.
    """
    tree = tmp_path / "repo"
    (tree / "tests").mkdir(parents=True)
    (tree / "pytest.ini").write_text(_FAKE_PYTEST_INI, encoding="utf-8")
    (tree / "tests" / "test_mix.py").write_text(_FAKE_MIXED_TESTS, encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "scripts" / "record_test_baseline.py"),
            "--repo", "zealchaiwut/live-http-default-2339",
            "--repo-root", str(tree),
            "--dry-run",
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "passed=1 failed=0" in combined, (
        f"expected the live_http-marked failure to be excluded by default:\n{combined}"
    )


def test_finish_feature_default_excludes_live_http_marked_tests(tmp_path, monkeypatch):
    """finish_feature.py's `_run_suite_on_branch`, given no override, must scope
    out live_http-marked tests the same way the recorder does — otherwise the
    two sides of the delta comparison are not measuring the same thing (#2331).
    """
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _run_git(upstream, "init", "-q", "-b", "main")
    _run_git(upstream, "config", "user.email", "t@example.com")
    _run_git(upstream, "config", "user.name", "t")
    (upstream / "tests").mkdir()
    (upstream / "pytest.ini").write_text(_FAKE_PYTEST_INI, encoding="utf-8")
    (upstream / "tests" / "test_mix.py").write_text(_FAKE_MIXED_TESTS, encoding="utf-8")
    _run_git(upstream, "add", "-A")
    _run_git(upstream, "commit", "-q", "-m", "init")

    workdir = tmp_path / "workdir"
    subprocess.run(
        ["git", "clone", "-q", str(upstream), str(workdir)],
        check=True, capture_output=True, text=True,
    )

    monkeypatch.chdir(workdir)
    ff = _load_finish_feature()
    monkeypatch.delenv("COMMANDER_BASELINE_PYTEST_ARGS", raising=False)

    output, timed_out = ff._run_suite_on_branch("main", timeout_seconds=60)
    assert not timed_out, output
    assert "passed=1 failed=0" in output or "1 passed" in output, (
        f"expected the live_http-marked failure to be excluded by default:\n{output}"
    )
    assert "1 failed" not in output, f"a live_http test counted as a failure:\n{output}"
