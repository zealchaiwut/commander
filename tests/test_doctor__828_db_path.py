"""UAT step 3: DB_PATH unset or read-only fails with the exact remediation."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def test_db_path_writable_passes(tmp_path):
    db = tmp_path / "commander.db"
    r = doctor.check_db_path_writable(db_path=str(db))
    assert r.ok, r.fix


def test_db_path_unset_fails_with_fix():
    r = doctor.check_db_path_writable(db_path="")
    assert not r.ok
    assert "DB_PATH" in r.fix


def test_db_path_readonly_dir_fails_with_fix(tmp_path):
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    ro_dir.chmod(0o500)  # read+execute, no write
    target = ro_dir / "commander.db"
    try:
        r = doctor.check_db_path_writable(db_path=str(target))
    finally:
        ro_dir.chmod(0o700)  # restore so tmp cleanup can remove it
    assert not r.ok
    assert "writable" in r.fix.lower()
