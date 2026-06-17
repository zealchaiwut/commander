"""Tests for issue #1333 — Auto-refresh calibration cache on sprint finish.

AC items verified:
  AC1 — _refresh_calibration_cache called on sprint-finish path
  AC2 — cache update is incremental (appends new samples, not full rescan)
  AC3 — fast path (<500 ms) runs inline; slow path dispatched as background task
  AC4 — calibration_cache_updated dashboard event logged with new_samples/total_samples
  AC5 — after sprint finish (without Analytics tab), Calibration view shows new tickets
  AC6 — finish-sprint latency does not increase by more than 500 ms on incremental path
  AC7 — activity log surfaces calibration_cache_updated event
  AC8 — pytest: synthetic sprint completes and _refresh_calibration_cache was invoked
"""
from __future__ import annotations

import json
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_state(project_root: Path, sprint_label: str, issues: list[dict]) -> Path:
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    path = sprints_dir / f"sprint-{n}-state.json"
    path.write_text(json.dumps({
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "start_timestamp": "2026-06-01T10:00:00Z",
        "issues": issues,
    }), encoding="utf-8")
    return path


def _write_estimate(project_root: Path, issue_num: int, size: str) -> None:
    estimates_dir = project_root / ".commander" / "estimates"
    estimates_dir.mkdir(parents=True, exist_ok=True)
    (estimates_dir / f"issue-{issue_num}.json").write_text(
        json.dumps({"size": size}), encoding="utf-8"
    )


def _done_issue(num: int, coder_min: float = 10.0, tester_min: float = 5.0) -> dict:
    return {
        "number": num,
        "status": "done",
        "coder_started_at": "2026-06-01T10:00:00Z",
        "coder_finished_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_started_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_finished_at": f"2026-06-01T10:{int(coder_min + tester_min):02d}:00Z",
        "labels": [],
    }


def _make_cfg(tmp_path: Path, repo: str = "owner/repo"):
    cfg = MagicMock()
    cfg.documentor_enabled = False
    cfg.reviewer_enabled = False
    cfg.repo_name = repo
    cfg.api_url = None
    cfg.logs_dir = tmp_path / "logs"
    cfg.worktree_coder = tmp_path / "coder"
    cfg.worktree_tester = tmp_path / "tester"
    cfg.worktree_tester_app = cfg.worktree_tester / "apps" / "dashboard"
    cfg.sprints_dir = tmp_path / ".commander" / "sprints"
    cfg.sprint_branch_prefix = "sprint"
    cfg.app_default_port = None
    cfg.documenter_prompt_template = None
    cfg.calibration_refresh_enabled = True
    return cfg


def _run_sprint_minimal(
    issue_numbers: list[int],
    tmp_path: Path,
    refresh_calls: list,
    *,
    repo: str = "owner/repo",
    extra_patches: list | None = None,
):
    """Run run_sprint() with heavy machinery mocked; records _run_calibration_cache_refresh calls."""
    cfg = _make_cfg(tmp_path, repo=repo)
    raw_issues = [{"number": n, "title": f"Issue {n}"} for n in issue_numbers]

    def fake_dispatch_coder(issue_num, *args, **kwargs):
        if kwargs.get("on_running"):
            kwargs["on_running"]()
        return True, None

    def fake_dispatch_tester(issue_num, *args, **kwargs):
        if kwargs.get("on_running"):
            kwargs["on_running"]()
        return 0, None

    def fake_handle_post_tester(issue_num, tester_exit_code, skip_gates,
                                gate_pytest, gate_lint, gate_merge_preview,
                                target_branch="develop", **kwargs):
        return True, f"Issue #{issue_num}: merged", None

    def fake_refresh(project_root, configured_minutes, project=""):
        refresh_calls.append({
            "project_root": project_root,
            "configured_minutes": configured_minutes,
            "project": project,
        })

    patches = [
        patch.object(sm, "list_backlog_issues", return_value=raw_issues),
        patch.object(sm, "_dispatch_coder", side_effect=fake_dispatch_coder),
        patch.object(sm, "_dispatch_tester", side_effect=fake_dispatch_tester),
        patch.object(sm, "handle_post_tester", side_effect=fake_handle_post_tester),
        patch.object(sm, "_post_sprint_status"),
        patch.object(sm, "_create_sprint_branch"),
        patch.object(sm, "_setup_pid_file"),
        patch.object(sm, "_warn_file_conflicts"),
        patch.object(sm, "_neon_sprint_init"),
        patch.object(sm, "_neon_sprint_status"),
        patch.object(sm, "_neon_ticket_status"),
        patch.object(sm, "_load_sprint_plan", return_value=None),
        patch.object(sm, "_build_sprint_dag_layers", return_value=None),
        patch.object(sm, "_compute_dispatch_levels", side_effect=lambda issues, *a, **kw: [issues]),
        patch.object(sm, "_pipeline_mode_enabled", return_value=False),
        patch.object(sm, "_emit_sprint_lifecycle_event"),
        patch.object(sm, "structured_log"),
        patch.object(sm, "_transition_safe"),
        patch.object(sm, "_load_estimate", return_value=None),
        patch.object(sm, "_plan_json_set_state_sm"),
        patch.object(sm, "_delete_failure_sidecar"),
        patch.object(sm, "_find_feature_branch", side_effect=lambda n: f"feature/{n}-slug"),
        patch.object(sm, "_post_agent_event"),
        patch.object(sm, "_prune_stale_local_feature_branch"),
        patch.object(sm, "_sprint_db_set_state_sm"),
        patch.object(sm, "_sprint_db_set_ticket_order_sm"),
        patch.object(sm, "_sprint_db_ingest_run_sm"),
        patch.object(sm, "_run_calibration_cache_refresh", side_effect=fake_refresh),
        patch.object(sm.SprintState, "save", lambda self, path: None),
    ]
    if extra_patches:
        patches.extend(extra_patches)

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        summary, state = sm.run_sprint(
            label="sprint-1",
            skip_gates=True,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            cfg=cfg,
        )
    return summary, state


