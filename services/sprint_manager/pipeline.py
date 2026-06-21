"""Concurrent two-stage dispatch pipeline for the sprint manager (issue #737).

This module owns the *opt-in* concurrent pipeline that lets one coder worker
build the next ticket while one tester worker validates the previous one,
roughly halving wall-clock time for independent tickets in the same dispatch
level. The feature is default-off; serial dispatch remains the canonical path.

Design contract (mirrors the issue's acceptance criteria):

  - Exactly one coder worker and one tester worker run at a time — never more.
  - The coder worker pulls from a coder queue; the tester worker pulls from a
    tester queue that is fed by coder completions.
  - A tester rejection pushes the ticket to the *front* of the coder queue.
  - Retries respect a cap (default 3 attempts). A ticket that exceeds the cap is
    marked needs-rework and dropped from both queues without blocking the rest.
  - One level is processed at a time. The caller iterates levels sequentially,
    so the hard level barrier (never advance until the current level finishes)
    is structural — `run_level` does not return until its level is drained.

Both serial and pipeline execution route through `run_level` and call the same
`code_fn` / `test_fn` stage callables in the same per-ticket order, so every
per-ticket side effect is identical between modes by construction. The only
difference is whether the two stages overlap across tickets.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from services.sprint_manager.state import IssueState

# Cap on coder attempts per ticket before it is dropped as needs-rework.
DEFAULT_MAX_ATTEMPTS = 3

# Kill-switch env var: when truthy, serial mode is forced regardless of any
# pipeline_mode setting value.
PIPELINE_KILL_SWITCH_ENV = "COMMANDER_PIPELINE_DISABLE"

_TRUTHY = {"1", "true", "yes", "on"}


def _is_truthy(raw: Any) -> bool:
    return str(raw).strip().lower() in _TRUTHY


class StageResult(Enum):
    """Outcome of a coder or tester stage for one ticket attempt."""

    PASS = "pass"        # coder: branch ready / tester: merged
    REJECT = "reject"    # tester only: send back to the coder queue
    FAIL = "fail"        # infra/non-retryable: drop, no rework label
    EXHAUST = "exhaust"  # tester only: consecutive identical failure — finalize needs-rework immediately


def pipeline_mode_enabled(
    sprint_setting: Optional[bool] = None,
    project_setting: Optional[bool] = None,
    env: Optional[dict] = None,
) -> bool:
    """Resolve the effective pipeline mode.

    Precedence (highest first):
      1. Kill-switch env var — when truthy, always returns False (force serial).
      2. Per-sprint setting, when not None.
      3. Per-project setting, when not None.
      4. Default: False (serial).
    """
    env = env if env is not None else os.environ
    if _is_truthy(env.get(PIPELINE_KILL_SWITCH_ENV, "")):
        return False
    if sprint_setting is not None:
        return bool(sprint_setting)
    if project_setting is not None:
        return bool(project_setting)
    return False


@dataclass
class LevelResult:
    """Outcome of processing a single dispatch level."""

    merged: list = field(default_factory=list)
    needs_rework: list = field(default_factory=list)
    dropped: list = field(default_factory=list)
    # Ordered record of completed stages: (ticket, "coder"|"tester", attempt).
    order: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)


def run_level(
    tickets: list,
    code_fn: Callable[[Any, int], StageResult],
    test_fn: Callable[[Any, int], StageResult],
    *,
    pipeline: bool,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    on_merged: Optional[Callable[[Any], None]] = None,
    on_needs_rework: Optional[Callable[[Any], None]] = None,
    on_dropped: Optional[Callable[[Any], None]] = None,
) -> LevelResult:
    """Process all tickets in one dispatch level and return a LevelResult.

    `code_fn(ticket, attempt)` runs the coder stage and returns
    StageResult.PASS (a SIT feature branch is ready) or StageResult.FAIL
    (infra/no-work — drop the ticket, no rework label).

    `test_fn(ticket, attempt)` runs the tester + gates and returns
    StageResult.PASS (merged), StageResult.REJECT (logic failure — re-queue to
    the coder), or StageResult.FAIL (infra — drop without rework).

    `attempt` is 1-based and counts coder runs for that ticket.

    When `pipeline` is False the two stages run strictly one ticket at a time.
    When True, one coder thread and one tester thread run concurrently, sharing
    a coder queue and a tester queue. Either way the per-ticket sequence of
    stage calls and callbacks is identical.
    """
    if pipeline:
        return _run_pipeline(
            tickets, code_fn, test_fn,
            max_attempts=max_attempts,
            on_merged=on_merged,
            on_needs_rework=on_needs_rework,
            on_dropped=on_dropped,
        )
    return _run_serial(
        tickets, code_fn, test_fn,
        max_attempts=max_attempts,
        on_merged=on_merged,
        on_needs_rework=on_needs_rework,
        on_dropped=on_dropped,
    )


def _run_serial(
    tickets, code_fn, test_fn, *,
    max_attempts, on_merged, on_needs_rework, on_dropped,
) -> LevelResult:
    result = LevelResult()
    attempts: dict = {t: 0 for t in tickets}
    result.attempts = attempts
    work: deque = deque(tickets)

    while work:
        ticket = work.popleft()
        attempts[ticket] += 1
        attempt = attempts[ticket]

        code_res = code_fn(ticket, attempt)
        result.order.append((ticket, "coder", attempt))
        if code_res is not StageResult.PASS:
            result.dropped.append(ticket)
            if on_dropped:
                on_dropped(ticket)
            continue

        test_res = test_fn(ticket, attempt)
        result.order.append((ticket, "tester", attempt))
        _apply_tester_outcome(
            ticket, attempt, test_res, result, work, max_attempts,
            requeue_front=True,
            on_merged=on_merged, on_needs_rework=on_needs_rework,
            on_dropped=on_dropped,
        )

    return result


def _apply_tester_outcome(
    ticket, attempt, test_res, result, coder_work, max_attempts,
    *, requeue_front, on_merged, on_needs_rework, on_dropped,
) -> None:
    """Shared tester-result handling for both serial and pipeline paths."""
    if test_res is StageResult.PASS:
        result.merged.append(ticket)
        if on_merged:
            on_merged(ticket)
    elif test_res is StageResult.EXHAUST:
        # Consecutive identical failure — finalize as needs-rework immediately.
        result.needs_rework.append(ticket)
        if on_needs_rework:
            on_needs_rework(ticket)
    elif test_res is StageResult.REJECT:
        if attempt >= max_attempts:
            # Cap reached — drop from both queues, label needs-rework.
            result.needs_rework.append(ticket)
            if on_needs_rework:
                on_needs_rework(ticket)
        else:
            # Push to the FRONT of the coder queue for a fix attempt.
            if requeue_front:
                coder_work.appendleft(ticket)
            else:
                coder_work.append(ticket)
    else:  # StageResult.FAIL
        result.dropped.append(ticket)
        if on_dropped:
            on_dropped(ticket)


def _run_pipeline(
    tickets, code_fn, test_fn, *,
    max_attempts, on_merged, on_needs_rework, on_dropped,
) -> LevelResult:
    result = LevelResult()
    attempts: dict = {t: 0 for t in tickets}
    result.attempts = attempts

    coder_q: deque = deque(tickets)
    tester_q: deque = deque()
    terminal: set = set()
    total = len(tickets)

    lock = threading.Lock()
    cond = threading.Condition(lock)

    def _finished() -> bool:
        return len(terminal) >= total

    def coder_loop() -> None:
        while True:
            with cond:
                while not coder_q and not _finished():
                    cond.wait()
                if _finished():
                    cond.notify_all()
                    return
                ticket = coder_q.popleft()
                attempts[ticket] += 1
                attempt = attempts[ticket]

            code_res = code_fn(ticket, attempt)

            with cond:
                result.order.append((ticket, "coder", attempt))
                if code_res is StageResult.PASS:
                    tester_q.append(ticket)
                else:
                    terminal.add(ticket)
                    result.dropped.append(ticket)
                    if on_dropped:
                        on_dropped(ticket)
                cond.notify_all()

    def tester_loop() -> None:
        while True:
            with cond:
                while not tester_q and not _finished():
                    cond.wait()
                if _finished():
                    cond.notify_all()
                    return
                ticket = tester_q.popleft()
                attempt = attempts[ticket]

            test_res = test_fn(ticket, attempt)

            with cond:
                result.order.append((ticket, "tester", attempt))
                if test_res is StageResult.PASS:
                    terminal.add(ticket)
                    result.merged.append(ticket)
                    if on_merged:
                        on_merged(ticket)
                elif test_res is StageResult.EXHAUST:
                    # Consecutive identical failure — finalize immediately.
                    terminal.add(ticket)
                    result.needs_rework.append(ticket)
                    if on_needs_rework:
                        on_needs_rework(ticket)
                elif test_res is StageResult.REJECT:
                    if attempt >= max_attempts:
                        terminal.add(ticket)
                        result.needs_rework.append(ticket)
                        if on_needs_rework:
                            on_needs_rework(ticket)
                    else:
                        coder_q.appendleft(ticket)  # front of coder queue
                else:  # FAIL
                    terminal.add(ticket)
                    result.dropped.append(ticket)
                    if on_dropped:
                        on_dropped(ticket)
                cond.notify_all()

    coder_t = threading.Thread(target=coder_loop, name="pipeline-coder")
    tester_t = threading.Thread(target=tester_loop, name="pipeline-tester")
    coder_t.start()
    tester_t.start()
    coder_t.join()
    tester_t.join()
    return result


# ── Extracted pipeline dispatch functions (issue #1289) ──────────────────────
#
# The five functions below are extracted from sprint_manager.py as pure moves.
# sprint_manager.py re-imports all five so existing call sites remain unmodified.
#
# Dependencies on sprint_manager-internal symbols are resolved at call time
# via _lookup_in_sm() proxies, following the same pattern as dispatch.py.
# This avoids a circular import: pipeline.py is imported BY sprint_manager.py,
# so pipeline.py must not import sprint_manager.py at the module level.

# ── Path setup (mirrors sprint_manager.py) ────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPO_ROOT = _REPO_ROOT

# ── Service-module imports (no circular deps) ─────────────────────────────────

from services.logging import log as structured_log  # noqa: E402
from services.sprint_manager.timekeeping import (  # noqa: E402
    _wait_if_paused,
    _token_window_utc_now,
    _token_window_sums,
    _utcnow,
)
from services.sprint_manager.alerts import dispatch_alerts  # noqa: E402
from services.sprint_manager.worktree import _detect_port  # noqa: E402

# model_routing and events import sprint_manager lazily inside functions, so
# it's safe to import from them here at module level.
from services.sprint_manager.model_routing import (  # noqa: E402
    _resolve_coder_model,
    _effective_coder_backend,
)
from services.sprint_manager.events import (  # noqa: E402
    _emit_sprint_lifecycle_event,
    _emit_ticket_failed,
    _post_sprint_status,
)

try:
    from services.sprint_manager.state_machine import (  # noqa: PLC0415
        TicketState as _TicketState,
    )
    _STATE_MACHINE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _TicketState = None  # type: ignore[assignment]
    _STATE_MACHINE_AVAILABLE = False

try:
    from services.sprint_manager.dag_builder import (  # noqa: PLC0415
        build_dag as _dag_build,
        CycleError as _DAGCycleError,
    )
    _DAG_BUILDER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _dag_build = None  # type: ignore[assignment]
    _DAGCycleError = None  # type: ignore[assignment]
    _DAG_BUILDER_AVAILABLE = False

# ── Module-level aliases (preserve call-site names from sprint_manager.py) ────

# sprint_manager.py aliases StageResult → _StageResult and run_level → _run_pipeline_level;
# the moved functions use those names, so we alias them here.
_StageResult = StageResult
_run_pipeline_level = run_level

# ── FailureCategory (mirrors sprint_manager.FailureCategory) ─────────────────


class FailureCategory:
    """String constants for dispatch failure categories — mirrors sprint_manager.FailureCategory."""

    HANG             = "HANG"
    CRASH            = "CRASH"
    GATE_FAIL        = "GATE_FAIL"
    TESTER_REJECTED  = "TESTER_REJECTED"
    RETRY_EXHAUSTED  = "RETRY_EXHAUSTED"
    CODER_NO_WORK    = "CODER_NO_WORK"
    MERGE_CONFLICT   = "MERGE_CONFLICT"
    LINT_FAIL        = "LINT_FAIL"
    PYTEST_FAIL      = "PYTEST_FAIL"
    REBASE_CONFLICT  = "REBASE_CONFLICT"


_LOGIC_FAILURE_CATEGORIES: frozenset[str] = frozenset({
    FailureCategory.CODER_NO_WORK,
    FailureCategory.MERGE_CONFLICT,
    FailureCategory.LINT_FAIL,
    FailureCategory.PYTEST_FAIL,
})

# ── sys.modules proxy helper ──────────────────────────────────────────────────
# Deferred lookups via sys.modules avoid a circular import (pipeline.py is
# imported BY sprint_manager.py) while also allowing test monkeypatches
# applied to sprint_manager attributes to be respected at call time.


def _lookup_in_sm(attr: str, local_fn: Any) -> Any:
    """Return the sprint_manager attribute if it differs from local_fn.

    Checks both 'sprint_manager' and 'services.sprint_manager.sprint_manager'
    keys so that monkeypatches applied via either import path are found.
    Returns None when no patch is active (or sprint_manager not loaded).
    """
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None:
            _f = getattr(_sm, attr, None)
            if _f is not None and _f is not local_fn:
                return _f
    return None


# ── Proxy functions for sprint_manager-internal helpers ───────────────────────

def _load_estimate(*args: Any, **kwargs: Any) -> Optional[dict]:
    """Proxy to sprint_manager._load_estimate."""
    _f = _lookup_in_sm("_load_estimate", _load_estimate)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


def _db_agent_start_sm(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager._db_agent_start_sm."""
    _f = _lookup_in_sm("_db_agent_start_sm", _db_agent_start_sm)
    if _f is not None:
        _f(*args, **kwargs)


