"""Tests for issue #1437: max_coder_slots clamped to hard cap 4 in load_config.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

AC1  In load_config, after parsing max_coder_slots from YAML, the value is
     clamped to MAX_SLOTS (4) before being stored in the returned config.
AC2  YAML max_coder_slots > 4 (e.g. 10) → load_config returns 4.
AC3  YAML max_coder_slots <= 4 → value returned unchanged.
AC4  The clamping constant is MAX_SLOTS (not a magic number) and MAX_SLOTS is
     defined as 4 in config.py.
AC5  concurrent_scheduler reads max_coder_slots from config after load_config,
     so the thread count spawned never exceeds 4 regardless of YAML.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import services.sprint_manager.config as config
from services.sprint_manager.config import MAX_SLOTS, load_config


def _write_yaml(tmp_path: Path, body: str) -> Path:
    """Write a sprint.yaml with the required base fields plus `body`.

    load_config validates that the worktree paths exist, so they point at real
    temp directories created here.
    """
    coder = tmp_path / "coder"
    tester = tmp_path / "tester"
    coder.mkdir(exist_ok=True)
    tester.mkdir(exist_ok=True)
    required = (
        "repo_name: owner/repo\n"
        "worktrees:\n"
        f"  coder: {coder}\n"
        f"  tester: {tester}\n"
    )
    p = tmp_path / "sprint.yaml"
    p.write_text(required + textwrap.dedent(body))
    return p


# ── AC4 — MAX_SLOTS defined as 4 in config.py ───────────────────────────────
def test_max_slots_constant_defined_as_four():
    assert MAX_SLOTS == 4
    assert hasattr(config, "MAX_SLOTS")


# ── AC2 — top-level value above the cap is clamped to 4 ──────────────────────
def test_top_level_value_above_cap_clamped(tmp_path):
    path = _write_yaml(tmp_path, """\
        max_coder_slots: 10
    """)
    cfg = load_config(path)
    assert cfg.max_coder_slots == 4


# ── AC2 — nested agent_config.coder.max_slots above cap is clamped ───────────
def test_nested_value_above_cap_clamped(tmp_path):
    path = _write_yaml(tmp_path, """\
        agent_config:
          coder:
            max_slots: 8
    """)
    cfg = load_config(path)
    assert cfg.max_coder_slots == 4


# ── AC3 — value at or below the cap is preserved ─────────────────────────────
def test_value_below_cap_preserved(tmp_path):
    path = _write_yaml(tmp_path, """\
        max_coder_slots: 2
    """)
    cfg = load_config(path)
    assert cfg.max_coder_slots == 2


# ── AC3 — value exactly at the cap is preserved ──────────────────────────────
def test_value_at_cap_preserved(tmp_path):
    path = _write_yaml(tmp_path, """\
        max_coder_slots: 4
    """)
    cfg = load_config(path)
    assert cfg.max_coder_slots == 4


# ── AC1 — unset value stays None (no clamping of "not configured") ───────────
def test_unset_value_stays_none(tmp_path):
    path = _write_yaml(tmp_path, """\
    """)
    cfg = load_config(path)
    assert cfg.max_coder_slots is None


# ── AC5 — scheduler spawns at most MAX_SLOTS worker threads ──────────────────
def test_scheduler_thread_count_capped_by_config(tmp_path, monkeypatch):
    """The clamped config value flows into the scheduler so the number of
    worker threads spawned at concurrent_scheduler.py never exceeds MAX_SLOTS."""
    import threading
    from services.sprint_manager import concurrent_scheduler

    path = _write_yaml(tmp_path, """\
        max_coder_slots: 10
    """)
    cfg = load_config(path)
    assert cfg.max_coder_slots == 4

    peak = {"threads": 0}
    real_thread = threading.Thread

    class _CountingThread(real_thread):  # type: ignore[misc]
        def start(self):  # noqa: D401
            peak["threads"] += 1
            super().start()

    monkeypatch.setattr(concurrent_scheduler.threading, "Thread", _CountingThread)

    from services.sprint_manager.pipeline import StageResult

    def _noop(ticket, attempt):
        return StageResult.PASS

    concurrent_scheduler.run_concurrent_level(
        ["t1", "t2", "t3", "t4", "t5", "t6"],
        max_coder_slots=cfg.max_coder_slots,
        code_fn=_noop,
        test_fn=_noop,
    )
    assert peak["threads"] <= MAX_SLOTS
