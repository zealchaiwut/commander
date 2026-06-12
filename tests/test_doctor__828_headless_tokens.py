"""UAT step 4: when the service is installed, missing headless auth tokens for
claude/gh fail with instructions to add them."""
import plistlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def _write_plist(path: Path, env: dict):
    data = {"Label": "com.commander.dashboard", "EnvironmentVariables": env}
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)


def test_tokens_skipped_when_service_not_installed(tmp_path):
    r = doctor.check_headless_tokens(plist_path=str(tmp_path / "absent.plist"))
    assert r.ok
    assert "skipped" in r.detail.lower()


def test_missing_tokens_fail_with_fix(tmp_path):
    plist = tmp_path / "svc.plist"
    _write_plist(plist, {"PATH": "/usr/bin"})  # no GH_TOKEN, no claude token
    r = doctor.check_headless_tokens(plist_path=str(plist))
    assert not r.ok
    assert "GH_TOKEN" in r.fix
    assert "CLAUDE" in r.fix.upper()


def test_partial_tokens_still_fail(tmp_path):
    plist = tmp_path / "svc.plist"
    _write_plist(plist, {"GH_TOKEN": "ghp_xxx"})  # claude token absent
    r = doctor.check_headless_tokens(plist_path=str(plist))
    assert not r.ok
    assert "claude" in r.fix.lower()


def test_both_tokens_present_passes(tmp_path):
    plist = tmp_path / "svc.plist"
    _write_plist(plist, {"GH_TOKEN": "ghp_xxx", "CLAUDE_CODE_OAUTH_TOKEN": "tok"})
    r = doctor.check_headless_tokens(plist_path=str(plist))
    assert r.ok, r.fix
