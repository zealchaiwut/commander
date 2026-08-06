"""Tests for issue #2014: Remove duplicate local-backup code from
services/sprint_manager/backup.py.

AC: The local-backup block (backup_db_local, list_local_backups,
start_local_backup_scheduler, and related private helpers) is removed from
services/sprint_manager/backup.py.  The live implementation in
apps/dashboard/backup.py is the only one; startup.py wires that module in.

These are behavioral runtime checks — they import the actual module and assert
its namespace, which is different from source-text regex checks.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _import_sm_backup():
    """Import services.sprint_manager.backup with repo root on sys.path."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    # Force a fresh import so cached stale modules don't mask a test failure.
    mod_name = "services.sprint_manager.backup"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    return importlib.import_module(mod_name)


class TestDuplicateLocalBackupRemoved:
    """AC: Local-backup symbols absent from services.sprint_manager.backup."""

    def test_module_imports_cleanly(self):
        """services.sprint_manager.backup imports without error after removal."""
        mod = _import_sm_backup()
        assert mod is not None

    def test_backup_db_local_removed(self):
        """backup_db_local must not exist in services.sprint_manager.backup."""
        mod = _import_sm_backup()
        assert not hasattr(mod, "backup_db_local"), (
            "backup_db_local is dead code duplicated from apps/dashboard/backup.py "
            "and must be removed from services.sprint_manager.backup"
        )

    def test_list_local_backups_removed(self):
        """list_local_backups must not exist in services.sprint_manager.backup."""
        mod = _import_sm_backup()
        assert not hasattr(mod, "list_local_backups"), (
            "list_local_backups is dead code duplicated from apps/dashboard/backup.py "
            "and must be removed from services.sprint_manager.backup"
        )

    def test_start_local_backup_scheduler_removed(self):
        """start_local_backup_scheduler must not exist in services.sprint_manager.backup."""
        mod = _import_sm_backup()
        assert not hasattr(mod, "start_local_backup_scheduler"), (
            "start_local_backup_scheduler is dead code duplicated from "
            "apps/dashboard/backup.py and must be removed from "
            "services.sprint_manager.backup"
        )

    def test_live_backup_module_still_has_scheduler(self):
        """apps/dashboard/backup.py still exposes start_local_backup_scheduler."""
        import importlib.util as ilu
        spec = ilu.spec_from_file_location(
            "_dash_backup_2014",
            str(REPO_ROOT / "apps" / "dashboard" / "backup.py"),
        )
        dash_backup = ilu.module_from_spec(spec)
        spec.loader.exec_module(dash_backup)  # type: ignore[union-attr]
        assert hasattr(dash_backup, "start_local_backup_scheduler"), (
            "apps/dashboard/backup.py must still have start_local_backup_scheduler"
        )
        assert hasattr(dash_backup, "backup_db_local"), (
            "apps/dashboard/backup.py must still have backup_db_local"
        )
        assert hasattr(dash_backup, "list_local_backups"), (
            "apps/dashboard/backup.py must still have list_local_backups"
        )

    def test_gist_backup_functions_still_present(self):
        """Removing the local-backup block must not disturb the gist/repo
        backup functions that are the primary purpose of this module."""
        mod = _import_sm_backup()
        assert hasattr(mod, "backup_config_to_gist"), (
            "backup_config_to_gist must remain — it is the module's primary function"
        )
        assert hasattr(mod, "backup_db_to_repo"), (
            "backup_db_to_repo must remain — it is wired into the live scheduler"
        )
        assert hasattr(mod, "get_backup_status"), (
            "get_backup_status must remain — consumed by the API"
        )
