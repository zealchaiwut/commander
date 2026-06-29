"""Persistent shared venv for the worktree pool (Run→dispatch latency fix).

Previously every Run rebuilt a per-slot venv + `pip install` (~2-3 min before the
first coder dispatch). Now a single shared venv (keyed by requirements hash,
stored outside the worktrees) is built once and each slot symlinks to it.
"""
from __future__ import annotations

from pathlib import Path

# sys.path (repo root, services/sprint_manager, apps/dashboard) is configured by
# tests/conftest.py with apps/dashboard first, so `config` resolves correctly.
import worktree_pool as wp  # noqa: E402


def _make_pool(tmp_path: Path, requirements: Path | None):
    return wp.WorktreePool(
        pool_dir=tmp_path / "runtime" / "worktree-pool",
        repo_root=tmp_path / "repo",
        base_branch="develop",
        slots=1,
        requirements_file=requirements,
    )


def _fake_shared_venv(pool) -> Path:
    """Create a stand-in shared venv (bin/python marker) so _link_venv accepts it."""
    shared = pool._shared_venv()
    (shared / "bin").mkdir(parents=True, exist_ok=True)
    (shared / "bin" / "python").write_text("#!/bin/sh\n")
    return shared


def test_venv_cache_is_outside_pool_dir(tmp_path):
    """The shared venv must live outside pool_dir so teardown (rmtree pool_dir)
    can't delete it."""
    pool = _make_pool(tmp_path, None)
    assert pool._venv_cache not in pool.pool_dir.parents
    assert pool.pool_dir not in pool._venv_cache.parents
    # both under runtime/, siblings
    assert pool._venv_cache.parent == pool.pool_dir.parent


def test_req_hash_varies_with_requirements(tmp_path):
    assert _make_pool(tmp_path, None)._req_hash() == "norequirements"
    req = tmp_path / "req.txt"
    req.write_text("fastapi\n")
    h1 = _make_pool(tmp_path, req)._req_hash()
    assert h1 != "norequirements" and len(h1) == 16
    req.write_text("fastapi\nhttpx\n")
    assert _make_pool(tmp_path, req)._req_hash() != h1


def test_link_venv_creates_symlink_to_shared(tmp_path):
    pool = _make_pool(tmp_path, None)
    shared = _fake_shared_venv(pool)
    wt = tmp_path / "repo" / "slot-0"
    wt.mkdir(parents=True)
    pool._link_venv(wt)
    slot_venv = wt / "venv"
    assert slot_venv.is_symlink()
    assert slot_venv.resolve() == shared.resolve()


def test_link_venv_idempotent(tmp_path):
    pool = _make_pool(tmp_path, None)
    _fake_shared_venv(pool)
    wt = tmp_path / "repo" / "slot-0"
    wt.mkdir(parents=True)
    pool._link_venv(wt)
    pool._link_venv(wt)  # second call must not error / must keep the link
    assert (wt / "venv").is_symlink()


def test_ensure_shared_venv_built_once(tmp_path, monkeypatch):
    """First call builds (one venv create); a second call reuses — no rebuild."""
    pool = _make_pool(tmp_path, None)
    calls: list[list] = []

    def fake_run(args, *, cwd, timeout=300):
        calls.append(list(args))
        # simulate `python -m venv <path>` creating bin/python
        if "venv" in args:
            vp = Path(args[-1])
            (vp / "bin").mkdir(parents=True, exist_ok=True)
            (vp / "bin" / "python").write_text("")
        return (True, "", "")

    monkeypatch.setattr(wp, "_run", fake_run)
    p1 = pool._ensure_shared_venv()
    n_after_first = len(calls)
    assert n_after_first >= 1
    p2 = pool._ensure_shared_venv()
    assert p1 == p2
    assert len(calls) == n_after_first, "shared venv rebuilt on reuse — should be cached"