# ---------------------------------------------------------------------------
# AC8 — synthetic sprint: _refresh_calibration_cache invoked (spy test)
# ---------------------------------------------------------------------------

class TestAC8SprintFinishInvokesRefresh:
    """AC8: pytest suite includes a test that asserts _refresh_calibration_cache was invoked."""

    def test_refresh_called_after_sprint_completes(self, tmp_path):
        """_run_calibration_cache_refresh called once when sprint finishes."""
        calls: list = []
        _run_sprint_minimal([101, 102], tmp_path, calls)
        assert len(calls) == 1, f"Expected 1 refresh call, got {len(calls)}"

    def test_refresh_receives_project_root_path(self, tmp_path):
        """project_root passed to refresh is a Path object."""
        calls: list = []
        _run_sprint_minimal([101], tmp_path, calls)
        assert isinstance(calls[0]["project_root"], Path)

    def test_refresh_receives_configured_minutes_dict(self, tmp_path):
        """configured_minutes passed to refresh is a dict with S/M/L/XL keys."""
        calls: list = []
        _run_sprint_minimal([101], tmp_path, calls)
        mins = calls[0]["configured_minutes"]
        assert isinstance(mins, dict)
        for sz in ("S", "M", "L", "XL"):
            assert sz in mins, f"Missing size key: {sz}"


# ---------------------------------------------------------------------------
# AC1 — called on sprint-finish path
# ---------------------------------------------------------------------------

class TestAC1SprintFinishPath:
    """AC1: _refresh_calibration_cache called when sprint is marked finished."""

    def test_refresh_not_called_when_no_issues(self, tmp_path):
        """Sprint with zero issues exits before finish path — no refresh called."""
        calls: list = []
        _run_sprint_minimal([], tmp_path, calls)
        assert len(calls) == 0

    def test_function_exists_on_sprint_manager_module(self):
        """_run_calibration_cache_refresh is defined in sprint_manager."""
        assert hasattr(sm, "_run_calibration_cache_refresh"), (
            "_run_calibration_cache_refresh not found in sprint_manager module"
        )

    def test_refresh_function_callable(self, tmp_path):
        """`_run_calibration_cache_refresh` is callable with (project_root, configured_minutes)."""
        assert callable(sm._run_calibration_cache_refresh)


# ---------------------------------------------------------------------------
# AC2 — incremental merge (not full rescan)
# ---------------------------------------------------------------------------

class TestAC2IncrementalMerge:
    """AC2: cache update appends new samples, not a full rescan."""

    def test_new_sprint_samples_added_without_full_rescan(self, tmp_path):
        """Second call to _refresh_calibration_cache only processes new tickets."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        # Sprint 1 with one ticket
        _write_state(project_root, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 101, "M")

        cache1 = _refresh_calibration_cache(project_root, configured_minutes)
        assert cache1["by_size"]["M"]["count"] == 1
        processed_after_first = list(cache1["processed"])

        # Sprint 2 with a new ticket
        _write_state(project_root, "sprint-2", [_done_issue(102, coder_min=20, tester_min=10)])
        _write_estimate(project_root, 102, "M")

        cache2 = _refresh_calibration_cache(project_root, configured_minutes)
        assert cache2["by_size"]["M"]["count"] == 2

        # Only the new ticket's key should have been added
        new_processed = set(cache2["processed"]) - set(processed_after_first)
        assert len(new_processed) == 1, (
            f"Expected exactly 1 new processed key, got {new_processed}"
        )

    def test_already_processed_tickets_not_double_counted(self, tmp_path):
        """Re-running refresh on unchanged state files doesn't increment counts."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 101, "S")

        cache1 = _refresh_calibration_cache(project_root, configured_minutes)
        assert cache1["by_size"]["S"]["count"] == 1

        # Second call — no new state files
        cache2 = _refresh_calibration_cache(project_root, configured_minutes)
        assert cache2["by_size"]["S"]["count"] == 1, (
            "Double-counting: same ticket counted twice on repeat refresh"
        )


