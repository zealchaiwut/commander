"""Tests for issue #762 — log rotation + gitignore secrets audit.

Each test maps to one acceptance criterion:

- AC1: prd.log uses RotatingFileHandler(maxBytes=10_000_000, backupCount=5) and
       is wired at every logger setup entry point (run_server, structured logger).
- AC2: .commander/logs agent logs have equivalent rotation / verified upper bound.
- AC3: git ls-files returns no .env / *.db / log files / runtime artifacts.
- AC4: .gitignore contains explicit patterns for .env, *.db, *.log, runtime artifacts.
- AC5: stray files outside the repo (tmp*.md at meta root) are documented.
- AC6: a "secrets hygiene" runbook note lists revoking the old ANTHROPIC_API_KEY
       at console.anthropic.com with a manual verification checklist.
"""
import logging
import logging.handlers
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# AC1 — prd.log RotatingFileHandler
# ---------------------------------------------------------------------------

def _detach(handler):
    logging.getLogger().removeHandler(handler)
    try:
        handler.close()
    except Exception:
        pass


def test_setup_logging_installs_rotating_handler_with_defaults(tmp_path):
    import services.logging as logging_mod

    log_path = tmp_path / "prd.log"
    handler = logging_mod.setup_logging(log_path=log_path)
    try:
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 10_000_000
        assert handler.backupCount == 5
        assert handler in logging.getLogger().handlers
    finally:
        _detach(handler)


def test_setup_logging_env_overrides_max_bytes_and_backup_count(tmp_path, monkeypatch):
    import services.logging as logging_mod

    monkeypatch.setenv("COMMANDER_LOG_MAX_BYTES", "1000")
    monkeypatch.setenv("COMMANDER_LOG_BACKUP_COUNT", "3")
    log_path = tmp_path / "prd.log"
    handler = logging_mod.setup_logging(log_path=log_path)
    try:
        assert handler.maxBytes == 1000
        assert handler.backupCount == 3
    finally:
        _detach(handler)


def test_setup_logging_is_idempotent(tmp_path):
    import services.logging as logging_mod

    log_path = tmp_path / "prd.log"
    h1 = logging_mod.setup_logging(log_path=log_path)
    h2 = logging_mod.setup_logging(log_path=log_path)
    try:
        assert h1 is h2
        rotating = [
            h for h in logging.getLogger().handlers
            if getattr(h, "_commander_prd", False)
        ]
        assert len(rotating) == 1
    finally:
        _detach(h1)


def test_prd_log_rotation_creates_backups_and_prunes(tmp_path):
    """UAT step 1: small maxBytes → prd.log.1 created, backups bounded, oldest pruned."""
    import services.logging as logging_mod

    log_path = tmp_path / "prd.log"
    handler = logging_mod.setup_logging(log_path=log_path, max_bytes=1000, backup_count=2)
    logger = logging.getLogger()
    prev_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        for i in range(500):
            logger.info("rotation probe line %05d padding-padding-padding", i)
        handler.flush()
        assert (tmp_path / "prd.log.1").exists()
        # backupCount=2 → at most prd.log.1 and prd.log.2, never prd.log.3
        assert not (tmp_path / "prd.log.3").exists()
        backups = list(tmp_path.glob("prd.log.*"))
        assert len(backups) <= 2
    finally:
        logger.setLevel(prev_level)
        _detach(handler)


def test_prd_log_default_path_points_at_dashboard(tmp_path, monkeypatch):
    import services.logging as logging_mod

    monkeypatch.delenv("COMMANDER_PRD_LOG", raising=False)
    path = logging_mod._prd_log_path()
    assert path.name == "prd.log"
    assert path.parent.name == "dashboard"


def test_entry_points_call_setup_logging():
    """AC1: rotation applies at every logger setup entry point."""
    run_server = (REPO_ROOT / "apps" / "dashboard" / "run_server.py").read_text()
    server = (REPO_ROOT / "apps" / "dashboard" / "server.py").read_text()
    assert "setup_logging" in run_server, "run_server.py must wire setup_logging"
    assert "setup_logging" in server, "server.py must wire setup_logging"


# ---------------------------------------------------------------------------
# AC2 — .commander/logs rotation / verified upper bound
# ---------------------------------------------------------------------------

def test_commander_file_handler_rotates_on_size(tmp_path, monkeypatch):
    import services.logging as logging_mod

    log_dir = tmp_path / ".commander" / "logs"
    log_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COMMANDER_LOG_MAX_BYTES", "1000")
    monkeypatch.setenv("COMMANDER_LOG_BACKUP_COUNT", "2")

    handler = logging_mod._CommanderFileHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    for i in range(500):
        rec = logging.LogRecord(
            "commander", logging.INFO, __file__, i,
            "agent log padding padding padding %05d" % i, None, None,
        )
        handler.emit(rec)

    rotated = list(log_dir.glob("commander-*.log.*"))
    assert rotated, "expected at least one rotated .commander/logs backup"
    # bounded by backupCount
    assert len(rotated) <= 2


# ---------------------------------------------------------------------------
# AC3 — git ls-files clean
# ---------------------------------------------------------------------------

def _git_ls_files():
    out = subprocess.check_output(
        ["git", "ls-files"], cwd=REPO_ROOT, text=True
    )
    return [line for line in out.splitlines() if line.strip()]


def test_no_secret_or_runtime_artifacts_tracked():
    tracked = _git_ls_files()
    offenders = []
    for path in tracked:
        name = Path(path).name
        # .env.example is the intentional template; everything else is an offender
        if name == ".env" or (name.startswith(".env") and not name.endswith(".example")):
            offenders.append(path)
        if name.endswith((".db", ".log", ".pid")):
            offenders.append(path)
        if name in ("commander.db", "dashboard.db", "STATUS.md", "requirements.txt.bak", ":memory:"):
            offenders.append(path)
        if name.startswith("tmp") and name.endswith(".md"):
            offenders.append(path)
    assert not offenders, f"secret/runtime artifacts are tracked: {offenders}"


# ---------------------------------------------------------------------------
# AC4 — .gitignore patterns
# ---------------------------------------------------------------------------

def test_gitignore_covers_secrets_and_runtime_artifacts():
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    lines = {line.strip() for line in gitignore.splitlines()}
    assert ".env" in lines
    assert "*.db" in lines
    assert "*.log" in lines
    # runtime artifacts identified during the audit
    assert "STATUS.md" in lines
    assert any(p in lines for p in ("requirements.txt.bak", "*.bak"))
    assert ".commander/estimates/" in lines
    assert ".commander/reports/" in lines


# ---------------------------------------------------------------------------
# AC5 + AC6 — runbook secrets-hygiene note
# ---------------------------------------------------------------------------

def _runbook_text():
    return (REPO_ROOT / "docs" / "runbook.md").read_text()


def test_runbook_has_secrets_hygiene_section():
    text = _runbook_text().lower()
    assert "secrets hygiene" in text


def test_runbook_lists_key_revocation_with_checklist():
    text = _runbook_text()
    assert "console.anthropic.com" in text
    assert "ANTHROPIC_API_KEY" in text
    # a manual confirmation checklist (markdown checkbox)
    assert "- [ ]" in text


def test_runbook_documents_stray_meta_root_files():
    text = _runbook_text().lower()
    assert "tmp" in text and ".md" in text
    assert "stray" in text or "meta root" in text
