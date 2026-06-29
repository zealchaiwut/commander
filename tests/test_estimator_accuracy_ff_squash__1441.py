"""Tests for issue #1441 — Estimator accuracy miscounts files on ff/squash merges.

`scripts/finish_feature.py` records estimator file-prediction accuracy by diffing
the merge commit. For a true merge commit `git diff-tree --first-parent <sha>`
captures the whole feature branch, but for a fast-forward/squash merge `<sha>`
has a single parent, so `--first-parent` only diffs the tip commit — undercounting
the files the ticket changed and skewing precision/recall.

Each test is anchored to a specific acceptance criterion. We build real temporary
git repositories and exercise the actual git plumbing the script uses.
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_finish_feature():
    spec = importlib.util.spec_from_file_location(
        "finish_feature", _REPO_ROOT / "scripts" / "finish_feature.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ff = _load_finish_feature()


# --- git fixture helpers -------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _runner_for(repo: Path):
    """A `_try`-shaped runner (returns (ok, stdout)) bound to a specific repo."""

    def run(*cmd):
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        return r.returncode == 0, r.stdout.strip()

    return run


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "develop")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    return repo


def _commit(repo: Path, fname: str, content: str = "x") -> str:
    (repo / fname).write_text(content)
    _git(repo, "add", fname)
    _git(repo, "commit", "-q", "-m", f"add {fname}")
    return _git(repo, "rev-parse", "HEAD")


def _true_merge_repo(tmp_path: Path):
    """develop ← (--no-ff merge of feature with commits a,b,c). Returns (repo, sha)."""
    repo = _init_repo(tmp_path)
    _commit(repo, "base.txt")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "a.txt")
    _commit(repo, "b.txt")
    _commit(repo, "c.txt")
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
    return repo, _git(repo, "rev-parse", "HEAD")


def _single_parent_repo(tmp_path: Path):
    """A feature tip (single-parent chain a→b→c) off develop's branch point.

    Mirrors a ff/squash merge sha: the recorded commit has one parent and its
    merge-base with `develop` is the branch point. Returns (repo, tip_sha).
    """
    repo = _init_repo(tmp_path)
    _commit(repo, "base.txt")
    _git(repo, "checkout", "-q", "-b", "feature")
    _commit(repo, "a.txt")
    _commit(repo, "b.txt")
    tip = _commit(repo, "c.txt")
    # develop intentionally left at the branch point (base.txt).
    return repo, tip


# --- AC1: detect true merge commit via second parent ---------------------


def test_ac1_detects_true_merge_commit(tmp_path):
    repo, sha = _true_merge_repo(tmp_path)
    assert ff._is_true_merge_commit(sha, run=_runner_for(repo)) is True


def test_ac1_detects_single_parent_commit(tmp_path):
    repo, sha = _single_parent_repo(tmp_path)
    assert ff._is_true_merge_commit(sha, run=_runner_for(repo)) is False


def test_ac1_uses_parent_line_count_from_object(tmp_path):
    """Detection is based on the count of `parent` lines in the commit object."""
    repo, sha = _true_merge_repo(tmp_path)
    raw = _git(repo, "cat-file", "-p", sha)
    parent_lines = [ln for ln in raw.splitlines() if ln.startswith("parent ")]
    assert len(parent_lines) == 2


# --- AC2: true merge commit keeps the --first-parent diff unchanged -------


def test_ac2_true_merge_uses_first_parent_diff(tmp_path, capsys):
    repo, sha = _true_merge_repo(tmp_path)
    run = _runner_for(repo)

    # The merge path must be taken (two parents), not the ff/squash fallback.
    assert ff._is_true_merge_commit(sha, run=run) is True
    files = set(ff._changed_files_for_merge(sha, "develop", run=run))

    # Result must equal the original `--first-parent` diff-tree command output —
    # the merge-commit path is left unchanged (AC2).
    _, raw = run(
        "git", "diff-tree", "-r", "--no-commit-id", "--name-only",
        "--first-parent", sha,
    )
    expected = {f for f in raw.splitlines() if f.strip()}
    assert files == expected

    # The single-parent merge-base fallback must NOT have been used.
    assert "single-parent" not in capsys.readouterr().out


# --- AC3 / AC4: single-parent falls back to merge-base diff ---------------


def test_ac3_single_parent_uses_merge_base_diff(tmp_path, capsys):
    repo, sha = _single_parent_repo(tmp_path)
    run = _runner_for(repo)
    files = set(ff._changed_files_for_merge(sha, "develop", run=run))

    # Equals the merge-base..sha diff, the prescribed fallback.
    _, mb = run("git", "merge-base", "develop", sha)
    _, raw = run("git", "diff", "--name-only", f"{mb}..{sha}")
    expected = {f for f in raw.splitlines() if f.strip()}
    assert files == expected

    out = capsys.readouterr().out
    assert "single-parent" in out
    assert "merge-base" in out


def test_ac4_ff_squash_returns_full_branch_file_set(tmp_path):
    repo, sha = _single_parent_repo(tmp_path)
    run = _runner_for(repo)
    files = set(ff._changed_files_for_merge(sha, "develop", run=run))

    # All three feature files, not just the tip commit's file.
    assert {"a.txt", "b.txt", "c.txt"} <= files

    # The old --first-parent path would have returned only the tip file.
    _, raw = run(
        "git", "diff-tree", "-r", "--no-commit-id", "--name-only",
        "--first-parent", sha,
    )
    tip_only = {f for f in raw.splitlines() if f.strip()}
    assert tip_only == {"c.txt"}
    assert len(files) > len(tip_only)


# --- AC5: precision/recall not skewed downward for ff/squash --------------


def test_ac5_metrics_not_skewed_for_ff_squash(tmp_path):
    """ff/squash merges must not skew accuracy metrics downward (AC5).

    With the full changed-file set the estimate's precision/recall reflect the
    whole branch; the pre-fix tip-only set undercounts `actual`, dragging
    precision down to a fraction of its correct value.
    """
    import estimate_accuracy

    repo, sha = _single_parent_repo(tmp_path)
    run = _runner_for(repo)

    predicted = ["a.txt", "b.txt", "c.txt"]
    actual = ff._changed_files_for_merge(sha, "develop", run=run)
    precision, recall = estimate_accuracy.compute_metrics(predicted, actual)
    assert precision == 1.0
    assert recall == 1.0

    # Pre-fix tip-only behavior: only the tip file counts as "actual", so
    # precision is skewed downward to 1/3.
    _, raw = run(
        "git", "diff-tree", "-r", "--no-commit-id", "--name-only",
        "--first-parent", sha,
    )
    tip_only = [f for f in raw.splitlines() if f.strip()]
    skewed_precision, _ = estimate_accuracy.compute_metrics(predicted, tip_only)
    assert skewed_precision < precision
