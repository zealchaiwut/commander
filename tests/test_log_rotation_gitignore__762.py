"""Tester AC verification for issue #762: Add log rotation and audit gitignore
for secrets.

These acceptance criteria are filesystem / config / docs facts about the repo
tree and the logging module — none are reachable over HTTP — so the tests
inspect the feature-branch source directly (import the logging module, read
.gitignore / docs/runbook.md, shell out to `git ls-files`) rather than hitting a
running server.
"""
import importlib
import logging
import logging.handlers
import re
import subprocess
import sys
from pathlib import Path

import pytest

# tests/ lives at the repo root; the dashboard code is under apps/dashboard and
# the logging module under services/.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def logging_mod():
    mod = importlib.import_module("services.logging")
    return importlib.reload(mod)


# --- AC1: prd.log uses RotatingFileHandler(maxBytes=10_000_000, backupCount=5)
#         at every logger setup entry point (run_server, structured logger) ---

def test_762__prd_log_rotating_handler_bounds(logging_mod, tmp_path):
    # AC: setup_logging installs a RotatingFileHandler with the required bounds.
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        # Strip any handler a prior import already installed so we test fresh.
        root.handlers = [h for h in root.handlers if not getattr(h, "_commander_prd", False)]
        handler = logging_mod.setup_logging(tmp_path / "prd.log")
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 10_000_000
        assert handler.backupCount == 5
        # Idempotent: a second call must not stack a duplicate handler.
        again = logging_mod.setup_logging(tmp_path / "prd.log")
        assert again is handler
        installed = [h for h in root.handlers if getattr(h, "_commander_prd", False)]
        assert len(installed) == 1
    finally:
        root.handlers = saved


def test_762__setup_logging_wired_at_entry_points():
    # AC: applies at every logger setup entry point — run_server + server import.
    run_server = (REPO_ROOT / "apps" / "dashboard" / "run_server.py").read_text()
    server = (REPO_ROOT / "apps" / "dashboard" / "server.py").read_text()
    assert "setup_logging" in run_server
    assert "setup_logging" in server


# --- AC2: .commander/logs agent logs have equivalent rotation / upper bound ---

def test_762__commander_logs_size_rotation(logging_mod, tmp_path):
    # AC: the .commander/logs daily file rotates on size like prd.log. Exercise
    # the rotation helper directly with a tiny threshold.
    target = tmp_path / "commander-2026-06-10.log"
    target.write_text("X" * 90)
    # Incoming write would push past max_bytes=100 -> rollover to .1
    logging_mod._rotate_if_needed(target, incoming_len=50, max_bytes=100, backup_count=5)
    assert (tmp_path / "commander-2026-06-10.log.1").exists()
    assert not target.exists()  # renamed to .1, fresh file recreated by caller


def test_762__commander_logs_prunes_beyond_backup_count(logging_mod, tmp_path):
    # AC: equivalent verified upper bound — backups never exceed backup_count.
    name = "commander.log"
    base = tmp_path / name
    base.write_text("X" * 90)
    for i in range(1, 3):
        (tmp_path / f"{name}.{i}").write_text("old")
    logging_mod._rotate_if_needed(base, incoming_len=50, max_bytes=100, backup_count=2)
    # oldest (.2) pruned, .1 shifted to .2, base -> .1
    backups = sorted(p.name for p in tmp_path.glob(f"{name}.*"))
    assert backups == [f"{name}.1", f"{name}.2"]


# --- AC3: git ls-files returns no .env, *.db, log files, or runtime artifacts ---

def test_762__no_secrets_or_artifacts_tracked():
    # AC: nothing sensitive leaked into the index.
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    bad = re.compile(r"(\.env$|\.(db|log|pid)$|tmp.*\.md)")
    matches = [line for line in out.splitlines() if bad.search(line)]
    # .env.example is the only intentional .env* file and must NOT match \.env$
    assert matches == [], f"unexpected tracked artifacts: {matches}"


# --- AC4: .gitignore covers .env, *.db, *.log, runtime artifacts ---

def test_762__gitignore_covers_required_patterns():
    # AC: explicit ignore patterns present.
    text = (REPO_ROOT / ".gitignore").read_text()
    for pat in [".env", "*.db", "*.log"]:
        assert re.search(rf"^{re.escape(pat)}\s*$", text, re.MULTILINE), f"missing {pat}"
    # at least one runtime-artifact dir pattern
    assert ".commander/" in text


# --- AC5: stray files outside the repo are noted / documented ---

def test_762__stray_files_documented_in_runbook():
    # AC: meta-root stray files (e.g. tmp*.md) noted in the runbook.
    runbook = (REPO_ROOT / "docs" / "runbook.md").read_text()
    assert re.search(r"[Ss]tray files", runbook)
    assert "tmp" in runbook and ".md" in runbook


# --- AC6: secrets hygiene note with ANTHROPIC_API_KEY revocation checklist ---

def test_762__secrets_hygiene_revocation_checklist():
    # AC: runbook has a secrets-hygiene section with a manual revoke checkbox
    # pointing at console.anthropic.com.
    runbook = (REPO_ROOT / "docs" / "runbook.md").read_text()
    assert re.search(r"[Ss]ecrets hygiene", runbook)
    assert "console.anthropic.com" in runbook
    assert "ANTHROPIC_API_KEY" in runbook
    # at least one unchecked manual checkbox in the revocation steps
    assert "- [ ]" in runbook
    assert re.search(r"revoke", runbook, re.IGNORECASE)