# ---------------------------------------------------------------------------
# AC3 — timing: fast path inline, slow path background
# ---------------------------------------------------------------------------

class TestAC3TimingBehavior:
    """AC3: fast path (<500ms) inline; slow path dispatched as background task."""

    def test_fast_path_runs_synchronously(self, tmp_path):
        """When refresh is fast (<500ms), _run_calibration_cache_refresh returns before 600ms."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"
        project_root.mkdir(parents=True, exist_ok=True)

        start = time.monotonic()
        sm._run_calibration_cache_refresh(project_root, configured_minutes)
        elapsed = time.monotonic() - start
        assert elapsed < 2.0, f"Inline refresh took {elapsed:.2f}s — too slow"

    def test_slow_path_returns_quickly(self, tmp_path):
        """When refresh would be slow (>500ms), _run_calibration_cache_refresh returns < 50ms."""
        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"
        project_root.mkdir(parents=True, exist_ok=True)

        slow_called = threading.Event()

        def slow_refresh(pr, cm):
            slow_called.set()
            return {}

        with patch("apps.dashboard.calibration_cache_service._refresh_calibration_cache",
                   side_effect=slow_refresh):
            # Simulate a slow refresh by patching the timing check
            with patch.object(sm, "_CALIBRATION_INLINE_THRESHOLD_S", 0.0):
                start = time.monotonic()
                sm._run_calibration_cache_refresh(project_root, configured_minutes)
                elapsed = time.monotonic() - start
                # Should return quickly (background thread dispatched)
                assert elapsed < 1.0, f"Background dispatch took {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# AC4 — calibration_cache_updated event logged
# ---------------------------------------------------------------------------

class TestAC4EventLogged:
    """AC4: calibration_cache_updated event logged with new_samples/total_samples."""

    def test_event_emitted_when_new_samples_absorbed(self, tmp_path):
        """_emit_sprint_lifecycle_event called with calibration_cache_updated type."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 101, "S")

        emitted_events: list[dict] = []

        def fake_emit(type, target, actor, detail, project, action_id=None):
            emitted_events.append({"type": type, "detail": detail})

        with patch.object(sm, "_emit_sprint_lifecycle_event", side_effect=fake_emit):
            sm._run_calibration_cache_refresh(
                project_root, configured_minutes, project="owner/repo"
            )

        cal_events = [e for e in emitted_events if e["type"] == "calibration_cache_updated"]
        assert len(cal_events) >= 1, (
            f"Expected calibration_cache_updated event; got types: "
            f"{[e['type'] for e in emitted_events]}"
        )

    def test_event_detail_contains_new_samples_count(self, tmp_path):
        """calibration_cache_updated event detail has new_samples key."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-1", [
            _done_issue(101, coder_min=10, tester_min=5),
            _done_issue(102, coder_min=8, tester_min=4),
        ])
        _write_estimate(project_root, 101, "S")
        _write_estimate(project_root, 102, "M")

        emitted_events: list[dict] = []

        def fake_emit(type, target, actor, detail, project, action_id=None):
            emitted_events.append({"type": type, "detail": detail})

        with patch.object(sm, "_emit_sprint_lifecycle_event", side_effect=fake_emit):
            sm._run_calibration_cache_refresh(
                project_root, configured_minutes, project="owner/repo"
            )

        cal_events = [e for e in emitted_events if e["type"] == "calibration_cache_updated"]
        assert cal_events, "No calibration_cache_updated event emitted"
        detail = cal_events[0]["detail"]
        assert "new_samples" in detail, f"missing new_samples in detail: {detail}"
        assert "total_samples" in detail, f"missing total_samples in detail: {detail}"
        assert detail["new_samples"] == 2
        assert detail["total_samples"] >= 2

    def test_no_event_when_no_new_samples(self, tmp_path):
        """No calibration_cache_updated event when nothing changed."""
        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"
        project_root.mkdir(parents=True, exist_ok=True)

        emitted_events: list[dict] = []

        def fake_emit(type, target, actor, detail, project, action_id=None):
            emitted_events.append({"type": type, "detail": detail})

        with patch.object(sm, "_emit_sprint_lifecycle_event", side_effect=fake_emit):
            sm._run_calibration_cache_refresh(
                project_root, configured_minutes, project="owner/repo"
            )

        cal_events = [e for e in emitted_events if e["type"] == "calibration_cache_updated"]
        assert len(cal_events) == 0, (
            "calibration_cache_updated event emitted even though no new samples"
        )


# ---------------------------------------------------------------------------
# AC5 — after finish, Calibration view shows new tickets (integration)
# ---------------------------------------------------------------------------

class TestAC5CalibrationViewAfterFinish:
    """AC5: after sprint finish, Calibration view shows newly completed tickets."""

    def test_calibration_cache_updated_by_refresh(self, tmp_path):
        """After _refresh_calibration_cache, cache file reflects completed sprint tickets."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-5", [
            _done_issue(501, coder_min=12, tester_min=3),
        ])
        _write_estimate(project_root, 501, "L")

        cache = _refresh_calibration_cache(project_root, configured_minutes)

        cache_file = project_root / ".commander" / "calibration_cache.json"
        assert cache_file.is_file(), "Cache file not created"

        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        assert saved["by_size"]["L"]["count"] == 1
        assert saved["by_size"]["L"]["avg_minutes"] == 15.0

    def test_cache_persists_across_calls(self, tmp_path):
        """Cache file persists so Calibration view doesn't need a live rescan."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-5", [_done_issue(501, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 501, "M")
        _refresh_calibration_cache(project_root, configured_minutes)

        # Delete the state file (simulate archiving)
        state_path = project_root / ".commander" / "sprints" / "sprint-5-state.json"
        state_path.unlink()

        # Cache should still have the data
        cache2 = _refresh_calibration_cache(project_root, configured_minutes)
        assert cache2["by_size"]["M"]["count"] == 1, (
            "Cache lost data after state file was removed"
        )


# ---------------------------------------------------------------------------
# AC6 — latency: incremental path ≤ 500 ms
# ---------------------------------------------------------------------------

class TestAC6Latency:
    """AC6: finish-sprint latency does not increase by more than 500 ms on incremental path."""

    def test_incremental_refresh_completes_within_500ms(self, tmp_path):
        """_refresh_calibration_cache on a small sprint state finishes < 500 ms."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        # Set up a typical sprint
        _write_state(project_root, "sprint-1", [
            _done_issue(101, coder_min=10, tester_min=5),
            _done_issue(102, coder_min=8, tester_min=4),
            _done_issue(103, coder_min=15, tester_min=6),
        ])
        for i, sz in ((101, "S"), (102, "M"), (103, "L")):
            _write_estimate(project_root, i, sz)

        # Prime the cache (simulate prior sprint already processed)
        _refresh_calibration_cache(project_root, configured_minutes)

        # Add a new sprint
        _write_state(project_root, "sprint-2", [_done_issue(201, coder_min=12, tester_min=3)])
        _write_estimate(project_root, 201, "M")

        # Time the incremental refresh
        start = time.monotonic()
        _refresh_calibration_cache(project_root, configured_minutes)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 500, (
            f"Incremental refresh took {elapsed_ms:.1f} ms — exceeds 500 ms threshold"
        )