def _db_agent_finish_sm(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager._db_agent_finish_sm."""
    _f = _lookup_in_sm("_db_agent_finish_sm", _db_agent_finish_sm)
    if _f is not None:
        _f(*args, **kwargs)


def _dispatch_tester(*args: Any, **kwargs: Any) -> Any:
    """Proxy to sprint_manager._dispatch_tester."""
    _f = _lookup_in_sm("_dispatch_tester", _dispatch_tester)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_dispatch_tester: sprint_manager not loaded")


def handle_post_tester(*args: Any, **kwargs: Any) -> Any:
    """Proxy to sprint_manager.handle_post_tester."""
    _f = _lookup_in_sm("handle_post_tester", handle_post_tester)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("handle_post_tester: sprint_manager not loaded")


def record_failure(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager.record_failure."""
    _f = _lookup_in_sm("record_failure", record_failure)
    if _f is not None:
        _f(*args, **kwargs)


def _issue_log_path(*args: Any, **kwargs: Any) -> Any:
    """Proxy to sprint_manager._issue_log_path."""
    _f = _lookup_in_sm("_issue_log_path", _issue_log_path)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_issue_log_path: sprint_manager not loaded")


def _build_crash_detail(*args: Any, **kwargs: Any) -> str:
    """Proxy to sprint_manager._build_crash_detail."""
    _f = _lookup_in_sm("_build_crash_detail", _build_crash_detail)
    if _f is not None:
        return _f(*args, **kwargs)
    return ""


def _pool_acquire(*args: Any, **kwargs: Any) -> Optional[Path]:
    """Proxy to sprint_manager._pool_acquire."""
    _f = _lookup_in_sm("_pool_acquire", _pool_acquire)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


def _pool_release(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager._pool_release."""
    _f = _lookup_in_sm("_pool_release", _pool_release)
    if _f is not None:
        _f(*args, **kwargs)


def _find_feature_branch(*args: Any, **kwargs: Any) -> Optional[str]:
    """Proxy to sprint_manager._find_feature_branch."""
    _f = _lookup_in_sm("_find_feature_branch", _find_feature_branch)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


def _publish_gate_failure_analyses(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager._publish_gate_failure_analyses."""
    _f = _lookup_in_sm("_publish_gate_failure_analyses", _publish_gate_failure_analyses)
    if _f is not None:
        _f(*args, **kwargs)


def _write_runtime_port(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager._write_runtime_port."""
    _f = _lookup_in_sm("_write_runtime_port", _write_runtime_port)
    if _f is not None:
        _f(*args, **kwargs)


def _neon_ticket_status(*args: Any, **kwargs: Any) -> None:
    """Proxy to sprint_manager._neon_ticket_status."""
    _f = _lookup_in_sm("_neon_ticket_status", _neon_ticket_status)
    if _f is not None:
        _f(*args, **kwargs)


def _list_labeled_open_issues(*args: Any, **kwargs: Any) -> list:
    """Proxy to sprint_manager._list_labeled_open_issues."""
    _f = _lookup_in_sm("_list_labeled_open_issues", _list_labeled_open_issues)
    if _f is not None:
        return _f(*args, **kwargs)
    return []


def _is_dispatchable(*args: Any, **kwargs: Any) -> bool:
    """Proxy to sprint_manager._is_dispatchable."""
    _f = _lookup_in_sm("_is_dispatchable", _is_dispatchable)
    if _f is not None:
        return _f(*args, **kwargs)
    return False


def _transition_safe(*args: Any, **kwargs: Any) -> bool:
    """Proxy to label_transitions._transition_safe (deferred to avoid sys.path ordering issues)."""
    _f = _lookup_in_sm("_transition_safe", _transition_safe)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.label_transitions import _transition_safe as _real
    return _real(*args, **kwargs)


def _dispatch_coder(*args: Any, **kwargs: Any) -> Any:
    """Proxy to dispatch._dispatch_coder (deferred to avoid label_transitions import at module level)."""
    _f = _lookup_in_sm("_dispatch_coder", _dispatch_coder)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.dispatch import _dispatch_coder as _real
    return _real(*args, **kwargs)


# ── Extracted functions (pure moves from sprint_manager.py) ──────────────────


def _build_sprint_dag_layers(issues: "list[IssueState]") -> Optional[list[list[int]]]:
    """Build topological layers from file-overlap estimates.

    Returns list of layers (each a list of issue numbers) or None if dag_builder
    is unavailable, building fails, or a cycle is detected.
    """
    if not _DAG_BUILDER_AVAILABLE:
        return None
    try:
        tickets = []
        for iss in issues:
            est = _load_estimate(iss.number)
            files = (est or {}).get("files_likely_affected") or []
            tickets.append({"id": str(iss.number), "files_touched": files})
        result = _dag_build(tickets)  # type: ignore[misc]
        if isinstance(result, _DAGCycleError):  # type: ignore[arg-type]
            return None
        return [[int(tid) for tid in layer] for layer in result.layers]
    except Exception:
        return None


def _compute_dispatch_levels(
    issues: "list[IssueState]",
    plan_order: Optional[list[int]],
    dag_layers: Optional[list[list[int]]],
) -> "list[list[IssueState]]":
    """Arrange issues into dispatch levels.

    Uses dag_layers for level grouping when available. Within each level, sorts
    by plan_order (if present) then ascending issue number. Issues absent from
    the DAG are appended as a trailing level.
    """
    issue_map = {iss.number: iss for iss in issues}

    if plan_order:
        plan_idx: dict[int, int] = {n: i for i, n in enumerate(plan_order)}
        def _sort_key(num: int) -> tuple:
            return (plan_idx.get(num, len(plan_order)), num)
    else:
        def _sort_key(num: int) -> tuple:  # type: ignore[misc]
            return (num,)

    if dag_layers:
        levels: "list[list[IssueState]]" = []
        placed: set[int] = set()
        for layer in dag_layers:
            layer_nums = sorted([n for n in layer if n in issue_map], key=_sort_key)
            placed.update(layer_nums)
            if layer_nums:
                levels.append([issue_map[n] for n in layer_nums])
        trailing = sorted([n for n in issue_map if n not in placed], key=_sort_key)
        if trailing:
            levels.append([issue_map[n] for n in trailing])
        return levels or [[issue_map[n] for n in sorted(issue_map, key=_sort_key)]]

    all_nums = sorted(issue_map.keys(), key=_sort_key)
    return [[issue_map[n] for n in all_nums]]


def _warn_file_conflicts(issues: "list[IssueState]") -> None:
    """Warn when multiple pending issues share files in their estimates."""
    file_to_issues: dict[str, list[int]] = {}
    for issue_state in issues:
        if issue_state.status not in ("pending", ""):
            continue
        estimate = _load_estimate(issue_state.number)
        if not estimate:
            continue
        for f in estimate.get("files_likely_affected", []):
            file_to_issues.setdefault(f, []).append(issue_state.number)

    for f, nums in file_to_issues.items():
        if len(nums) > 1:
            issues_str = " and ".join(f"#{n}" for n in nums)
            structured_log.warn("estimate_file_conflict", f"tickets {issues_str} share files: {f}", file_path=f, issue_nums=nums)


def list_backlog_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return open, dispatchable issues for ``label``, sorted by number."""
    result = []
    for issue in _list_labeled_open_issues(label, repo_name=repo_name):
        labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
        if _is_dispatchable(labels_set):
            result.append(issue)
    return result


def _run_pipeline_dispatch(
    *,
    state, state_path, summary,
    dispatch_levels, level_nums_by_idx,
    label, sprint_num, eff_repo, api_url,
    target_branch, sprint_branch, alert_modes, cfg, run_id,
    eff_sprints_dir, rerun_decisions,
    skip_gates, gate_pytest, gate_lint, gate_merge_preview,
    gate_typecheck, gate_design, gate_frontend_lint, gate_monolith, gate_scope,
    resume, retry_failed,
) -> None:
    """Process dispatch levels with one coder + one tester worker concurrently.

    Each level is drained fully before the next begins (hard level barrier).
    Per-ticket side effects mirror the serial loop; the retry/requeue and
    needs-rework-after-cap semantics are owned by ``pipeline.run_level``.
    """
    by_num = {iss.number: iss for iss in state.issues}
    pctx: dict[int, dict] = {}

    def _finalize_skip(num, ist, reason, category, *, tag, gate=False):
        ist.set_agent_status("failed")
        ist.failure_reason = reason
        ist.status = "skipped"
        ist.skip_reason = reason
        ist.category = category
        if gate and "gate failed" in (reason or ""):
            summary.gate_failures.append(reason)
        else:
            summary.skipped.append(f"#{num} ({tag})")
        dispatch_alerts(
            alert_modes,
            title=f"Issue #{num} skipped: {category}",
            body=reason,
            issue_num=num,
            category=category,
            cfg=cfg,
            repo=eff_repo,
        )
        _tag_l = (tag or "").lower()
        _failed_role = (
            "tester" if gate or "tester" in _tag_l
            else "coder" if "coder" in _tag_l
            else "tester" if "tester" in (ist.agent_status or "")
            else "coder" if "coder" in (ist.agent_status or "")
            else "agent"
        )
        _emit_ticket_failed(
            num, _failed_role, reason, category,
            project=eff_repo or label, action_id=run_id, cfg=cfg,
            gate=gate, sprint_label=label,
        )
        _neon_ticket_status(label, num, "failed", eff_sprints_dir)
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url, project=eff_repo)

    def _coder_stage(num, attempt):
        ist = by_num[num]
        _wait_if_paused(sprint_num, state, api_url=api_url)

        # Estimate logging — mirrors serial ordering (issue #737 AC9).
        _est = _load_estimate(num)
        if _est:
            sys.stdout.write(str(f"  [estimate] size={_est.get('size', '?')}, confidence={_est.get('confidence', '?')}") + "\n")

        # Per-ticket port detection (issue #62).
        chosen_port = None
        if cfg is not None and cfg.app_default_port is not None:
            chosen_port = _detect_port(cfg)
            if chosen_port is not None:
                _write_runtime_port(cfg.worktree_coder, chosen_port)
        ctx = pctx.setdefault(num, {"fix_history": []})
        ctx["port"] = chosen_port
        skip_coder = rerun_decisions.get(num) == "dispatch_tester"
        ctx["skip_coder"] = skip_coder

        ist.set_agent_status("queued")
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)

        if skip_coder:
            sys.stdout.write(str(f"  [rerun] SIT ticket: dispatching tester directly for #{num}") + "\n")
            return _StageResult.PASS

        ist.set_agent_status("coder_dispatched")
        ist.coder_started_at = ist.status_changed_at
        # Pre-compute model + routing_reason (issue #789) so agent_runs captures
        # the selection at dispatch time, mirroring the tester risk-tier pattern.
        _coder_model_sel, _coder_route_reason = _resolve_coder_model(num, cfg, estimate=_est)
        ist.coder_model = _coder_model_sel  # surface size-routed model on the live running pane (bug: coder badge had no model)
        ist.coder_routing_reason = _coder_route_reason  # tooltip/sub-label on running pane badge (issue #1403)
        ist.coder_backend = _effective_coder_backend(label, cfg, ctx["fix_history"] if ctx["fix_history"] else None)
        # Determine attempt_kind for this dispatch (issue #787).
        _pipe_attempt_kind = ctx.get("attempt_kind", "initial")
        _db_agent_start_sm(
            num, label, "coder",
            model_used=_coder_model_sel, routing_reason=_coder_route_reason,
            attempt_kind=_pipe_attempt_kind,
            log_path=str(_issue_log_path(num, cfg=cfg)),
        )  # issue #764, #789, #787, #783
        _emit_sprint_lifecycle_event(
            type="ticket_dispatched", target=f"#{num}", actor="system",
            detail={"agent": "CODER"}, project=eff_repo or label, action_id=run_id,
        )
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)
        _transition_safe(num, _TicketState.IN_PROGRESS, actor="sprint_manager", repo_name=eff_repo)

        def _on_coder_running(_ist=ist):
            _ist.set_agent_status("coder_running")
            _neon_ticket_status(label, num, "running", eff_sprints_dir)
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)

        _stage_coder_t0 = time.monotonic()
        _stage_coder_utc0 = _token_window_utc_now()
        _pipe_hang_continuation = ctx.get("hang_continuation")
        _pipe_pool_slot = _pool_acquire()
        try:
            coder_ok, coder_category = _dispatch_coder(
                num, alert_modes, sprint_branch=sprint_branch, repo_name=eff_repo, cfg=cfg,
                chosen_port=chosen_port, rate_limit_events=state.rate_limit_events,
                on_running=_on_coder_running, sprint_label=label,
                prior_failures=ctx["fix_history"] if ctx["fix_history"] else None,
                hang_continuation=_pipe_hang_continuation,
                attempt_kind=_pipe_attempt_kind,
                worktree_override=_pipe_pool_slot,
            )
        finally:
            _pool_release(_pipe_pool_slot)
        _ctin, _ctout = _token_window_sums("coder", _stage_coder_utc0)
        state.total_tokens_in += _ctin
        state.total_tokens_out += _ctout
        _db_agent_finish_sm(  # issue #764: one closed row per coder dispatch
            num, label, "coder",
            duration_seconds=time.monotonic() - _stage_coder_t0,
            outcome="success" if coder_ok else "failed",
            total_tokens=(_ctin + _ctout) or None,
        )
        # Clear hang_continuation after dispatch so fix_round dispatches don't inherit it.
        ctx.pop("hang_continuation", None)
        ctx["attempt_kind"] = "fix_round"

        if not coder_ok:
            category = coder_category or FailureCategory.CRASH
            reason = (
                "Subscription rate limit exhausted"
                if category == FailureCategory.RETRY_EXHAUSTED
                else f"Coder failed with category {category}"
            )
            ist.coder_finished_at = ist.status_changed_at
            if category in _LOGIC_FAILURE_CATEGORIES:
                record_failure(num, category, detail=_build_crash_detail(_issue_log_path(num, cfg=cfg)))

            # Hang-redispatch path (issue #787): on first hang, redispatch once inline.
            if category == FailureCategory.HANG:
                _hang_redispatch_enabled = os.environ.get("COMMANDER_HANG_REDISPATCH", "1") != "0"
                _pipe_hang_count = ctx.get("hang_redispatch_count", 0)
                if _hang_redispatch_enabled and _pipe_hang_count == 0:
                    ctx["hang_redispatch_count"] = 1
                    _pipe_hc_log_tail: list[str] = []
                    try:
                        _pipe_sc = REPO_ROOT / ".commander" / "runtime" / f"last-failure-{num}.json"
                        if cfg:
                            _pipe_sc = cfg.worktree_coder.parent / ".commander" / "runtime" / f"last-failure-{num}.json"
                        if _pipe_sc.exists():
                            _pipe_sc_data = json.loads(_pipe_sc.read_text(encoding="utf-8"))
                            _pipe_hc_log_tail = _pipe_sc_data.get("log_tail", [])
                    except Exception:
                        pass
                    ctx["hang_continuation"] = {
                        "timestamp": _utcnow(),
                        "log_tail": _pipe_hc_log_tail,
                    }
                    ctx["attempt_kind"] = "hang_continue"
                    dispatch_alerts(
                        alert_modes,
                        title=f"Issue #{num}: hang-redispatch",
                        body=(
                            "Coder hung and was idle-killed; redispatching once "
                            "with continuation context (attempt_kind=hang_continue)."
                        ),
                        issue_num=num,
                        category="hang-redispatch",
                        cfg=cfg,
                        repo=eff_repo,
                    )
                    sys.stdout.write(str(f"  [hang-redispatch] #{num} (pipeline): first hang, scheduling "
                        f"hang_continue redispatch (log_tail lines: {len(_pipe_hc_log_tail)})") + "\n")
                    sys.stdout.flush()
                    # Re-dispatch inline within this coder stage call.
                    _stage_hc_t0 = time.monotonic()
                    _db_agent_start_sm(
                        num, label, "coder",
                        model_used=_coder_model_sel, routing_reason=_coder_route_reason,
                        attempt_kind="hang_continue",
                        log_path=str(_issue_log_path(num, cfg=cfg)),
                    )
                    _hc_pool_slot = _pool_acquire()
                    try:
                        coder_ok, coder_category = _dispatch_coder(
                            num, alert_modes, sprint_branch=sprint_branch, repo_name=eff_repo, cfg=cfg,
                            chosen_port=chosen_port, rate_limit_events=state.rate_limit_events,
                            on_running=_on_coder_running, sprint_label=label,
                            prior_failures=ctx["fix_history"] if ctx["fix_history"] else None,
                            hang_continuation=ctx["hang_continuation"],
                            attempt_kind="hang_continue",
                            worktree_override=_hc_pool_slot,
                        )
                    finally:
                        _pool_release(_hc_pool_slot)
                    _db_agent_finish_sm(
                        num, label, "coder",
                        duration_seconds=time.monotonic() - _stage_hc_t0,
                        outcome="success" if coder_ok else "failed",
                    )
                    ctx.pop("hang_continuation", None)
                    ctx["attempt_kind"] = "fix_round"
                    if coder_ok:
                        ist.set_agent_status("coder_done")
                        ist.coder_finished_at = ist.status_changed_at
                        _emit_sprint_lifecycle_event(
                            type="ticket_agent_finished", target=f"#{num}", actor="system",
                            detail={"agent": "CODER"}, project=eff_repo or label, action_id=run_id,
                        )
                        state.save(state_path)
                        _post_sprint_status(state, api_url=api_url)
                        return _StageResult.PASS
                    # hang_continue also failed — fall through to _finalize_skip
                    category = coder_category or FailureCategory.CRASH
                    reason = (
                        "Subscription rate limit exhausted"
                        if category == FailureCategory.RETRY_EXHAUSTED
                        else f"Coder failed with category {category} (after hang-redispatch)"
                    )
                    ist.coder_finished_at = ist.status_changed_at

            _finalize_skip(num, ist, reason, category, tag="coder failed")
            return _StageResult.FAIL

        if _find_feature_branch(num) is None:
            category = FailureCategory.CODER_NO_WORK
            reason = f"Coder exited 0 but no feature/{num}-* branch was created"
            sys.stdout.write(str(f"  {reason}") + "\n")
            record_failure(num, category, detail=reason)
            _finalize_skip(num, ist, reason, category, tag="coder no-work")
            return _StageResult.FAIL

        ist.set_agent_status("coder_done")
        ist.coder_finished_at = ist.status_changed_at
        _emit_sprint_lifecycle_event(
            type="ticket_agent_finished", target=f"#{num}", actor="system",
            detail={"agent": "CODER"}, project=eff_repo or label, action_id=run_id,
        )
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)
        return _StageResult.PASS

    def _tester_stage(num, attempt):
        ist = by_num[num]
        ctx = pctx.setdefault(num, {"fix_history": [], "skip_coder": False})
        _transition_safe(num, _TicketState.SIT, actor="sprint_manager", repo_name=eff_repo)

        ist.set_agent_status("tester_dispatched")
        ist.tester_started_at = ist.status_changed_at
        _db_agent_start_sm(num, label, "tester",
                           log_path=str(_issue_log_path(num, cfg=cfg)))  # issue #764, #783
        ist.tester_attempt_count += 1
        _emit_sprint_lifecycle_event(
            type="ticket_dispatched", target=f"#{num}", actor="system",
            detail={"agent": "TESTER"}, project=eff_repo or label, action_id=run_id,
        )
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)

        def _on_tester_running(_ist=ist):
            _ist.set_agent_status("tester_running")
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)

        _stage_tester_t0 = time.monotonic()
        _stage_tester_utc0 = _token_window_utc_now()
        tester_rc, hang_category = _dispatch_tester(
            num, alert_modes, sprint_branch=sprint_branch, repo_name=eff_repo, cfg=cfg,
            chosen_port=ctx.get("port"), rate_limit_events=state.rate_limit_events,
            on_running=_on_tester_running, sprint_label=label,
            prior_failures=ctx["fix_history"] if ctx["fix_history"] else None,
        )
        _ttin, _ttout = _token_window_sums("tester", _stage_tester_utc0)
        state.total_tokens_in += _ttin
        state.total_tokens_out += _ttout
        _db_agent_finish_sm(  # issue #764: one closed row per tester dispatch
            num, label, "tester",
            duration_seconds=time.monotonic() - _stage_tester_t0,
            outcome="pass" if tester_rc == 0 else "fail",
            total_tokens=(_ttin + _ttout) or None,
        )
        ist.tester_finished_at = ist.status_changed_at
        if hang_category == FailureCategory.HANG:
            _finalize_skip(num, ist, "Tester HANG detected", FailureCategory.HANG, tag="tester hang")
            return _StageResult.FAIL
        if hang_category == FailureCategory.RETRY_EXHAUSTED:
            _finalize_skip(num, ist, "Subscription rate limit exhausted",
                           FailureCategory.RETRY_EXHAUSTED, tag="rate limit exhausted")
            return _StageResult.FAIL

        ist.set_agent_status("tester_done")
        _emit_sprint_lifecycle_event(
            type="ticket_agent_finished", target=f"#{num}", actor="system",
            detail={"agent": "TESTER"}, project=eff_repo or label, action_id=run_id,
        )
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)

        merged, summary_line, gate_category = handle_post_tester(
            issue_num=num, tester_exit_code=tester_rc, skip_gates=skip_gates,
            gate_pytest=gate_pytest, gate_lint=gate_lint, gate_merge_preview=gate_merge_preview,
            gate_typecheck=gate_typecheck, gate_design=gate_design,
            gate_frontend_lint=gate_frontend_lint, gate_monolith=gate_monolith,
            target_branch=target_branch,
            repo_name=eff_repo, cfg=cfg, base_branch=target_branch or "develop",
            gate_scope=gate_scope, documentor_enabled=cfg.documentor_enabled if cfg else False,
            alert_modes=alert_modes, sprint_label=label,
        )
        sys.stdout.write(str(f"  {summary_line}") + "\n")
        ctx["summary_line"] = summary_line
        if merged:
            return _StageResult.PASS

        category = gate_category or FailureCategory.CRASH
        ctx["category"] = category
        if not ctx.get("skip_coder") and category in _LOGIC_FAILURE_CATEGORIES:
            record_failure(num, category, detail=summary_line)
            ctx["fix_history"].append({"attempt": attempt, "category": category, "summary": summary_line})
            _sig = f"{category}:{summary_line[:80]}"
            _last = ctx.get("last_failure_sig")
            ctx["last_failure_sig"] = _sig
            if _sig == _last:
                sys.stdout.write(
                    f"  [pipeline] consecutive identical failure ({category}): aborting early\n"
                )
                sys.stdout.flush()
                try:
                    structured_log.error(
                        "fix_loop_exhausted",
                        f"consecutive identical gate failure ({category}): aborting early",
                        issue_num=num,
                        fix_history=ctx["fix_history"],
                        reason="consecutive_identical",
                        failure_sig=_sig,
                        failure_class=str(category),
                    )
                except Exception:
                    pass
                return _StageResult.EXHAUST
            return _StageResult.REJECT  # scheduler re-queues to front of coder queue

        # Non-logic gate failure or rerun-tester-direct path: terminal drop.
        if category in _LOGIC_FAILURE_CATEGORIES:
            _transition_safe(num, _TicketState.NEEDS_REWORK, actor="sprint_manager", repo_name=eff_repo)
        _finalize_skip(num, ist, summary_line, category, tag=str(category), gate=True)
        return _StageResult.FAIL

    def _on_merged(num):
        ist = by_num[num]
        ist.set_agent_status("completed")
        ist.status = "done"
        summary.merged.append(f"#{num}")
        _neon_ticket_status(label, num, "done", eff_sprints_dir,
                            total_tokens=ist.tokens_in + ist.tokens_out)
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url, project=eff_repo)

    def _on_needs_rework(num):
        ist = by_num[num]
        ctx = pctx.get(num, {})
        history = ctx.get("fix_history", [])
        reason = f"Fix-loop exhausted after {len(history)} attempt(s)"
        sys.stdout.write(str(f"  [pipeline] {reason} — tagging needs-rework") + "\n")
        sys.stdout.flush()
        ist.set_agent_status("failed")
        ist.failure_reason = reason
        ist.status = "skipped"
        ist.skip_reason = reason
        ist.category = FailureCategory.RETRY_EXHAUSTED
        summary.skipped.append(f"#{num} (fix-loop exhausted)")
        dispatch_alerts(
            alert_modes, title=f"Issue #{num} skipped: needs-rework", body=reason,
            issue_num=num, category=FailureCategory.RETRY_EXHAUSTED, cfg=cfg, repo=eff_repo,
            sprint_label=label,
        )
        _emit_ticket_failed(
            num, "coder", reason, FailureCategory.RETRY_EXHAUSTED,
            project=eff_repo or label, action_id=run_id, cfg=cfg, sprint_label=label,
        )
        _transition_safe(num, _TicketState.NEEDS_REWORK, actor="sprint_manager", repo_name=eff_repo)
        _publish_gate_failure_analyses(num, repo_name=eff_repo, cfg=cfg)
        _neon_ticket_status(label, num, "failed", eff_sprints_dir)
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url, project=eff_repo)

    prev_level_idx = -1
    for lvl_idx, lvl in enumerate(dispatch_levels):
        # Filter resume/retry skips, mirroring the serial loop.
        level_nums = []
        for iss in lvl:
            if resume and iss.status in ("done", "skipped"):
                summary.processed.append(f"#{iss.number}")
                if iss.status == "done":
                    summary.merged.append(f"#{iss.number}")
                else:
                    summary.skipped.append(f"#{iss.number} ({iss.skip_reason or 'skipped'})")
                continue
            if retry_failed and iss.status == "done":
                summary.processed.append(f"#{iss.number}")
                summary.merged.append(f"#{iss.number}")
                continue
            summary.processed.append(f"#{iss.number}")
            level_nums.append(iss.number)

        if prev_level_idx >= 0:
            try:
                structured_log.event(
                    "level_complete", run_id=run_id, issue_num=None, sprint_label=label,
                    agent_role="sprint", level_index=prev_level_idx,
                    total=len(level_nums_by_idx[prev_level_idx]),
                )
            except Exception:
                pass
        prev_level_idx = lvl_idx
        try:
            structured_log.event(
                "level_start", run_id=run_id, issue_num=None, sprint_label=label,
                agent_role="sprint", level_index=lvl_idx, tickets=level_nums_by_idx[lvl_idx],
            )
        except Exception:
            pass
        sys.stdout.write(str(f"\n=== Dispatch level {lvl_idx} (pipeline): tickets {level_nums} ===") + "\n")

        if not level_nums:
            continue

        # Use resolved slot count from state (set at run start, issue #1415).
        _max_coder_slots = state.max_coder_slots

        if _max_coder_slots > 1:
            # Build file_map for conflict-aware concurrent dispatch.
            _file_map: dict = {}
            for _fnum in level_nums:
                _est = _load_estimate(_fnum)
                if _est:
                    _files = (
                        _est.get("files_touched")
                        or _est.get("files_likely_affected")
                        or []
                    )
                    _file_map[_fnum] = set(_files)

            def _on_slot_change(_count: int) -> None:
                state.active_coder_slots = _count
                state.save(state_path)
                _post_sprint_status(state, api_url=api_url, project=eff_repo)

            from services.sprint_manager.concurrent_scheduler import (  # noqa: PLC0415
                run_concurrent_level as _run_concurrent_level,
            )
            _run_concurrent_level(
                level_nums, _coder_stage, _tester_stage,
                max_coder_slots=_max_coder_slots,
                file_map=_file_map,
                on_merged=_on_merged, on_needs_rework=_on_needs_rework,
                on_active_slots_change=_on_slot_change,
            )
        else:
            # Hard level barrier: run_level does not return until the level drains.
            _run_pipeline_level(
                level_nums, _coder_stage, _tester_stage, pipeline=True,
                on_merged=_on_merged, on_needs_rework=_on_needs_rework,
            )

    if prev_level_idx >= 0:
        try:
            structured_log.event(
                "level_complete", run_id=run_id, issue_num=None, sprint_label=label,
                agent_role="sprint", level_index=prev_level_idx,
                total=len(level_nums_by_idx[prev_level_idx]),
            )
        except Exception:
            pass
