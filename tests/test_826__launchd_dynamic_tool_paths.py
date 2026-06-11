"""Tests for issue #826 — resolve tool paths dynamically in install_launchd.sh.

`install_launchd.sh` used to bake a hardcoded `PATH` string into the launchd
plist's `EnvironmentVariables`. On machines where the `claude` CLI lives outside
those hardcoded directories (e.g. `~/.local/bin`) every launchd agent dispatch
failed with "claude CLI not found". The installer now resolves the real on-disk
directories of `claude`, `gh`, and the project venv `bin/` at install time and
writes those into the plist PATH.

AC coverage (code-testable subset). The physical Mac launchctl / dispatch steps
(UAT steps 4 and the live-dispatch half of AC8) are covered by the manual UAT
steps in the issue, exactly as in test_724 / test_772:

  AC1 — `command -v claude` locates the binary; its parent dir lands in PATH
  AC2 — `command -v gh` locates the binary; its parent dir lands in PATH
  AC3 — the project venv bin/ (resolved from the repo root) is in PATH
  AC4 — the plist EnvironmentVariables > PATH contains the dynamically resolved
        dirs (not a hardcoded assumption about where claude lives)
  AC5 — claude missing => human-readable error, non-zero exit, no plist rendered
  AC6 — gh missing => human-readable error, non-zero exit, no plist rendered
  AC7 — claude in a non-standard dir (~/.local/bin style) => its absolute path is
        one of the colon-separated PATH segments
  AC8 — (dispatch success: manual UAT) the resolved-PATH plist is well formed
  AC9 — claude in a standard location is unaffected: the standard system dirs
        remain present and the install still renders a valid plist
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_launchd.sh"

sys.path.insert(0, str(REPO_ROOT))


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_fake_bin(dir_path: Path, name: str) -> None:
    """Create an executable stub named `name` inside `dir_path`."""
    dir_path.mkdir(parents=True, exist_ok=True)
    f = dir_path / name
    f.write_text("#!/usr/bin/env bash\nexit 0\n")
    f.chmod(0o755)


def _run(*args, path_env: str):
    """Run the installer with a controlled PATH (so we decide what's resolvable).

    PATH must still include the system dirs the script itself relies on
    (cat, dirname, grep, sed, ...), so callers prepend their fake bin dir to
    `/usr/bin:/bin`.
    """
    env = dict(os.environ)
    env["PATH"] = path_env
    # Strip any ambient token so it never interferes with PATH assertions.
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return subprocess.run(
        ["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env
    )


def _render(path_env: str, *extra_args) -> subprocess.CompletedProcess:
    return _run("--print-plist", *extra_args, path_env=path_env)


def _path_segments(plist_stdout: str) -> list[str]:
    data = plistlib.loads(plist_stdout.encode("utf-8"))
    return data["EnvironmentVariables"]["PATH"].split(":")


# ── AC1: claude parent dir resolved via command -v and added to PATH ──────────


def test_claude_parent_dir_in_path(tmp_path):
    """AC1: the directory containing `claude` is a PATH segment in the plist."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    assert str(bindir) in _path_segments(proc.stdout)


# ── AC2: gh parent dir resolved via command -v and added to PATH ──────────────


def test_gh_parent_dir_in_path(tmp_path):
    """AC2: the directory containing `gh` is a PATH segment in the plist."""
    claude_dir = tmp_path / "cbin"
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(claude_dir, "claude")
    _make_fake_bin(gh_dir, "gh")
    proc = _render(f"{claude_dir}:{gh_dir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    assert str(gh_dir) in _path_segments(proc.stdout)


# ── AC3: project venv bin/ resolved from the repo root and added to PATH ──────


def test_venv_bin_dir_in_path(tmp_path):
    """AC3: the repo's venv bin/ directory is one of the PATH segments."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    venv_bin = str(REPO_ROOT / "venv" / "bin")
    assert venv_bin in _path_segments(proc.stdout)


# ── AC4: PATH carries the dynamically resolved dirs, not a hardcoded assumption ─


def test_path_uses_resolved_dirs_not_hardcoded(tmp_path):
    """AC4: when claude/gh live in non-standard dirs, those exact dirs appear in
    PATH — the value is built from resolution, not a fixed string."""
    claude_dir = tmp_path / "weird" / "claude-home" / "bin"
    gh_dir = tmp_path / "elsewhere" / "gh-home" / "bin"
    _make_fake_bin(claude_dir, "claude")
    _make_fake_bin(gh_dir, "gh")
    proc = _render(f"{claude_dir}:{gh_dir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    assert str(claude_dir) in segs
    assert str(gh_dir) in segs


# ── AC5: claude not found => error + non-zero exit + no plist rendered ────────


def test_missing_claude_aborts(tmp_path):
    """AC5: with no claude resolvable, the installer errors, exits non-zero, and
    does NOT render the plist."""
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(gh_dir, "gh")  # gh present, claude absent
    proc = _render(f"{gh_dir}:/usr/bin:/bin")
    assert proc.returncode != 0
    combined = (proc.stdout + proc.stderr).lower()
    assert "claude" in combined
    assert "<plist" not in proc.stdout  # nothing rendered


# ── AC6: gh not found => error (referencing gh) + non-zero exit + no plist ────


def test_missing_gh_aborts(tmp_path):
    """AC6: with no gh resolvable, the installer errors referencing gh, exits
    non-zero, and does NOT render the plist."""
    claude_dir = tmp_path / "cbin"
    _make_fake_bin(claude_dir, "claude")  # claude present, gh absent
    proc = _render(f"{claude_dir}:/usr/bin:/bin")
    assert proc.returncode != 0
    combined = (proc.stdout + proc.stderr).lower()
    assert "gh" in combined
    assert "<plist" not in proc.stdout


# ── AC7: claude in a non-standard ~/.local/bin-style dir => absolute path in PATH ─


def test_local_bin_claude_absolute_path_in_path(tmp_path):
    """AC7: claude installed under a `.local/bin` directory has that directory's
    absolute path show up as a colon-separated PATH segment."""
    local_bin = tmp_path / ".local" / "bin"
    _make_fake_bin(local_bin, "claude")
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(gh_dir, "gh")
    proc = _render(f"{local_bin}:{gh_dir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    assert str(local_bin) in segs
    assert os.path.isabs(str(local_bin))


# ── AC8: the resolved-PATH plist is a well-formed, loadable plist ─────────────


def test_resolved_plist_is_valid(tmp_path):
    """AC8: the plist produced with dynamically resolved paths still parses and
    keeps its core identity (backward compatible with #724 / #772)."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    data = plistlib.loads(proc.stdout.encode("utf-8"))
    assert data["Label"] == "com.commander.dashboard"
    assert "PATH" in data["EnvironmentVariables"]


# ── AC9: standard locations unaffected — system dirs remain present ───────────


def test_standard_system_dirs_remain_in_path(tmp_path):
    """AC9: a machine with claude in a standard dir is unaffected — the usual
    system directories are still present so existing installs keep working."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    assert "/usr/bin" in segs
    assert "/bin" in segs
    assert "/usr/local/bin" in segs


def test_resolved_paths_printed_on_real_install_dry_run(tmp_path):
    """UAT step 2: the installer reports the resolved claude / gh / venv bin
    directories. Asserted via --print-plist not touching launchctl: the resolved
    dirs are visible in the plist PATH (the stdout-report half is exercised on a
    real install)."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    assert str(bindir) in segs
    assert str(REPO_ROOT / "venv" / "bin") in segs
