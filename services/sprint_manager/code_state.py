"""Code-state snapshot integration (issue #1862).

generate_code_state_snapshot() is called after the documenter step at sprint
finish.  It runs scripts/generate_code_state.py deterministically, commits the
resulting docs/architecture/code-state.md to the sprint branch, and pushes.

All errors are caught and logged — the function never raises so a snapshot
failure cannot block the sprint pipeline (AC2).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Absolute path to the generator script
_GENERATOR_SCRIPT: Path = (
    Path(__file__).parent.parent.parent / "scripts" / "generate_code_state.py"
)

_OUTPUT_REL = Path("docs") / "architecture" / "code-state.md"


def _run(*cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, cwd=str(cwd))


def _get_sha(ref: str, cwd: Path) -> str:
    r = _run("git", "rev-parse", ref, cwd=cwd)
    return r.stdout.strip() if r.returncode == 0 else "HEAD"


def _commit_code_state(repo_root: Path, output_path: Path, sprint_label: str) -> bool:
    """Stage and commit output_path if it has uncommitted changes.

    Returns True when a commit was made, False when the file was already
    up to date (no-op).
    """
    rel = str(output_path.relative_to(repo_root))

    # Check for unstaged or untracked changes
    status_r = _run("git", "status", "--porcelain", rel, cwd=repo_root)
    if status_r.returncode != 0 or not status_r.stdout.strip():
        return False

    _run("git", "add", rel, cwd=repo_root)
    commit_r = _run(
        "git", "commit",
        "-m", f"docs: generate code-state snapshot for {sprint_label}",
        cwd=repo_root,
    )
    return commit_r.returncode == 0


def generate_code_state_snapshot(
    sprint_label: str,
    sprint_branch: str,
    cwd: Path,
    base_sha: str | None = None,
    head_sha: str | None = None,
) -> None:
    """Run the code-state generator, commit, and push.

    Never raises — all errors are printed as WARNING lines so the calling
    sprint pipeline can continue (AC2).

    Args:
        sprint_label:  e.g. "sprint-116"
        sprint_branch: e.g. "sprint/sprint-116" (used for push)
        cwd:           working-tree path (tester clone or repo root)
        base_sha:      merge-base SHA for recent deltas (auto-derived if None)
        head_sha:      HEAD SHA (auto-derived if None)
    """
    try:
        _generate_code_state_snapshot_inner(
            sprint_label=sprint_label,
            sprint_branch=sprint_branch,
            cwd=cwd,
            base_sha=base_sha,
            head_sha=head_sha,
        )
    except Exception as exc:
        sys.stdout.write(
            f"  [code_state] WARNING: snapshot generation failed (non-fatal): {exc}\n"
        )
        sys.stdout.flush()


def _generate_code_state_snapshot_inner(
    sprint_label: str,
    sprint_branch: str,
    cwd: Path,
    base_sha: str | None,
    head_sha: str | None,
) -> None:
    if not _GENERATOR_SCRIPT.exists():
        raise FileNotFoundError(f"Generator script not found: {_GENERATOR_SCRIPT}")

    # Resolve SHAs from the working tree
    eff_head = head_sha or _get_sha(f"origin/{sprint_branch}", cwd) or _get_sha("HEAD", cwd)
    eff_base = base_sha or _get_sha("origin/develop", cwd) or eff_head

    output_path = cwd / _OUTPUT_REL

    cmd = [
        sys.executable,
        str(_GENERATOR_SCRIPT),
        "--repo-root", str(cwd),
        "--sprint-label", sprint_label,
        "--base-sha", eff_base,
        "--head-sha", eff_head,
        "--output", str(output_path),
    ]

    sys.stdout.write(f"  [code_state] Generating {output_path} ...\n")
    sys.stdout.flush()

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"generate_code_state.py exited {result.returncode}: "
            f"{result.stderr.strip()[:300]}"
        )

    committed = _commit_code_state(cwd, output_path, sprint_label)
    if committed:
        push_r = _run("git", "push", "origin", sprint_branch, cwd=cwd)
        if push_r.returncode != 0:
            sys.stdout.write(
                f"  [code_state] WARNING: push failed (non-fatal): "
                f"{push_r.stderr.strip()[:200]}\n"
            )
            sys.stdout.flush()
        else:
            sys.stdout.write(f"  [code_state] Pushed code-state.md to {sprint_branch}\n")
            sys.stdout.flush()
    else:
        sys.stdout.write("  [code_state] code-state.md unchanged — no commit needed\n")
        sys.stdout.flush()

    sys.stdout.write(f"  [code_state] Done\n")
    sys.stdout.flush()