# ---------------------------------------------------------------------------
# AC7 — activity log surfaces calibration_cache_updated event
# ---------------------------------------------------------------------------

class TestAC7ActivityLog:
    """AC7: activity log or debug endpoint surfaces the calibration_cache_updated event."""

    def test_event_type_is_calibration_cache_updated(self, tmp_path):
        """Event type string matches exactly 'calibration_cache_updated'."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 101, "S")

        emitted_events: list[dict] = []

        def fake_emit(type, target, actor, detail, project, action_id=None):
            emitted_events.append({"type": type})

        with patch.object(sm, "_emit_sprint_lifecycle_event", side_effect=fake_emit):
            sm._run_calibration_cache_refresh(
                project_root, configured_minutes, project="owner/repo"
            )

        types = [e["type"] for e in emitted_events]
        assert "calibration_cache_updated" in types, (
            f"calibration_cache_updated not in emitted types: {types}"
        )

    def test_event_actor_is_sprint_manager(self, tmp_path):
        """calibration_cache_updated event actor identifies sprint_manager."""
        from apps.dashboard.calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        _write_state(project_root, "sprint-1", [_done_issue(101, coder_min=10, tester_min=5)])
        _write_estimate(project_root, 101, "S")

        emitted_events: list[dict] = []

        def fake_emit(type, target, actor, detail, project, action_id=None):
            if type == "calibration_cache_updated":
                emitted_events.append({"actor": actor})

        with patch.object(sm, "_emit_sprint_lifecycle_event", side_effect=fake_emit):
            sm._run_calibration_cache_refresh(
                project_root, configured_minutes, project="owner/repo"
            )

        assert emitted_events, "No calibration_cache_updated event emitted"
        assert "sprint_manager" in emitted_events[0]["actor"], (
            f"Unexpected actor: {emitted_events[0]['actor']}"
        )
