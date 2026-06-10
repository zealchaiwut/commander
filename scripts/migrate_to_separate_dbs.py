#!/usr/bin/env python3
"""migrate_to_separate_dbs.py — Copy dashboard.db into commander.db before the
PRD/UAT DB-split goes live.

Run from the repo root or from within dashboard/:
    python3 dashboard/scripts/migrate_to_separate_dbs.py

Actions:
  a. Creates  dashboard/.backups/  if absent.
  b. Copies   dashboard/dashboard.db → dashboard/.backups/commander-pre-split.db
              (skips if the backup already exists — idempotent).
  c. Copies   dashboard/dashboard.db → dashboard/commander.db
              (only if commander.db is absent or 0 bytes).
  d. Prints a one-line summary per action.
  e. Exits non-zero if dashboard/dashboard.db does not exist.
"""

import shutil
import sys
from pathlib import Path

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent          # dashboard/scripts/
DASHBOARD_DIR = SCRIPT_DIR.parent                     # dashboard/
SOURCE_DB = DASHBOARD_DIR / "dashboard.db"
BACKUP_DIR = DASHBOARD_DIR / ".backups"
BACKUP_DB = BACKUP_DIR / "commander-pre-split.db"
TARGET_DB = DASHBOARD_DIR / "commander.db"


def main() -> int:
    # ── Guard: source must exist ───────────────────────────────────────────────
    if not SOURCE_DB.exists():
        sys.stderr.write(str(f"ERROR: source database not found: {SOURCE_DB}") + "\n")
        return 1

    # ── Step a: ensure .backups/ exists ───────────────────────────────────────
    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True)
        sys.stdout.write(str(f"Created directory: {BACKUP_DIR}") + "\n")
    else:
        sys.stdout.write(str(f"Directory already exists: {BACKUP_DIR}") + "\n")

    # ── Step b: backup (idempotent) ────────────────────────────────────────────
    if BACKUP_DB.exists():
        sys.stdout.write(str(f"Skipped backup (already exists): {BACKUP_DB}") + "\n")
    else:
        shutil.copy2(SOURCE_DB, BACKUP_DB)
        sys.stdout.write(str(f"Copied {SOURCE_DB} → {BACKUP_DB}") + "\n")

    # ── Step c: copy to commander.db if absent or empty ───────────────────────
    if TARGET_DB.exists() and TARGET_DB.stat().st_size > 0:
        sys.stdout.write(str(f"Skipped target copy (already populated): {TARGET_DB}") + "\n")
    else:
        shutil.copy2(SOURCE_DB, TARGET_DB)
        sys.stdout.write(str(f"Copied {SOURCE_DB} → {TARGET_DB}") + "\n")

    sys.stdout.write(str("Migration complete.") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
