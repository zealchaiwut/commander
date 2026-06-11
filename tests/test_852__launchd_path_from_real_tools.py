"""Tests for issue #852 — build launchd plist PATH from real tool locations.

`install_launchd.sh` used to bake a hardcoded `PATH` string into the launchd
plist's `EnvironmentVariables`. On machines where `claude` / `gh` live outside
those fixed dirs (e.g. `~/.local/bin`), every launchd agent dispatch failed with
"claude CLI not found" even though the tool worked in the interactive shell. The
installer now resolves the real on-disk directories of `claude`, `gh`, and the
project venv `bin/` at install time and writes those into the plist PATH.

This builds on the #826 suite. The criteria below that #826 did not directly
exercise are the focus here:

  AC6 — the generated plist PATH contains NO duplicate directory entries
  AC7 — standard system dirs appear as a baseline AFTER the resolved tool dirs
  AC8 — a fresh run regenerates the plist from scratch (overwrites prior state)

The remaining AC are anchored too, so every criterion in the issue maps to a
test here:

  AC1 — `command -v claude` dir lands in PATH
  AC2 — `command -v gh` dir lands in PATH
  AC3 — the project venv bin/ (resolved from the repo root) is appended to PATH
  AC4 — claude missing => human-readable error, non-zero exit, no plist rendered
  AC5 — gh missing => message referencing gh, non-zero exit, no plist rendered
  AC9 — claude in a `~/.local/bin`-style dir => its absolute dir is a PATH segment

The physical Mac launchctl reload and the live agent-dispatch half of AC9 are
covered by the manual UAT steps in the issue (steps 2 and 5), exactly as in
test_724 / test_772 / test_826.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_launchd.sh"

sys.path.insert(0, str(REPO_ROOT))

SYSTEM_DIRS = ["/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_fake_bin(dir_path: Path, name: str) -> None:
    """Create an executable stub named `name` inside `dir_path`."""
    dir_path.mkdir(parents=True, exist_ok=True)
    f = dir_path / name
    f.write_text("#!/usr/bin/env bash\nexit 0\n")
    f.chmod(0o755)


def _run(*args, path_env: str):
    """Run the installer with a controlled PATH so we decide what's resolvable.

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


def test_claude_dir_in_path(tmp_path):
    """AC1: the directory containing `claude` is a PATH segment in the plist."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    assert str(bindir) in _path_segments(proc.stdout)


# ── AC2: gh parent dir resolved via command -v and added to PATH ──────────────


def test_gh_dir_in_path(tmp_path):
    """AC2: the directory containing `gh` is a PATH segment in the plist."""
    claude_dir = tmp_path / "cbin"
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(claude_dir, "claude")
    _make_fake_bin(gh_dir, "gh")
    proc = _render(f"{claude_dir}:{gh_dir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    assert str(gh_dir) in _path_segments(proc.stdout)


# ── AC3: project venv bin/ resolved at install time and appended to PATH ──────


def test_venv_bin_dir_in_path(tmp_path):
    """AC3: the repo's venv bin/ directory is one of the PATH segments."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    assert str(REPO_ROOT / "venv" / "bin") in _path_segments(proc.stdout)


# ── AC4: claude not found => error + non-zero exit + no plist rendered ────────


def test_missing_claude_aborts_without_plist(tmp_path):
    """AC4: with no claude resolvable, the installer errors, exits non-zero, and
    does NOT render the plist."""
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(gh_dir, "gh")  # gh present, claude absent
    proc = _render(f"{gh_dir}:/usr/bin:/bin")
    assert proc.returncode != 0
    assert "claude" in (proc.stdout + proc.stderr).lower()
    assert "<plist" not in proc.stdout  # nothing rendered


# ── AC5: gh not found => message referencing gh + non-zero exit + no plist ────


def test_missing_gh_aborts_without_plist(tmp_path):
    """AC5: with no gh resolvable, the installer prints a human-readable message
    referencing gh, exits non-zero, and does NOT render the plist."""
    claude_dir = tmp_path / "cbin"
    _make_fake_bin(claude_dir, "claude")  # claude present, gh absent
    proc = _render(f"{claude_dir}:/usr/bin:/bin")
    assert proc.returncode != 0
    assert "gh" in (proc.stdout + proc.stderr).lower()
    assert "<plist" not in proc.stdout


