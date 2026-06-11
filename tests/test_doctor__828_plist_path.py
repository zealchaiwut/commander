"""AC: the launchd plist PATH entry includes the dirs containing claude and gh."""
import os
import plistlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402


def _write_plist(path: Path, path_value: str, extra_env=None):
    env = {"PATH": path_value}
    if extra_env:
        env.update(extra_env)
    data = {"Label": "com.commander.dashboard", "EnvironmentVariables": env}
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)


def test_plist_not_installed_is_skipped(tmp_path):
    missing = tmp_path / "not-installed.plist"
    r = doctor.check_plist_path(plist_path=str(missing))
    assert r.ok
    assert "skipped" in r.detail.lower()


def test_plist_path_missing_claude_dir_fails_with_fix(tmp_path):
    claude_dir = tmp_path / "claude-bin"
    gh_dir = tmp_path / "gh-bin"
    claude_dir.mkdir()
    gh_dir.mkdir()
    (claude_dir / "claude").write_text("#!/bin/sh\n")
    (gh_dir / "gh").write_text("#!/bin/sh\n")

    plist = tmp_path / "svc.plist"
    # PATH includes gh dir but NOT the claude dir.
    _write_plist(plist, str(gh_dir))

    def which(name):
        return str(claude_dir / "claude") if name == "claude" else str(gh_dir / "gh")

    r = doctor.check_plist_path(plist_path=str(plist), which=which)
    assert not r.ok
    assert str(claude_dir) in r.fix
    assert "install_launchd" in r.fix.lower()


def test_plist_path_with_both_dirs_passes(tmp_path):
    claude_dir = tmp_path / "claude-bin"
    gh_dir = tmp_path / "gh-bin"
    claude_dir.mkdir()
    gh_dir.mkdir()
    plist = tmp_path / "svc.plist"
    _write_plist(plist, os.pathsep.join([str(claude_dir), str(gh_dir)]))

    def which(name):
        return str(claude_dir / "claude") if name == "claude" else str(gh_dir / "gh")

    r = doctor.check_plist_path(plist_path=str(plist), which=which)
    assert r.ok, r.fix
