"""Tests for issue #2342 — Running view is blind to manual dispatch on
managed projects.

Implemented approach: scripts/provision_global_hooks.py provisions
session-wide Claude Code hooks under ~/.claude so PreToolUse/PostToolUse/Stop
hooks fire regardless of which repo's worktree is the session cwd, feeding
the Running view from hand-driven coder/tester sessions on managed projects.

Covers the coder's own "To test" steps from the issue #2342 implementation
comment, which serve as this ticket's acceptance criteria:
  AC1 — --dry-run prints what would change without writing anything
  AC2 — running without --dry-run creates ~/.claude/hooks/*.sh wrapper
        scripts and updates ~/.claude/settings.json with hooks pointing at
        them
  AC3 — idempotency: re-running when nothing changed reports "unchanged" /
        "already up to date" and does not rewrite files
  AC4 — a simulated managed-project session (piping a tool-use JSON payload
        into the generated run_tool_used.sh from an arbitrary cwd) exits 0,
        proving the wrapper actually delegates to commander's hook script
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import scripts.provision_global_hooks as provision_global_hooks  # noqa: E402


def _make_fake_commander_dir(tmp_path: Path) -> Path:
    """Build a minimal fake commander clone: venv/bin/python3 + hooks/*.py."""
    commander_dir = tmp_path / "fake-commander"
    venv_python = commander_dir / "venv" / "bin" / "python3"
    venv_python.parent.mkdir(parents=True)
    # A stub interpreter that just exits 0 — stands in for the real venv
    # python so the generated wrapper script can actually be executed.
    venv_python.write_text(f"#!/usr/bin/env bash\nexec {sys.executable} \"$@\"\n")
    venv_python.chmod(venv_python.stat().st_mode | stat.S_IXUSR)

    hooks_dir = commander_dir / "hooks"
    hooks_dir.mkdir()
    for py_name in provision_global_hooks.HOOK_PY_MAP.values():
        # A minimal stand-in hook: reads stdin (as the real hooks do) and
        # exits 0 without touching any real dashboard.
        (hooks_dir / py_name).write_text(
            "import sys\nsys.stdin.read()\nsys.exit(0)\n"
        )
    return commander_dir


def _run_provision(commander_dir: Path, home_dir: Path, extra_args=None):
    argv = [
        "provision_global_hooks.py",
        "--commander-dir",
        str(commander_dir),
    ] + (extra_args or [])
    env_backup = os.environ.get("HOME")
    home_backup = provision_global_hooks.Path.home
    os.environ["HOME"] = str(home_dir)
    try:
        # Path.home() honours $HOME on POSIX, but pin it defensively too.
        provision_global_hooks.Path.home = staticmethod(lambda: home_dir)
        old_argv = sys.argv
        sys.argv = argv
        try:
            provision_global_hooks.main()
        finally:
            sys.argv = old_argv
    finally:
        provision_global_hooks.Path.home = home_backup
        if env_backup is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = env_backup


class TestAC1DryRunMakesNoChanges:
    def test_dry_run_creates_no_hook_files(self, tmp_path, capsys):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        home_dir.mkdir()

        _run_provision(commander_dir, home_dir, extra_args=["--dry-run"])

        out = capsys.readouterr().out
        assert "[dry-run]" in out
        assert not (home_dir / ".claude" / "hooks").exists()
        assert not (home_dir / ".claude" / "settings.json").exists()

    def test_dry_run_does_not_modify_existing_settings(self, tmp_path, capsys):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        (home_dir / ".claude").mkdir(parents=True)
        settings_path = home_dir / ".claude" / "settings.json"
        original = json.dumps({"hooks": {}, "other": "untouched"})
        settings_path.write_text(original)

        _run_provision(commander_dir, home_dir, extra_args=["--dry-run"])

        assert settings_path.read_text() == original


class TestAC2ProvisioningCreatesHooksAndUpdatesSettings:
    def test_wrapper_scripts_created_and_executable(self, tmp_path):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        home_dir.mkdir()

        _run_provision(commander_dir, home_dir)

        hooks_dir = home_dir / ".claude" / "hooks"
        for sh_name in provision_global_hooks.HOOK_PY_MAP:
            dest = hooks_dir / sh_name
            assert dest.exists(), f"{sh_name} was not created"
            mode = dest.stat().st_mode
            assert mode & stat.S_IXUSR, f"{sh_name} is not executable"
            assert str(commander_dir) in dest.read_text()

    def test_settings_json_updated_with_hooks_pointing_at_wrappers(self, tmp_path):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        home_dir.mkdir()

        _run_provision(commander_dir, home_dir)

        settings_path = home_dir / ".claude" / "settings.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())

        hooks_dir = home_dir / ".claude" / "hooks"
        pre_cmd = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        post_cmd = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
        stop_cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]

        assert str(hooks_dir / "run_tool_used.sh") in pre_cmd
        assert str(hooks_dir / "run_post_tool_used.sh") in post_cmd
        assert str(hooks_dir / "run_agent_finished.sh") in stop_cmd

    def test_preserves_unrelated_existing_settings_keys(self, tmp_path):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        (home_dir / ".claude").mkdir(parents=True)
        settings_path = home_dir / ".claude" / "settings.json"
        settings_path.write_text(json.dumps({"someOtherSetting": "keepme"}))

        _run_provision(commander_dir, home_dir)

        settings = json.loads(settings_path.read_text())
        assert settings["someOtherSetting"] == "keepme"
        assert "hooks" in settings


class TestAC3Idempotency:
    def test_rerun_with_no_changes_reports_unchanged(self, tmp_path, capsys):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        home_dir.mkdir()

        _run_provision(commander_dir, home_dir)
        capsys.readouterr()  # discard first run's output

        _run_provision(commander_dir, home_dir)
        out = capsys.readouterr().out

        assert "unchanged" in out
        assert "already up to date" in out.lower()

    def test_rerun_does_not_rewrite_wrapper_files(self, tmp_path):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        home_dir.mkdir()

        _run_provision(commander_dir, home_dir)
        dest = home_dir / ".claude" / "hooks" / "run_tool_used.sh"
        mtime_before = dest.stat().st_mtime_ns
        content_before = dest.read_text()

        _run_provision(commander_dir, home_dir)

        assert dest.read_text() == content_before
        # Content identical; script explicitly skips writing when unchanged.
        assert dest.stat().st_mtime_ns == mtime_before


class TestAC4SimulatedManagedProjectSessionFiresHook:
    def test_generated_wrapper_executes_from_arbitrary_cwd(self, tmp_path):
        commander_dir = _make_fake_commander_dir(tmp_path)
        home_dir = tmp_path / "home"
        home_dir.mkdir()

        _run_provision(commander_dir, home_dir)

        wrapper = home_dir / ".claude" / "hooks" / "run_tool_used.sh"
        other_repo_cwd = tmp_path / "some-managed-project" / "coder"
        other_repo_cwd.mkdir(parents=True)

        payload = json.dumps(
            {"session_id": "test", "tool_name": "Bash", "cwd": str(other_repo_cwd)}
        )
        result = subprocess.run(
            ["bash", str(wrapper)],
            input=payload,
            cwd=str(other_repo_cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )

        assert result.returncode == 0, (
            f"wrapper failed from a non-commander cwd: {result.stderr}"
        )


class TestAC5ErrorsOnMissingCommanderDir:
    def test_missing_commander_dir_exits_nonzero(self, tmp_path, capsys):
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        missing_dir = tmp_path / "does-not-exist"

        with pytest.raises(SystemExit) as exc_info:
            _run_provision(missing_dir, home_dir)

        assert exc_info.value.code != 0
        err = capsys.readouterr().err
        assert "not found" in err.lower()
        assert not (home_dir / ".claude").exists()