# ── AC6: the generated plist PATH contains no duplicate directory entries ─────


def test_no_duplicate_path_entries(tmp_path):
    """AC6: every directory appears at most once in the plist PATH, even when
    claude and gh resolve to the SAME directory."""
    bindir = tmp_path / "shared"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    assert len(segs) == len(set(segs)), f"duplicate PATH entries: {segs}"
    # The shared tool dir collapses to exactly one segment.
    assert segs.count(str(bindir)) == 1


# ── AC7: system dirs are a baseline AFTER the resolved tool directories ───────


def test_system_dirs_come_after_resolved_tool_dirs(tmp_path):
    """AC7: the resolved tool dirs (venv, claude, gh) precede the standard
    system dirs in the plist PATH — system dirs are the trailing baseline."""
    claude_dir = tmp_path / "cbin"
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(claude_dir, "claude")
    _make_fake_bin(gh_dir, "gh")
    proc = _render(f"{claude_dir}:{gh_dir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)

    venv_bin = str(REPO_ROOT / "venv" / "bin")
    resolved = [venv_bin, str(claude_dir), str(gh_dir)]
    present_system = [d for d in SYSTEM_DIRS if d in segs]
    assert present_system, "no baseline system dirs present"

    last_resolved = max(segs.index(d) for d in resolved if d in segs)
    first_system = min(segs.index(d) for d in present_system)
    assert last_resolved < first_system, (
        f"resolved tool dirs must precede system dirs; got {segs}"
    )


def test_baseline_system_dirs_present(tmp_path):
    """AC7: the standard system dirs are included as a baseline."""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    proc = _render(f"{bindir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    for d in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        assert d in segs, f"missing baseline system dir {d}"


# ── AC8: a fresh run regenerates the plist from scratch (overwrites prior) ────


def test_fresh_run_regenerates_plist_deterministically(tmp_path):
    """AC8 (code-testable half): each run rebuilds the plist purely from the
    resolved tool dirs, so a fresh run fully replaces any prior plist rather
    than merging with it. Two runs over the same environment yield byte-identical
    output, and that output carries only freshly resolved + baseline dirs — no
    stale hardcoded directory leaks through. (The on-disk file overwrite via `>`
    plus the launchctl reload are exercised by manual UAT step 5.)"""
    bindir = tmp_path / "toolbin"
    _make_fake_bin(bindir, "claude")
    _make_fake_bin(bindir, "gh")
    first = _render(f"{bindir}:/usr/bin:/bin")
    second = _render(f"{bindir}:/usr/bin:/bin")
    assert first.returncode == 0 and second.returncode == 0
    assert first.stdout == second.stdout, "render is not deterministic across runs"

    segs = _path_segments(first.stdout)
    allowed = {str(bindir), str(REPO_ROOT / "venv" / "bin"), *SYSTEM_DIRS}
    assert set(segs) <= allowed, f"unexpected/stale PATH segment: {set(segs) - allowed}"


# ── AC9: claude in ~/.local/bin-style dir => its absolute dir is a PATH segment ─


def test_local_bin_claude_absolute_dir_in_path(tmp_path):
    """AC9 (install half): claude installed under a `.local/bin` directory has
    that directory's absolute path show up as a colon-separated PATH segment, so
    the launchd dispatch can locate it. (The live-dispatch half is manual UAT.)"""
    local_bin = tmp_path / ".local" / "bin"
    _make_fake_bin(local_bin, "claude")
    gh_dir = tmp_path / "gbin"
    _make_fake_bin(gh_dir, "gh")
    proc = _render(f"{local_bin}:{gh_dir}:/usr/bin:/bin")
    assert proc.returncode == 0, proc.stderr
    segs = _path_segments(proc.stdout)
    assert str(local_bin) in segs
    assert os.path.isabs(str(local_bin))
