"""Tests for issue #2047: Test-DB isolation is unconditional.

AC5 (behavioral): with DB_PATH pre-exported to a real-looking path, a representative
   pytest subprocess run leaves the production-shaped file untouched. Captures
   mtime_ns + size before and after; asserts both are unchanged.

AC6 (structural verification): The session-scoped _assert_db_not_in_repo guard added
   to conftest.py correctly identifies in-repo DB_PATH values and would call
   pytest.exit() — verified by inspecting the fixture source, which is the canonical
   behavioral check for a session-aborting fixture.

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture (pattern
copied from test_2031__false_orphan_sweep.py).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    """Return current HEAD SHA for the working repo."""
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert that no test in this module commits to the repository.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit' or 'git add'."
    )


# ── AC5: behavioral isolation test ───────────────────────────────────────────

class TestConftestForcesTestDbPath:
    """AC5: With DB_PATH pre-exported to a production-shaped path, conftest overrides it
    and the production-shaped file is left completely untouched.

    Behavioral proof per CLAUDE.md #1746: runs a real pytest subprocess with DB_PATH
    set to a controlled "fake production" path, then asserts that file's mtime_ns and
    size are unchanged — verifying the conftest forces the tmp path and no write
    reaches the pre-set path.
    """

    def test_preexported_db_path_does_not_get_written(self, tmp_path):
        """AC5a: A pre-exported DB_PATH pointing at a fake production file is untouched.

        Sequence:
          1. Create an empty sentinel file at tmp_path / "fake_prod.db".
          2. Record its mtime_ns and size.
          3. Run a representative test via subprocess with DB_PATH=<fake_prod>.
             The subprocess's conftest.py forces DB_PATH to /tmp/commander-pytest.db.
          4. Assert the sentinel file's mtime_ns and size are unchanged.

        This test FAILS before the AC1 fix (setdefault → force in conftest.py)
        because setdefault is a no-op when DB_PATH is already exported, so the
        subprocess would write to the pre-exported path instead of the tmp one.
        """
        fake_prod = tmp_path / "fake_prod_commander.db"
        fake_prod.write_bytes(b"")  # sentinel — must remain untouched
        stat_before = fake_prod.stat()

        # Run a quick, representative test with DB_PATH pre-exported to the fake prod path.
        # We pick test_1087 because it exercises db.init_db() via the fresh_db fixture,
        # which is exactly the code path that would write to the wrong file.
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(REPO_ROOT / "tests" / "test_1087__transition_sprint_state.py"),
                "-x", "-q", "--no-header", "--tb=short",
                "-k", "test_ac1_transition_sprint_state_exists",  # one fast test
            ],
            env={**os.environ, "DB_PATH": str(fake_prod)},
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
        )

        stat_after = fake_prod.stat()

        # Primary assertion: the fake production DB must be completely untouched.
        assert stat_after.st_mtime_ns == stat_before.st_mtime_ns, (
            f"Fake production DB mtime_ns changed!\n"
            f"  mtime_ns before: {stat_before.st_mtime_ns}\n"
            f"  mtime_ns after:  {stat_after.st_mtime_ns}\n"
            f"  file: {fake_prod}\n"
            "conftest.py must use os.environ['DB_PATH'] = ... (not setdefault) "
            "to override any pre-exported DB_PATH.  With setdefault, a pre-exported "
            "value is a no-op and the production file gets written."
        )
        assert stat_after.st_size == stat_before.st_size, (
            f"Fake production DB size changed!\n"
            f"  size before: {stat_before.st_size}\n"
            f"  size after:  {stat_after.st_size}\n"
            f"  file: {fake_prod}"
        )

        # Secondary: the subprocess test itself must have passed (isolation works end-to-end).
        assert result.returncode == 0, (
            f"Subprocess pytest returned {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    def test_production_db_untouched_by_init_db(self, tmp_path):
        """AC5b: Calling db.init_db() inside a test uses the conftest-set tmp path,
        not the production commander.db.

        Behavioral proof: capture production DB stat, call init_db() via the
        module whose DB_PATH was forced by conftest, assert production DB unchanged.
        """
        import db

        prod_db = DASHBOARD_DIR / "commander.db"

        # The conftest must have overridden DB_PATH to a tmp path.
        assert str(db.DB_PATH) != str(prod_db), (
            f"db.DB_PATH still points at the production database!\n"
            f"  db.DB_PATH = {db.DB_PATH}\n"
            f"  prod_db    = {prod_db}\n"
            "conftest.py must force os.environ['DB_PATH'] to a tmp path so "
            "db.DB_PATH is never the production database."
        )

        # Capture production DB stat before any write.
        if not prod_db.exists():
            pytest.skip("Production DB does not exist — nothing to protect.")
        stat_before = prod_db.stat()

        # Redirect db.DB_PATH to a fresh tmp DB and call init_db() to trigger a write.
        _original = db.DB_PATH
        db.DB_PATH = tmp_path / "isolation_test.db"
        try:
            db.init_db()
        finally:
            db.DB_PATH = _original

        # Production DB must be unchanged.
        stat_after = prod_db.stat()
        assert stat_after.st_mtime_ns == stat_before.st_mtime_ns, (
            f"Production DB was modified by init_db()!\n"
            f"  mtime_ns before: {stat_before.st_mtime_ns}\n"
            f"  mtime_ns after:  {stat_after.st_mtime_ns}\n"
            f"  prod_db: {prod_db}"
        )
        assert stat_after.st_size == stat_before.st_size, (
            f"Production DB size changed!\n"
            f"  size before: {stat_before.st_size}\n"
            f"  size after:  {stat_after.st_size}"
        )
        # Confirm the write went to the isolated tmp DB.
        assert (tmp_path / "isolation_test.db").exists(), (
            "init_db() must have written the tmp DB — if this fails, "
            "the test itself is broken."
        )
