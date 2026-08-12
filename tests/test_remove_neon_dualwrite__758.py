"""Tests for issue #758: Remove Neon dual-write; add one-shot export script.

This ticket is a backend refactor: Neon stops being a live dual-write target and
becomes an optional, on-demand export. All acceptance criteria are code-structure /
CLI-script / import-safety checks — none are reachable over HTTP — so these tests
exercise the source tree and the export script directly rather than the UAT HTTP API.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
SM = REPO_ROOT / "services" / "sprint_manager"


def _run(args, env=None, cwd=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(
        args, cwd=str(cwd or REPO_ROOT), env=e,
        capture_output=True, text=True, timeout=60,
    )


# --- Acceptance Criteria ---

def test_remove_neon_dualwrite__no_live_call_sites():
    # AC: grep 'sprint_repo|sync_projects_to_neon' finds no live call sites in
    # normal operation code paths. Normal-operation modules = server.py and
    # sprint_manager.py (the always-on request/sprint paths). Explicit one-shot
    # scripts (export_to_neon.py, migrate_sprints_to_neon.py) are allowed.
    normal_paths = [
        REPO_ROOT / "apps" / "dashboard" / "server.py",
        SM / "sprint_manager.py",
    ]
    offenders = []
    for p in normal_paths:
        text = p.read_text()
        for ln, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "sprint_repo" in line or "sync_projects_to_neon" in line:
                offenders.append(f"{p.name}:{ln}: {stripped}")
    assert not offenders, "live Neon call sites in normal paths:\n" + "\n".join(offenders)


def test_remove_neon_dualwrite__no_top_level_sprint_repo_import():
    # AC (edge): the dual-write module sprint_repo must not be imported at module
    # load time by the normal-operation modules.
    for p in (REPO_ROOT / "apps" / "dashboard" / "server.py", SM / "sprint_manager.py"):
        text = p.read_text()
        assert "import sprint_repo" not in text and "import services.sprint_manager.sprint_repo" not in text, \
            f"{p.name} still imports sprint_repo"


def test_remove_neon_dualwrite__missing_database_url_no_errors():
    # AC: missing DATABASE_URL causes zero warnings/errors/exceptions on normal
    # code paths. Importing the core modules with DATABASE_URL unset must succeed
    # cleanly (no raised exception, no Neon warning on stderr).
    code = (
        "import importlib;"
        "import services.sprint_manager.sprint_manager;"
        "import services.sprint_manager.neon_db;"
        "print('IMPORT_OK')"
    )
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT),
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"import failed:\n{r.stderr}"
    assert "IMPORT_OK" in r.stdout
    assert "DATABASE_URL" not in r.stderr, f"Neon warning leaked on import:\n{r.stderr}"
    assert "Traceback" not in r.stderr, f"exception on import:\n{r.stderr}"


def test_remove_neon_dualwrite__engine_cached_at_module_level():
    # AC: neon_db.py engine is cached at module level (no per-call create_engine()).
    sys.path.insert(0, str(REPO_ROOT))
    import importlib
    import services.sprint_manager.neon_db as neon_db
    importlib.reload(neon_db)
    # module-level cache slot exists and starts empty
    assert hasattr(neon_db, "_engine")
    neon_db._engine = None
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # file-backed sqlite uses a QueuePool (like real Neon/Postgres) so the
        # production pool_size/max_overflow kwargs apply; :memory: would use a
        # SingletonThreadPool that rejects them.
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(td) / 'cache.db'}"
        try:
            e1 = neon_db.get_engine()
            e2 = neon_db.get_engine()
            assert e1 is e2, "get_engine() returned a new engine — not cached"
        finally:
            os.environ.pop("DATABASE_URL", None)
            neon_db._engine = None


def test_remove_neon_dualwrite__export_script_exists_and_runnable():
    # AC: scripts/export_to_neon.py exists, is runnable, exits cleanly with a set
    # DATABASE_URL. The export uses portable SQL so a SQLite DATABASE_URL is a
    # valid target for the runnable check; exit code must be 0.
    script = SCRIPTS / "archive" / "export_to_neon.py"
    assert script.exists(), "scripts/export_to_neon.py missing"
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "neon_target.db"
        r = _run(
            [sys.executable, str(script)],
            env={"DATABASE_URL": f"sqlite:///{target}", "PYTHONPATH": str(REPO_ROOT)},
        )
    assert r.returncode == 0, f"export script failed (exit {r.returncode}):\n{r.stderr}\n{r.stdout}"
    assert "Done." in r.stdout


def test_remove_neon_dualwrite__export_script_dry_run():
    # AC (edge): export script supports a no-write dry-run path and exits 0.
    script = SCRIPTS / "archive" / "export_to_neon.py"
    r = _run(
        [sys.executable, str(script), "--dry-run"],
        env={"DATABASE_URL": "sqlite:///:memory:", "PYTHONPATH": str(REPO_ROOT)},
    )
    assert r.returncode == 0, f"dry-run failed:\n{r.stderr}"
    assert "dry-run" in r.stdout.lower()


def test_remove_neon_dualwrite__export_script_missing_url_exits_nonzero():
    # AC (error path): without DATABASE_URL the export refuses cleanly (exit 1),
    # not a traceback.
    script = SCRIPTS / "archive" / "export_to_neon.py"
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    env["PYTHONPATH"] = str(REPO_ROOT)
    r = subprocess.run(
        [sys.executable, str(script)], cwd=str(REPO_ROOT),
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 1, f"expected exit 1, got {r.returncode}:\n{r.stdout}\n{r.stderr}"
    assert "DATABASE_URL" in r.stderr
    assert "Traceback" not in r.stderr


def test_remove_neon_dualwrite__readme_documents_optional_export():
    # AC: README Neon section describes Neon as an optional export target with setup.
    readme = (REPO_ROOT / "README.md").read_text()
    assert "Neon (optional export target)" in readme or "optional export target" in readme, \
        "README missing 'optional export target' Neon section"
    assert "export_to_neon.py" in readme, "README does not reference the export script"


def test_remove_neon_dualwrite__alembic_and_models_intact():
    # AC: Alembic migrations and SQLAlchemy models remain intact.
    versions = REPO_ROOT / "alembic" / "versions"
    assert versions.is_dir(), "alembic/versions missing"
    migrations = list(versions.glob("0*.py"))
    assert len(migrations) >= 8, f"expected >=8 alembic migrations, found {len(migrations)}"
    assert (SM / "models.py").exists(), "SQLAlchemy models.py missing"


def test_remove_neon_dualwrite__alembic_pointed_at_database_url():
    # AC: models/migrations point at the configured DB (DATABASE_URL), not hardcoded.
    env_py = (REPO_ROOT / "alembic" / "env.py").read_text()
    assert "DATABASE_URL" in env_py, "alembic env.py no longer reads DATABASE_URL"
