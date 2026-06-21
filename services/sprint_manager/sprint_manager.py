#!/usr/bin/env python3
"""Sprint Manager — orchestrates coder and tester agents for sprint issues,
with a post-tester quality gate pipeline before auto-merging to develop.

Quality gates (pytest → lint → merge-preview) run after a tester subprocess
exits 0 and the issue has advanced to the UAT label. Any gate failure reverts
the issue to SIT with a detailed comment.

After sprint completion a rich executive summary is written to
~/commander/apps/dashboard/sprints/sprint-<N>-summary-<YYYY-MM-DD>.md, a GitHub
issue is created for permanent record, and an optional interactive learnings
prompt is shown when stdout is a TTY.

Adds per-failure categorisation, hang detection, configurable alert channels,
a sprint summary report, restart/resume from state, and live dashboard progress.

Usage:
    python3 ~/commander/services/sprint_manager/sprint_manager.py <label> [options]

Examples:
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py sprint-5
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py sprint-5 --skip-gates
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py sprint-5 --gate-pytest=false
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py \
        sprint-5 --alert-mode dashboard-banner,file
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py sprint-5 --resume
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py sprint-5 --retry-failed
    python3 ~/cmd/svc/sprint_mgr/sprint_mgr.py sprint-5 --dry-run

Run from the git root of the repository.
"""
from __future__ import annotations

import argparse
import atexit
import dataclasses
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import traceback
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repo root is in sys.path early so `from services.*` imports work
# regardless of how the script is invoked (direct path, cwd, etc.)
_early_repo_root = str(Path(__file__).parent.parent.parent)
if _early_repo_root not in sys.path:
    sys.path.insert(0, _early_repo_root)

try:
    import yaml  # PyYAML — already in requirements.txt
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

from services.sprint_manager.pipeline import (  # noqa: E402
    pipeline_mode_enabled as _pipeline_mode_enabled,
    _run_pipeline_dispatch,
    _compute_dispatch_levels,
    _build_sprint_dag_layers,
    _warn_file_conflicts,
    list_backlog_issues,
)

from services.sprint_manager.config import (  # noqa: E402
    SprintConfig,
    load_config,
    discover_config,
    _default_config,
)

from services.sprint_manager.worktree_pool import (  # noqa: E402
    WorktreePool as _WorktreePool,
)

from services.sprint_manager.commander_paths import (  # noqa: E402
    discover_commander_dir,
)

from services.sprint_manager.paths import (  # noqa: E402
    _plan_json_path,
    _state_path,
    _sprint_number,
    _is_child_sprint_label,
    _sprint_branch_for_label,
    _base_sprint_branch,
)

# issue #738: cross-thread serialization for concurrent pipeline mode — one
# develop merge and one status-label write at a time, plus ghost reconciliation.
from services.sprint_manager.serialization import (  # noqa: E402
    develop_merge_guard as _develop_merge_guard,
    ghost_status_labels as _ghost_status_labels,  # noqa: F401
)

from services.sprint_manager.model_routing import (  # noqa: E402
    _effective_coder_backend,
    _is_docs_only,  # noqa: F401  re-exported for backward compat
    _resolve_coder_model,
    _select_coder_backend,
)

try:
    from services.sprint_manager.state_machine import (  # noqa: PLC0415
        transition as _sm_transition,
        TicketState as _TicketState,
        TransitionError as _TransitionError,
        STATE_LABELS as _STATE_LABELS,
        STATUS_LABELS as _STATUS_LABELS,
    )
    _STATE_MACHINE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    _sm_transition = None  # type: ignore[assignment]
    _TicketState = None  # type: ignore[assignment]
    _TransitionError = Exception  # type: ignore[assignment,misc]
    _STATE_LABELS = {}  # type: ignore[assignment]
    _STATUS_LABELS = frozenset()  # type: ignore[assignment]
    _STATE_MACHINE_AVAILABLE = False

try:
    from services.sprint_manager.dag_builder import (  # noqa: PLC0415
        build_dag as _dag_build,
        DAGResult as _DAGResult,
        CycleError as _DAGCycleError,
    )
    _DAG_BUILDER_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    _dag_build = None  # type: ignore[assignment]
    _DAGResult = None  # type: ignore[assignment]
    _DAGCycleError = None  # type: ignore[assignment]
    _DAG_BUILDER_AVAILABLE = False

# ── path setup ────────────────────────────────────────────────────────────────

# This file lives at services/sprint_manager/sprint_manager.py
# Repo root is three levels up: sprint_manager/ → services/ → repo_root
REPO_ROOT     = Path(__file__).parent.parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPTS_DIR   = REPO_ROOT / "scripts"


def _git_worktree_root(start: "Path") -> "Path | None":
    """Return the git toplevel for ``start``, or None if not inside a worktree."""
    d = start.resolve()
    for _ in range(25):
        if (d / ".git").exists():
            return d
        if d.parent == d:
            break
        d = d.parent
    return None


def _parse_dotenv_value(text: str, key: str) -> Optional[str]:
    """Return the value for ``key=`` from a dotenv file body, or None."""
    prefix = f"{key}="
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        val = line[len(prefix):].strip().strip('"').strip("'")
        return val or None
    return None

sys.path.insert(0, str(REPO_ROOT))      # allow `from services.*` imports
sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(DASHBOARD_DIR / ".env")
import github_client  # noqa: E402

from services.run_id import mint_run_id  # noqa: E402
from services.logging import log as structured_log  # noqa: E402
from services.logging import install_orchestrator_stdout_timestamps  # noqa: E402
# issue #710: live-browser UAT
from services.sprint_manager import agent_browser_runner  # noqa: E402
from services.sprint_manager.state import (  # noqa: E402
    IssueState,
    SprintState,
    GateResult,
    SprintSummary,
)

try:
    # issue #860
    from services.sprint_manager.brief_generator import (
        write_sprint_brief as _write_sprint_brief,
    )
    _BRIEF_GENERATOR_AVAILABLE = True
except ImportError:  # pragma: no cover
    _write_sprint_brief = None  # type: ignore[assignment]
    _BRIEF_GENERATOR_AVAILABLE = False

try:
    from services.sprint_manager import suite_health_gate as _suite_health_gate
    _SUITE_HEALTH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _suite_health_gate = None  # type: ignore[assignment]
    _SUITE_HEALTH_AVAILABLE = False

try:
    from db import record_event as _db_record_event  # type: ignore[import]
    _RECORD_EVENT_AVAILABLE = True
except ImportError:
    _db_record_event = None  # type: ignore[assignment]
    _RECORD_EVENT_AVAILABLE = False

# Event-emission helpers extracted to events.py (issue #1275); re-exported here
# so all existing call sites within this module remain unmodified.
from services.sprint_manager.events import (  # noqa: E402
    _emit_sprint_lifecycle_event,
    _emit_ticket_failed,
    _post_agent_event,
    _post_sprint_status,
)

# Timekeeping helpers extracted to timekeeping.py (issue #1277); re-exported here
# so all existing call sites within this module remain unmodified.
from services.sprint_manager.timekeeping import (  # noqa: E402
    _token_window_utc_now,
    _token_window_sums,
    _utcnow,
    _BANGKOK_TZ,  # noqa: F401
    _bangkok_now,  # noqa: F401
    _to_bangkok,  # noqa: F401
    _setup_pid_file,
    _wait_if_paused,
    _acquire_pid_lock,
    _release_pid_lock,
)

# Gate functions extracted to gates.py (issues #1280, #1281); re-exported here
# so all existing call sites within this module remain unmodified.
from services.sprint_manager.gates import (  # noqa: E402, F401
    _gate_pytest,
    _lint_autofix_commit,
    _gate_lint,
    _run_frontend_lint,
    _changed_py_files,
    _changed_js_ts_files,
    _changed_frontend_files,
    _JS_TS_EXTENSIONS,
    _JS_TS_LINT_EXCLUDE,
    _DESIGN_FE_EXTENSIONS,
    # issue #1281: additional gate functions
    _gate_typecheck,
    _gate_design,
    _gate_merge_preview,
    _gate_monolith,
    _run_quality_gates,
    _log_gate_result,
    MONOLITH_GUARDED_FILE,
    _file_line_count_at_ref,
    _MERGE_PREVIEW_TMP_BRANCH,
    _impeccable_findings,
    _finding_sig,
    _net_new_findings,
    # subprocess helpers and _revert_to_sit proxy — exported from gates so that
    # tests patching "sprint_manager._run_timed" etc. work correctly regardless
    # of which import path was used (flat vs package).
    _run_timed,
    _try,
    _revert_to_sit,
)

# Label transition helpers extracted to label_transitions.py (issue #1282);
# re-exported here so all existing call sites within this module remain unmodified.
from services.sprint_manager.label_transitions import (  # noqa: E402, F401
    _get_issue_labels,
    _sweep_stale_status,
    _current_status_labels,
    _emit_label_transition_event,
    _transition_safe,
    _add_blocked_label,
)

# Worktree/env helpers extracted to worktree.py (issue #1283); re-exported here
# so all existing call sites within this module remain unmodified.
from services.sprint_manager.worktree import (  # noqa: E402, F401
    _resolve_uat_env_for_tester,
    _worktree_hygiene,
    _crg_update_worktree,
    _stash_to_quarantine,
    _detect_port,
)

# Coder dispatch helpers extracted to dispatch.py (issue #1285); re-exported here
# so all existing call sites within this module remain unmodified.
from services.sprint_manager.dispatch import (  # noqa: E402, F401
    _dispatch_coder,
    _load_agent_persona,
    _agent_identity_env,
)

# Summary generation helpers extracted to summary.py (issue #1287); re-exported
# here so all existing call sites within this module remain unmodified.
from services.sprint_manager.summary import (  # noqa: E402, F401
    LEARNINGS_STUB,
    generate_sprint_summary,
    write_sprint_summary,
    create_summary_github_issue,
    _prompt_learnings,
    _is_stale_summary,
    _load_screenshot_url_map,
    _follow_up_action,
    _build_screenshots_section,
)

# Post-sprint agent helpers extracted to post_sprint.py (issue #1288); re-exported
# here so all existing call sites within this module remain unmodified.
from services.sprint_manager.post_sprint import (  # noqa: E402, F401
    DEFAULT_REVIEWER_PROMPT,
    DEFAULT_DOCUMENTER_PROMPT,
    _extract_follow_up_issue_nums,
    _create_sprint_pr,
    _dispatch_documenter,
    _dispatch_reviewer,
    _dispatch_ba_for_followup,
    _dispatch_estimator_for_followup,
    _enrich_followup_tickets,
    _BA_REWRITE_PROMPT,
    _BA_DISPATCH_TIMEOUT,
    _ESTIMATOR_DISPATCH_TIMEOUT,
    _ESTIMATE_ISSUE_SCRIPT_SM,
)

# Import failure-parsing helpers from post_test_report (no circular deps)
try:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from post_test_report import (  # type: ignore[import]
        parse_failures,
        build_failure_block,
        write_sidecar,   # noqa: F401
        sidecar_path,    # noqa: F401
    )
    _FAILURE_PARSING_AVAILABLE = True
except ImportError:
    _FAILURE_PARSING_AVAILABLE = False

# Default paths — can be overridden via env vars or CLI for testing
WORKTESTER_ROOT      = Path(os.environ.get("WORKTESTER_ROOT",
                             Path.home() / "dev" / "commander" / "tester"))
WORKTESTER_DASHBOARD = WORKTESTER_ROOT / "apps" / "dashboard"
FINISH_FEATURE_SCRIPT = SCRIPTS_DIR / "finish_feature.py"
DASHBOARD_API_URL    = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")
SPRINTS_DIR          = DASHBOARD_DIR / "sprints"
ALERTS_DIR           = DASHBOARD_DIR / "alerts"

# ── API cost pricing (USD per million tokens) ─────────────────────────────────
# All agents (coder, tester, preflight) run via Claude Code CLI which is
# subscription-funded — no raw API charges.
# Rates kept for reference only; not used in cost_estimate.
_HAIKU_INPUT_COST_PER_M  = 0.80   # claude-haiku-4-5-20251001 input (reference)
_HAIKU_OUTPUT_COST_PER_M = 4.00   # claude-haiku-4-5-20251001 output (reference)

# Doctor auth probe cache — at most one real probe per TTL seconds (issue #789).
_DOCTOR_AUTH_LAST_PROBE: float = 0.0
_DOCTOR_CLINE_AUTH_LAST_PROBE: float = 0.0
_DOCTOR_AUTH_PROBE_TTL: float = 5 * 60  # 5 minutes
DOCTOR_MIN_DISK_BYTES: int = 1 * 1024 * 1024 * 1024  # 1 GB minimum free space

# Set by _sigterm_handler when the user cancels the sprint (issue #365, #514).
# Checked in write_sprint_summary and in main()'s SystemExit handler.
# threading.Event for thread-safe signaling across worker threads.
_sprint_user_cancelled: threading.Event = threading.Event()

# Calibration cache: if incremental refresh completes within this budget it runs
# inline (no perceived delay); otherwise dispatched as a background thread (issue #1333).
_CALIBRATION_INLINE_THRESHOLD_S: float = 0.5


def _run_calibration_cache_refresh(
    project_root: Path,
    configured_minutes: dict,
    project: str = "",
) -> None:
    """Refresh the calibration cache after a sprint finishes (issue #1333).

    Runs _refresh_calibration_cache incrementally. If it completes within
    _CALIBRATION_INLINE_THRESHOLD_S it runs inline; otherwise it continues in a
    daemon background thread so the sprint-finish UX is not blocked.

    Emits a calibration_cache_updated event when new samples are absorbed.
    """
    try:
        from calibration_cache_service import _refresh_calibration_cache as _rcr  # noqa: PLC0415
    except ImportError:
        return

    commander = project_root / ".commander"
    cache_path = commander / "calibration_cache.json"
    try:
        prev_processed = len(
            json.loads(cache_path.read_text(encoding="utf-8")).get("processed") or []
        ) if cache_path.is_file() else 0
    except Exception:
        prev_processed = 0

    result_holder: list = [None]

    def _work() -> None:
        try:
            result_holder[0] = _rcr(project_root, configured_minutes)
        except Exception:
            pass

    t = threading.Thread(target=_work, daemon=True, name="calibration-refresh")
    t.start()
    t.join(timeout=_CALIBRATION_INLINE_THRESHOLD_S)

    if t.is_alive():
        def _bg_log() -> None:
            t.join()
            cache = result_holder[0]
            if cache is not None:
                _emit_calibration_updated_event(cache, prev_processed, project)
        threading.Thread(target=_bg_log, daemon=True, name="calibration-refresh-log").start()
        return

    cache = result_holder[0]
    if cache is not None:
        _emit_calibration_updated_event(cache, prev_processed, project)


def _emit_calibration_updated_event(cache: dict, prev_processed: int, project: str) -> None:
    """Emit calibration_cache_updated event when new samples were absorbed."""
    processed_after = len(cache.get("processed") or [])
    new_samples = processed_after - prev_processed
    if new_samples <= 0:
        return
    by_size = cache.get("by_size") or {}
    total_samples = sum(
        int(by_size[sz]["count"] or 0)
        for sz in ("S", "M", "L", "XL")
        if sz in by_size
    )
    _emit_sprint_lifecycle_event(
        type="calibration_cache_updated",
        target="calibration_cache",
        actor="sprint_manager:calibration",
        detail={"new_samples": new_samples, "total_samples": total_samples},
        project=project,
    )


# ── Plan.json state helpers (issue #507) ─────────────────────────────────────

def _plan_has_parent(label: str, cfg: Optional["SprintConfig"] = None) -> bool:
    """True when ``label`` is a versioned re-run sub-sprint (plan.json has parent)."""
    path = _plan_json_path(label, cfg)
    try:
        if not path.exists():
            return False
        raw = json.loads(path.read_text(encoding="utf-8"))
        return bool(isinstance(raw, dict) and raw.get("parent"))
    except Exception:
        return False


def _plan_json_set_state_sm(
    label: str,
    state: str,
    cfg: Optional["SprintConfig"] = None,
    **extra_fields,
) -> None:
    """Best-effort update of plan.json state from sprint_manager side.

    Reads existing file (handling both old list format and new dict format),
    merges the new state + extra fields, and writes atomically.  All errors
    are swallowed — this must never interrupt the sprint run.
    """
    path = _plan_json_path(label, cfg)
    try:
        existing: dict = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                existing = raw
            elif isinstance(raw, list):
                existing = {"tickets": raw}
        existing["state"] = state
        existing.update(extra_fields)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception:
        pass


# ── end plan.json helpers ─────────────────────────────────────────────────────

# ── DB lifecycle helpers (issue #757) ────────────────────────────────────────
#
# The `sprints` / `sprint_ticket_order` tables are the durable home for sprint
# lifecycle state and ticket order; plan.json + PID files above are now a
# deprecated cache (dual-write). All DB writes here are best-effort: a missing
# DB_PATH (CLI dispatch) makes apps/dashboard/db.py sys.exit at import, so we
# swallow SystemExit too — a DB write must never interrupt a sprint run.

def _sprint_db_set_state_sm(
    label: str,
    state: str,
    project: str = "",
    **fields,
) -> None:
    """Best-effort mirror of a sprint lifecycle transition into the DB (#757)."""
    try:
        import db  # apps/dashboard on sys.path (line 142)
        if state == "running":
            db.record_sprint_start(label, project=project or "",
                                   started_at=fields.get("started_at"))
        elif state == "completed":
            db.record_sprint_finish(label, ended_at=fields.get("ended_at"),
                                    end_reason=fields.get("end_reason"),
                                    project=project or "")
        elif state == "ready_to_merge":
            db.record_sprint_ready_to_merge(label,
                                            end_reason=fields.get("end_reason"),
                                            ended_at=fields.get("ended_at"),
                                            project=project or "")
        elif state in ("needs_rework", "cancelled", "failed"):
            # cancelled/failed are legacy spellings — all bad endings land in
            # needs_rework under the unified lifecycle (sprint-lifecycle.md).
            db.record_sprint_needs_rework(label,
                                          end_reason=fields.get("end_reason"),
                                          ended_at=fields.get("ended_at"),
                                          project=project or "")
    except (Exception, SystemExit):
        pass


def _sprint_db_set_ticket_order_sm(label: str, issue_numbers: list[int]) -> None:
    """Best-effort persist of the dispatch order into sprint_ticket_order (#757)."""
    try:
        import db  # apps/dashboard on sys.path (line 142)
        db.set_sprint_ticket_order(label, issue_numbers)
    except (Exception, SystemExit):
        pass


def _sprint_db_ingest_run_sm(
    label: str,
    state: "SprintState",
    project: str = "",
    summary_path: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """End-of-run disk → DB ingest (lifecycle P3). Best-effort."""
    try:
        import db  # apps/dashboard on sys.path (line 142)
        sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
        spath = summary_path
        if spath is None:
            from pathlib import Path as _Path
            from routers import sprint_artifact_service  # noqa: PLC0415
            spath = sprint_artifact_service.find_summary_path(_Path(sprints_dir), label)
        db.ingest_sprint_run_artifact(
            label,
            state.to_dict(),
            project=project or state.project or "",
            summary_path=spath,
        )
    except (Exception, SystemExit):
        pass


# ── Per-agent run tracking (issue #764) ──────────────────────────────────────
#
# Each dispatched agent (coder, tester, documenter, reviewer, estimator) opens
# an agent_runs row at start and closes it on finish with its precise wall-clock
# duration. Best-effort, like the lifecycle helpers above: a DB write must never
# interrupt or fail a sprint run.

# Owning project (owner/repo) for the run in progress — set at run_sprint start so
# every agent_runs row is tagged with its project (sprint labels collide across
# repos). Read by _db_agent_start_sm without threading it through 8 call sites.
_CURRENT_RUN_PROJECT: Optional[str] = None

# Active worktree pool for the current sprint run (issue #1411).
# Set by run_sprint at sprint start; cleared at sprint end.
# Provides K isolated worktrees for concurrent coder dispatch.
_ACTIVE_WORKTREE_POOL: Optional[_WorktreePool] = None


def _db_agent_start_sm(
    issue_number,
    sprint_label: str,
    agent: str,
    risk_tier: Optional[str] = None,
    model_used: Optional[str] = None,
    routing_reason: Optional[str] = None,
    worktree_sha: Optional[str] = None,
    base_sha: Optional[str] = None,
    attempt_kind: Optional[str] = None,
    log_path: Optional[str] = None,
    backend: Optional[str] = None,
) -> None:
    """Best-effort open of an agent_runs row at dispatch time (#764).

    `risk_tier` and `model_used` are supplied for tester dispatches that
    use risk-tier routing (issue #790). `routing_reason` is supplied for
    coder dispatches using size-tier routing (issue #789). `worktree_sha` and
    `base_sha` are forensic fields from worktree hygiene (issue #788).
    `attempt_kind` is one of 'initial', 'fix_round', or 'hang_continue' (issue #787).
    `log_path` is the absolute path to the issue log file (issue #783).
    `backend` is 'cline' or 'claude-code' (issue #920).
    """
    try:
        import db  # apps/dashboard on sys.path (line 142)
        db.record_agent_start(
            int(issue_number), sprint_label, agent,
            risk_tier=risk_tier, model_used=model_used,
            routing_reason=routing_reason,
            worktree_sha=worktree_sha,
            base_sha=base_sha,
            attempt_kind=attempt_kind,
            log_path=log_path,
            backend=backend,
            project=_CURRENT_RUN_PROJECT,
        )
    except (Exception, SystemExit):
        pass


def _db_update_worktree_shas_sm(
    issue_number: int,
    sprint_label: str,
    agent: str,
    worktree_sha: Optional[str],
    base_sha: Optional[str],
) -> None:
    """Best-effort UPDATE of the open agent_runs row to record hygiene SHAs (issue #788)."""
    try:
        import db  # apps/dashboard on sys.path
        db.update_worktree_shas(int(issue_number), sprint_label, agent, worktree_sha, base_sha)
    except (Exception, SystemExit):
        pass


def _db_agent_finish_sm(
    issue_number,
    sprint_label: str,
    agent: str,
    duration_seconds=None,
    outcome=None,
    total_tokens=None,
) -> None:
    """Best-effort close of the open agent_runs row on finish (#764).

    `duration_seconds` is the precise monotonic measurement taken by the caller
    so the stored value is within ±2 s of the logged wall-clock time (AC3).
    """
    try:
        import db  # apps/dashboard on sys.path (line 142)
        db.record_agent_finish(
            int(issue_number), sprint_label, agent,
            duration_seconds=None if duration_seconds is None else round(duration_seconds),
            outcome=outcome, total_tokens=total_tokens,
        )
    except (Exception, SystemExit):
        pass


# Hang detection constants (in seconds)
HANG_WARN_SECS  = 30 * 60   # 30 minutes
HANG_KILL_SECS  = 60 * 60   # 60 minutes
HANG_CHECK_SECS = 5  * 60   # check every 5 minutes

# ── Sprint-label protection (issue #305) ─────────────────────────────────────

# Status labels that may be added or removed while a sprint run is active.
# All other label additions are deferred to post-run; sprint-N is never
# removed from a ticket until the sprint run ends.
# Consolidated from old _RUN_MUTABLE_GITHUB_LABELS constant (issue #506, Wave 1 label protection).
RUN_MUTABLE_LABELS: frozenset[str] = frozenset({
    "in-progress", "SIT", "UAT", "needs-rework",
})

_SPRINT_LABEL_RE = re.compile(r"^sprint-\d+$")
_SUMMARY_TITLE_RE = re.compile(r"^Sprint \d+(\.\d+)?\s+Executive Summary$")


def _summary_sprint_display(sprint_label: Optional[str], sprint_number) -> str:
    """Full sprint identifier for summary titles/labels: '60', '60.1', '60.2'.

    Prefer the dotted form from the label — using the bare sprint_number made
    every re-run (sprint-60.1, 60.2 …) reuse the title "Sprint 60 Executive
    Summary", so the dedup check kept updating the parent sprint's summary
    issue instead of filing one per re-run.
    """
    m = re.match(r"^sprint-(\d+(?:\.\d+)*)$", sprint_label or "")
    if m:
        return m.group(1)
    return str(sprint_number) if sprint_number is not None else str(sprint_label)


def _guard_sprint_labels(
    add: list[str],
    remove: list[str],
    sprint_label: Optional[str] = None,
) -> tuple[list[str], list[str]]:
    """Return filtered (add, remove) respecting sprint-label protection.

    Always: sprint-N labels are stripped from *remove* and never deleted.
    Active run (sprint_label provided): *add* is restricted to
    RUN_MUTABLE_LABELS; any other additions are logged and dropped.
    """
    safe_remove = [lbl for lbl in remove if not _SPRINT_LABEL_RE.match(lbl)]
    blocked_removes = [lbl for lbl in remove if _SPRINT_LABEL_RE.match(lbl)]
    if blocked_removes:
        sys.stderr.write(str(f"  [label-guard] Blocked removal of sprint label(s): {blocked_removes}") + "\n")

    if sprint_label is not None:
        safe_add = [lbl for lbl in add if lbl in RUN_MUTABLE_LABELS]
        deferred = [lbl for lbl in add if lbl not in RUN_MUTABLE_LABELS]
        if deferred:
            sys.stderr.write(str(f"  [label-guard] Deferred non-mutable label addition(s) until post-run: {deferred}") + "\n")
    else:
        safe_add = add

    return safe_add, safe_remove


def _assert_run_mutable(labels: list[str], op: str) -> None:
    """Raise ValueError if any label in `labels` is outside RUN_MUTABLE_LABELS.

    Caller must catch ValueError and skip the operation so the sprint loop
    continues without crashing.
    """
    violations = [lbl for lbl in labels if lbl not in RUN_MUTABLE_LABELS]
    for lbl in violations:
        msg = f"Refused to {op} label {lbl!r} during sprint run — outside RUN_MUTABLE_LABELS"
        sys.stderr.write(str(f"  [run-mutable-guard] {msg}") + "\n")
        structured_log.warn("run_mutable_guard", msg, label=lbl, op=op)
    if violations:
        raise ValueError(f"Label mutation blocked: {violations!r} outside RUN_MUTABLE_LABELS")

# ── end sprint-label protection ───────────────────────────────────────────────

# Rate-limit retry constants
_RATE_LIMIT_MAX_RETRIES     = 3
_RATE_LIMIT_BACKOFF_DELAYS  = [30, 60, 120]   # seconds per attempt
_RATE_LIMIT_SIGNALS         = ["429", "rate limit", "too many requests",
                                "subscription rate limit", "rate_limit"]


def _is_rate_limit_error(output: str) -> tuple[bool, Optional[int]]:
    """Return (is_rate_limit, retry_after_secs) by inspecting subprocess output.

    Checks for 429 / rate-limit signals and an optional Retry-After value.
    """
    lower = output.lower()
    if not any(sig in lower for sig in _RATE_LIMIT_SIGNALS):
        return False, None
    m = re.search(r"retry.?after[:\s]+(\d+)", output, re.IGNORECASE)
    retry_after = int(m.group(1)) if m else None
    return True, retry_after


# ── failure categories ────────────────────────────────────────────────────────

class FailureCategory:
    HANG             = "HANG"
    CRASH            = "CRASH"
    GATE_FAIL        = "GATE_FAIL"
    TESTER_REJECTED  = "TESTER_REJECTED"
    RETRY_EXHAUSTED  = "RETRY_EXHAUSTED"
    # Fine-grained logic failure categories (issue #239)
    CODER_NO_WORK    = "CODER_NO_WORK"
    MERGE_CONFLICT   = "MERGE_CONFLICT"
    LINT_FAIL        = "LINT_FAIL"
    PYTEST_FAIL      = "PYTEST_FAIL"
    # Merge sequencing issue — not a code quality problem, no coder requeue (issue #1414)
    REBASE_CONFLICT  = "REBASE_CONFLICT"


# Logic failures signal bad code/spec and warrant needs-rework label.
# Infrastructure failures (CRASH, HANG, RETRY_EXHAUSTED, TESTER_REJECTED) are transient and do not.
# TESTER_REJECTED means tests passed (exit 0) but merge was not detected — a process/infra issue,
# not a code quality problem, so it must not apply needs-rework.
# REBASE_CONFLICT is a merge-sequencing issue (not a code defect), so it is intentionally absent
# from this set — the needs-rework label is applied directly in handle_post_tester (issue #1414).
_LOGIC_FAILURE_CATEGORIES: frozenset[str] = frozenset({
    FailureCategory.CODER_NO_WORK,
    FailureCategory.MERGE_CONFLICT,
    FailureCategory.LINT_FAIL,
    FailureCategory.PYTEST_FAIL,
})


def _extract_rebase_conflict_files(output: str) -> list[str]:
    """Parse 'git rebase' stdout+stderr to extract conflicting file paths (issue #1414).

    Git emits one line per conflict:
        CONFLICT (content): Merge conflict in path/to/file.py
    Returns a deduplicated list in order of first occurrence.
    """
    import re as _re
    seen: dict[str, None] = {}
    for line in output.splitlines():
        m = _re.search(r"CONFLICT \([^)]+\): Merge conflict in (.+)$", line)
        if m:
            seen[m.group(1).strip()] = None
    return list(seen)


# ── alert channels (extracted to alerts.py, issue #1271) ─────────────────────

from services.sprint_manager.alerts import (  # noqa: E402
    AlertMode,
    dispatch_alerts,
    HangDetector,
)

# ── subprocess helpers ────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)
    return r.stdout.strip()


_CRG_UPDATE_TIMEOUT_SECS = 120


def _find_crg_bin(near: Optional[Path] = None) -> Optional[str]:
    """Return path to code-review-graph CLI if installed, else None."""
    candidates: list[Path] = []
    if near is not None:
        candidates.append(near / "venv" / "bin" / "code-review-graph")
        for parent in near.parents:
            if parent.name in ("commander", "dev") or len(candidates) > 6:
                break
            candidates.append(parent / "uat" / "venv" / "bin" / "code-review-graph")
    candidates.append(Path.home() / "dev" / "commander" / "uat" / "venv" / "bin" / "code-review-graph")
    candidates.append(Path.home() / "dev" / "commander" / "prd" / "venv" / "bin" / "code-review-graph")
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    found = shutil.which("code-review-graph")
    return found


def _write_runtime_port(worktree_coder: Path, port: int) -> None:
    """Write chosen port to <coder_worktree>/.commander/runtime/port.

    AC-6: Creates parent dirs as needed.
    """
    runtime_dir = worktree_coder / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    port_file = runtime_dir / "port"
    port_file.write_text(str(port), encoding="utf-8")
    sys.stdout.write(str(f"  [port] wrote {port} to {port_file}") + "\n")


# ── strangler-fig monolith gate (issue #761) ──────────────────────────────────

# MONOLITH_GUARDED_FILE, _file_line_count_at_ref extracted to gates.py (#1281);
# re-imported above.


def _monolith_gate_enabled() -> bool:
    """COMMANDER_GATE_MONOLITH defaults on; 'false'/'0'/'no'/'off' disables it."""
    return os.environ.get("COMMANDER_GATE_MONOLITH", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _r(repo_name: Optional[str]) -> str:
    return repo_name or github_client.repo()


def _sweep_stale_in_progress(
    sprint_label: str,
    repo_name: Optional[str],
    active_issue: Optional[int] = None,
) -> None:
    """Clear stale ``in-progress`` labels — see _sweep_stale_status."""
    _sweep_stale_status("in-progress", sprint_label, repo_name, active_issue)


def _find_feature_branch(issue_num: int) -> Optional[str]:
    """Return feature/<N>-* branch name, preferring origin/ remote over local.

    Prefers the remote tracking ref so we get the current authoritative tip
    (e.g. after a tester's finish_feature.py pushed the final commit) rather
    than a potentially stale local copy.
    """
    ok, out, _ = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")
    ok, out, _ = _try("git", "branch", "--list", f"feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("* ")
    return None


def _is_branch_merged_into(branch: str, target: str, issue_num: Optional[int] = None) -> bool:
    """Return True if branch has been merged into target (local or remote).

    When ``issue_num`` is provided, delegates to the strict
    ``_is_issue_merged_into_target`` check which avoids the ``git branch
    --merged`` false-positive on stale ancestor branches.

    Without ``issue_num``, falls back to the original ``git branch --merged``
    heuristic (kept for call sites that don't have an issue number).
    """
    if issue_num is not None:
        return _is_issue_merged_into_target(issue_num, target, feature_branch=branch)

    # Legacy path: git branch --merged heuristic (may false-positive on stale ancestors)
    target_ref = _git_target_ref(target)

    ok, out, _ = _try("git", "branch", "--merged", target_ref, "--list", branch)
    if ok and out.strip():
        return True

    ok, out, _ = _try("git", "branch", "-r", "--merged", target_ref, "--list", f"origin/{branch}")
    if ok and out.strip():
        return True

    return False


def _was_feature_merged_via_log(issue_num: int, target: str) -> bool:
    """Return True if the target branch history contains a merge commit for this issue.

    Used when _find_feature_branch returns None (branch was deleted after merging).
    finish_feature.py creates commits of the form:
        "Merge feature/<N>-<slug> into <target> (issue #<N>)"
    so we search the recent merge history of the target for that pattern.

    Case-sensitivity note: ``git log --grep`` is case-sensitive by default.
    The ``issue #<N>`` grep is unaffected (numerics don't vary in case).
    The branch-name grep uses ``--regexp-ignore-case`` so that unusual branch
    prefixes (e.g. ``Feature/`` instead of ``feature/``) are still matched,
    even though project convention requires lowercase kebab-case branch names.
    """
    target_ref = f"origin/{target}"
    ok, _, _ = _try("git", "rev-parse", "--verify", target_ref)
    if not ok:
        target_ref = target

    # Search for merge commits containing the issue reference
    ok, out, _ = _try(
        "git", "log", target_ref, "--merges", "--oneline",
        f"--grep=issue #{issue_num}", "-1",
    )
    if ok and out.strip():
        return True

    # Also match branch name directly (covers non-standard merge messages).
    # --regexp-ignore-case guards against uppercase branch prefixes.
    ok, out, _ = _try(
        "git", "log", target_ref, "--merges", "--oneline",
        "--regexp-ignore-case", f"--grep=feature/{issue_num}-", "-1",
    )
    return ok and bool(out.strip())


def _git_target_ref(target: str) -> str:
    """Return the best available git ref for target: origin/<target> if it exists, else <target>."""
    origin_ref = f"origin/{target}"
    ok, _, _ = _try("git", "rev-parse", "--verify", origin_ref)
    return origin_ref if ok else target


def _is_issue_merged_into_target(
    issue_num: int,
    target: str,
    feature_branch: Optional[str] = None,
) -> bool:
    """Strict merge check — does NOT use git branch --merged to avoid false positives.

    A stale local feature branch whose tip is an ancestor of <target> but was
    never actually merged (e.g. superseded/force-pushed) would pass
    ``git branch --merged`` and trigger the E2 already-merged guard falsely.
    This function avoids that by requiring a merge-log entry OR confirming there
    are no unique commits on the feature side not yet on the target.

    Decision tree:
    1. If a merge log entry is found in target → True (fastest, authoritative).
    2. Resolve the feature branch tip (origin first, then local).
    3. If the feature tip is NOT reachable from target (i.e. rev-list --count > 0)
       → False (unmerged work present).
    4. If we reach this point the tip IS an ancestor of target but no merge log
       exists → False (stale ancestor, not merged).
    """
    target_ref = _git_target_ref(target)

    # Step 1: merge log is the most reliable signal
    if _was_feature_merged_via_log(issue_num, target):
        return True

    # Step 2: resolve feature branch tip
    branch = feature_branch or _find_feature_branch(issue_num)
    if not branch:
        return False

    # Prefer origin ref for the feature branch tip
    feature_ref = f"origin/{branch}"
    ok, _, _ = _try("git", "rev-parse", "--verify", feature_ref)
    if not ok:
        feature_ref = branch
        ok, _, _ = _try("git", "rev-parse", "--verify", feature_ref)
        if not ok:
            return False

    ok, feature_sha, _ = _try("git", "rev-parse", feature_ref)
    if not ok or not feature_sha.strip():
        return False
    feature_sha = feature_sha.strip()

    # Step 3: check for unmerged commits
    ok, count_out, _ = _try(
        "git", "rev-list", "--count", f"{feature_sha}", f"^{target_ref}",
    )
    if ok:
        try:
            unmerged = int(count_out.strip())
        except ValueError:
            unmerged = 1
        if unmerged > 0:
            # Feature has commits not present in target — genuinely unmerged
            return False

    # Step 4: tip is ancestor of target but no merge log → stale, not merged
    return False


def _git_verified_shipped_issues(
    state: "SprintState",
    merge_target: str,
) -> list:
    """Return issues from state that are status==done AND git-verified as merged into merge_target."""
    return [
        i for i in state.issues
        if i.status == "done"
        and _is_issue_merged_into_target(i.number, merge_target)
    ]


def _reporting_not_shipped_issues(
    state: "SprintState",
    merge_target: str,
) -> list:
    """Return issues that are skipped, plus done-but-not-git-verified (false done).

    These issues belong in the "Skipped / Failed" section of the sprint PR/summary
    so operators can see tickets that were marked done without actual merge proof.
    """
    result = []
    for i in state.issues:
        if i.status == "skipped":
            result.append(i)
        elif i.status == "done" and not _is_issue_merged_into_target(i.number, merge_target):
            result.append(i)
    return result


def _log_shipped_status_git_mismatch(
    state: "SprintState",
    merge_target: str,
) -> None:
    """Log a warning for any done-labelled ticket that fails git verification."""
    for i in state.issues:
        if i.status == "done" and not _is_issue_merged_into_target(i.number, merge_target):
            structured_log.warn(
                "shipped_git_mismatch",
                f"Issue #{i.number} marked done but not git-verified on {merge_target}",
                issue_num=i.number,
                merge_target=merge_target,
                issue_title=i.title,
            )
            sys.stdout.write(
                f"  [git-verify] WARNING: #{i.number} '{i.title}' is status=done "
                f"but not git-verified on {merge_target}\n"
            )


def _shipped_reconciliation_mismatch(
    state: "SprintState",
    merge_target: str,
) -> list[int]:
    """Return issue numbers that are status==done but not git-verified on merge_target."""
    return [
        i.number
        for i in state.issues
        if i.status == "done" and not _is_issue_merged_into_target(i.number, merge_target)
    ]


def _fail_loud_shipped_reconciliation(
    state: "SprintState",
    merge_target: str,
    context: str,
) -> list[int]:
    """Check for done-but-unverified tickets and fail loud if any are found.

    Logs a structured error and prints a prominent [ERROR] line.
    Returns the list of mismatched issue numbers (empty = all clear).
    """
    mismatch = _shipped_reconciliation_mismatch(state, merge_target)
    if mismatch:
        nums_str = ", ".join(f"#{n}" for n in mismatch)
        structured_log.error(
            "shipped_reconciliation_failed",
            f"{context}: {len(mismatch)} ticket(s) marked done but not git-verified on {merge_target}",
            issue_nums=mismatch,
            merge_target=merge_target,
            context=context,
        )
        sys.stdout.write(
            f"  [ERROR] {context}: {len(mismatch)} ticket(s) marked done but not "
            f"git-verified on {merge_target}: {nums_str}\n"
        )
    return mismatch


def _prune_stale_local_feature_branch(
    issue_num: int,
    merge_target: str,
    cwd: Optional[Path] = None,
) -> None:
    """Delete a stale local feature branch so E2 merge-check reads fresh state.

    A local branch is considered stale and is deleted when either:
    - origin/feature exists with a different SHA (local is divergent / outdated), OR
    - local branch exists, no origin/feature exists, the issue is NOT git-verified
      merged, and the local tip has 0 commits outside merge_target (stale ancestor).

    Runs fetch origin first so the origin/ refs are current.
    """
    effective_cwd = cwd or REPO_ROOT

    # fetch to refresh origin refs
    _run("git", "fetch", "origin", cwd=effective_cwd, check=False)

    # find local feature branch pattern feature/<num>-*
    ok_local, local_out, _ = _try(
        "git", "branch", "--list", f"feature/{issue_num}-*",
        cwd=effective_cwd,
    )
    if not ok_local or not local_out.strip():
        return  # no local branch, nothing to prune

    local_branch = local_out.strip().lstrip("* ").split()[0]

    # get local tip SHA
    ok_local_sha, local_sha, _ = _try(
        "git", "rev-parse", "--verify", local_branch,
        cwd=effective_cwd,
    )
    if not ok_local_sha:
        return

    local_sha = local_sha.strip()

    # check origin/feature
    ok_origin, origin_out, _ = _try(
        "git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*",
        cwd=effective_cwd,
    )
    origin_branch_exists = ok_origin and bool(origin_out.strip())

    if origin_branch_exists:
        origin_ref = origin_out.strip().split()[0]
        ok_origin_sha, origin_sha, _ = _try(
            "git", "rev-parse", "--verify", origin_ref,
            cwd=effective_cwd,
        )
        if ok_origin_sha and origin_sha.strip() != local_sha:
            # local is divergent from origin — delete it
            ok_del, _, _ = _try("git", "branch", "-D", local_branch, cwd=effective_cwd)
            if ok_del:
                sys.stdout.write(
                    f"  [prune] deleted stale local branch {local_branch} "
                    f"(diverged from origin)\n"
                )
        return

    # No origin branch — check if local is a stale ancestor
    if not _is_issue_merged_into_target(issue_num, merge_target):
        # not git-verified merged — check if local tip has 0 unique commits
        ok_count, count_out, _ = _try(
            "git", "rev-list", "--count", local_sha, f"^{merge_target}",
            cwd=effective_cwd,
        )
        if ok_count:
            try:
                count = int(count_out.strip())
            except ValueError:
                count = 1
            if count == 0:
                ok_del, _, _ = _try("git", "branch", "-D", local_branch, cwd=effective_cwd)
                if ok_del:
                    sys.stdout.write(
                        f"  [prune] deleted stale local branch {local_branch} "
                        f"(ancestor of {merge_target}, no origin, not verified merged)\n"
                    )


# ── quality gates ─────────────────────────────────────────────────────────────

_GATE_FAILURE_CLASS_MAP: dict[str, str] = {
    "pytest":        "test",
    "lint":          "lint",
    "ruff":          "lint",
    "typecheck":     "typecheck",
    "design":        "design",
    "merge-preview": "merge",
}


def _revert_to_sit_impl(issue_num: int, gate_name: str, output: str,
                        repo_name: Optional[str] = None,
                        repo_root: Optional[Path] = None) -> None:
    """Label the issue SIT and post a structured failure comment.

    Always calls record_failure() so the sidecar covers all gate failure classes.
    When failure-parsing helpers are available, also appends structured tables.
    """
    truncated = output[:2000] if len(output) > 2000 else output
    comment = (
        f"Quality gate failed: **{gate_name}**\n"
        f"Issue reverted to SIT for re-inspection.\n\n"
        f"**{gate_name}** output:\n```\n{truncated}\n```"
    )

    failure_class = _GATE_FAILURE_CLASS_MAP.get(gate_name, gate_name)
    files_to_inspect: list[str] = []

    if _FAILURE_PARSING_AVAILABLE:
        try:
            failures = parse_failures(gate_name, output)
            comment += build_failure_block(gate_name, failures)
            files_to_inspect = sorted({
                f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
                for f in failures if f.get("file")
            })
        except Exception as e:
            structured_log.error(
                "failure_parsing_error",
                f"failure parsing failed for {gate_name}: {e}",
                issue_num=issue_num,
                exc=str(e),
            )

    record_failure(
        issue_num,
        failure_class,
        detail=truncated,
        repo_root=repo_root,
        summary=f"Issue #{issue_num}: {gate_name} gate failed",
        files_to_inspect=files_to_inspect,
    )

    # Store full gate output for Gate Failure Analysis (issue #701).
    # Using the full untruncated output so AC-2 (untruncated error) is satisfied.
    _write_gate_failure_record(issue_num, gate_name, output, repo_root=repo_root)

    _transition_safe(issue_num, _TicketState.SIT, actor="sprint_manager:gate_fail", repo_name=repo_name)
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        structured_log.warn("github_update_failed", f"failed to post gate failure comment: {e}", issue_num=issue_num, exc=str(e))


def _post_success_comment(issue_num: int, results: list[GateResult],
                          repo_name: Optional[str] = None,
                          gates_skipped: bool = False,
                          target_branch: str = "develop") -> None:
    gate_lines = "\n".join(
        f"- **{r.gate}**: {r.symbol}" for r in results
    )
    if gates_skipped:
        header = f"Quality gates skipped (`--skip-gates`). Tester verified, then auto-merged into `{target_branch}`."
    else:
        header = f"Quality gates passed. Tester verified → gates → merged into `{target_branch}`."
    comment = (
        f"{header}\n\n"
        f"Gates:\n{gate_lines}\n\n"
        f"The work now lives on `{target_branch}` (the sprint branch). It reaches "
        f"`develop` only when you click **Merge Sprint** — that is why there is no "
        f"per-ticket PR into develop. Awaiting human UAT approval."
    )
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        structured_log.warn("success_comment_failed", f"failed to post success comment: {e}", issue_num=issue_num, exc=str(e))


# _MERGE_PREVIEW_TMP_BRANCH, _gate_merge_preview, _gate_typecheck,
# _impeccable_findings, _finding_sig, _net_new_findings, _gate_design,
# _gate_monolith, _log_gate_result, _run_quality_gates extracted to
# gates.py (issue #1281); re-imported above via the gates import block.


def _create_sprint_branch(sprint_branch: str, parent_ref: str = "develop") -> None:
    """Create sprint/<label> off parent_ref and push to origin (idempotent).

    Base sprints (sprint-N) are created off develop; child sprints (sprint-N.M)
    off the base sprint branch (sprint/sprint-N). See sprint-lifecycle.md.
    """
    # Check if branch already exists on remote
    ok, _, _ = _try("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{sprint_branch}")
    if ok:
        sys.stdout.write(str(f"  Sprint branch {sprint_branch!r} already exists on origin — skipping creation.") + "\n")
        return

    # Check if branch already exists locally
    ok, _, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{sprint_branch}")
    if ok:
        sys.stdout.write(str(f"  Sprint branch {sprint_branch!r} already exists locally — pushing to origin.") + "\n")
        _run("git", "push", "-u", "origin", sprint_branch)
        return

    sys.stdout.write(str(f"  Creating sprint branch {sprint_branch!r} off {parent_ref}…") + "\n")
    _run("git", "fetch", "origin")
    ok, parent_sha, _ = _try("git", "rev-parse", f"origin/{parent_ref}")
    if not ok or not parent_sha:
        ok, parent_sha, _ = _try("git", "rev-parse", parent_ref)
    if not ok or not parent_sha:
        structured_log.warn(
            "sprint_branch_sha_resolve_failed",
            f"could not resolve {parent_ref!r} SHA — using HEAD for sprint branch",
        )
        parent_sha = "HEAD"
    _run("git", "branch", sprint_branch, parent_sha)
    _run("git", "push", "-u", "origin", sprint_branch)
    sys.stdout.write(str(f"  Sprint branch {sprint_branch!r} created and pushed.") + "\n")


def _call_finish_feature(
    issue_num: int,
    worktester_root: Optional[Path] = None,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    sprint_label: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """Call finish_feature.py as a subprocess from the worktester root.

    Returns (success, conflict_files).
    On a rebase conflict after an automated one-shot rebase attempt (issue #1414),
    conflict_files lists the paths that could not be reconciled automatically.
    On success or any other failure, conflict_files is empty.
    """
    if cfg is not None:
        finish_script = cfg.finish_feature_script
        # Prefer the dedicated agents clone for the documentor (falls back to the
        # tester worktree when not configured).
        wt_root = worktester_root or cfg.worktree_agents or cfg.worktree_tester
    else:
        finish_script = FINISH_FEATURE_SCRIPT
        wt_root = worktester_root or WORKTESTER_ROOT

    cmd = [
        sys.executable, str(finish_script),
        "--issue", str(issue_num),
        "--target-branch", target_branch,
    ]
    if repo_name:
        cmd += ["--repo", repo_name]

    sub_env = os.environ.copy()
    if sprint_label:
        sub_env["COMMANDER_SPRINT_RUNNING"] = sprint_label

    sys.stdout.write(str(f"  Calling finish_feature.py --issue {issue_num} --target-branch {target_branch} ...") + "\n")
    # issue #738: serialize develop/sprint-branch merges — if a merge is already
    # in flight (e.g. a concurrent tester or a stale retry), block until it lands
    # so two merges never push to the shared target branch at the same time.
    with _develop_merge_guard():
        result = subprocess.run(cmd, cwd=str(wt_root), capture_output=True, text=True, env=sub_env)
    if result.stdout:
        sys.stdout.write(str(result.stdout.rstrip()) + "\n")
    if result.returncode == 0:
        sys.stdout.write(str("  finish_feature.py completed successfully") + "\n")
        return True, []

    structured_log.error(
        "subprocess_nonzero_exit",
        f"finish_feature.py exited {result.returncode}",
        issue_num=issue_num,
        subprocess="finish_feature.py",
        exit_code=result.returncode,
        subprocess_stderr=result.stderr.rstrip() if result.stderr else "",
    )

    # ── Automated one-shot rebase (issue #1414 AC2) ───────────────────────────
    # finish_feature.py failing here typically means a concurrent merge landed on
    # the target branch first, causing a divergence.  Attempt a single automated
    # rebase to reconcile the feature branch before giving up.
    sys.stdout.write(str(
        f"  [rebase] finish_feature.py failed — attempting one-shot rebase of "
        f"feature/{issue_num}-* onto origin/{target_branch}"
    ) + "\n")

    # Locate the remote feature branch.
    ok, br_out, _ = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*", cwd=wt_root)
    if not ok or not br_out.strip():
        sys.stdout.write(str(
            f"  [rebase] no remote feature branch found for #{issue_num} — skipping rebase"
        ) + "\n")
        return False, []

    feature_branch = br_out.strip().splitlines()[0].strip().removeprefix("origin/")

    # Ensure we have the latest refs.
    _try("git", "fetch", "origin", cwd=wt_root)

    # Checkout the feature branch in the tester worktree (separate full clone —
    # not a linked worktree, so this never conflicts with pool-slot worktrees).
    ok_co, _, co_err = _try(
        "git", "checkout", "-B", feature_branch, f"origin/{feature_branch}", cwd=wt_root,
    )
    if not ok_co:
        sys.stdout.write(str(f"  [rebase] could not checkout {feature_branch}: {co_err}") + "\n")
        return False, []

    # Attempt a single rebase onto the current target (AC8: only one attempt).
    ok_rb, rb_out, rb_err = _try(
        "git", "rebase", f"origin/{target_branch}", cwd=wt_root,
    )
    rebase_combined = rb_out + "\n" + rb_err

    if not ok_rb:
        conflict_files = _extract_rebase_conflict_files(rebase_combined)
        sys.stdout.write(str(
            f"  [rebase] rebase conflict for #{issue_num} in "
            f"{len(conflict_files)} file(s): {conflict_files}"
        ) + "\n")
        _try("git", "rebase", "--abort", cwd=wt_root)
        return False, conflict_files

    # Rebase succeeded — push the rebased branch so finish_feature.py can merge it.
    sys.stdout.write(str(f"  [rebase] rebased {feature_branch} onto origin/{target_branch} — pushing") + "\n")
    ok_push, _, push_err = _try(
        "git", "push", "--force-with-lease", "origin", feature_branch, cwd=wt_root,
    )
    if not ok_push:
        sys.stdout.write(str(f"  [rebase] push of rebased branch failed: {push_err}") + "\n")
        return False, []

    # Retry the merge exactly once after the successful rebase (AC8).
    sys.stdout.write(str(
        f"  [rebase] retrying finish_feature.py for #{issue_num} after successful rebase"
    ) + "\n")
    with _develop_merge_guard():
        result2 = subprocess.run(cmd, cwd=str(wt_root), capture_output=True, text=True, env=sub_env)
    if result2.stdout:
        sys.stdout.write(str(result2.stdout.rstrip()) + "\n")
    if result2.returncode == 0:
        sys.stdout.write(str("  [rebase] post-rebase merge completed successfully") + "\n")
        return True, []

    sys.stdout.write(str(
        f"  [rebase] post-rebase merge also failed (exit {result2.returncode}) — giving up"
    ) + "\n")
    return False, []


# ── documentor integration (issue #103) ──────────────────────────────────────

def _run_documentor(
    issue_nums: "list[int]",
    sprint_label: str,
    repo_name: Optional[str],
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Invoke document_issue.py for every issue in issue_nums (best-effort, non-blocking).

    Called once per sprint after the dispatch loop, with the full list of
    merged issue numbers and the sprint label as context (issue #697).
    Failures are logged as warnings so they never block the pipeline.
    """
    document_script = Path(__file__).parent / "document_issue.py"
    if not document_script.exists():
        sys.stderr.write(str("  [documentor] document_issue.py not found — skipping") + "\n")
        return

    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    # Log-format contract (issue #705): the opening/closing lines below are
    # per-sprint (issue #697). The only on-disk consumer is the dashboard's
    # log_source.read_log, which returns the raw tail verbatim — no structured
    # parsing — so the format is safe to display. The exact shape is pinned by
    # tests/test_705__documentor_log_format_contract.py; keep them in sync.
    sys.stdout.write(str(f"  [documentor] running for sprint {sprint_label} "
        f"({len(issue_nums)} ticket(s): {issue_nums}) ...") + "\n")
    for issue_num in issue_nums:
        cmd = [sys.executable, str(document_script), "--issue", str(issue_num), "--mode", "both"]
        if eff_repo:
            cmd += ["--repo", eff_repo]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.stdout:
                for line in result.stdout.splitlines():
                    sys.stdout.write(str(f"  {line}") + "\n")
            if result.returncode != 0:
                sys.stderr.write(str(f"  [documentor] issue #{issue_num} exited {result.returncode} (non-fatal)") + "\n")
                if result.stderr:
                    sys.stderr.write(str(f"  [documentor] stderr: {result.stderr.strip()[:400]}") + "\n")
        except subprocess.TimeoutExpired:
            structured_log.warn("documentor_timeout", "[documentor] timed out after 300s", issue_num=issue_num)
        except Exception as e:
            structured_log.error("documentor_error", f"[documentor] error: {e}", issue_num=issue_num, exc=str(e))
    sys.stdout.write(str(f"  [documentor] completed for sprint {sprint_label}") + "\n")


# ── post-tester hook ──────────────────────────────────────────────────────────

def handle_post_tester(
    issue_num: int,
    tester_exit_code: int,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    gate_typecheck: bool = True,
    gate_design: bool = True,
    gate_frontend_lint: bool = True,
    gate_monolith: bool = True,
    worktester_root: Optional[Path] = None,
    worktester_dashboard: Optional[Path] = None,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
    documentor_enabled: bool = False,
    alert_modes: Optional[list] = None,
    sprint_label: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """Called after a tester subprocess exits.

    Returns (merged: bool, summary_line: str, failure_category: Optional[str]).

    AC-1: Gates only run if tester exited 0 AND label is exactly UAT.

    base_branch: branch to diff against when gate_scope='changed' (default: 'develop').
    gate_scope: 'changed' (default) scopes gates to changed files only;
                'full' restores legacy full-codebase behaviour.
    """
    # Resolve paths: prefer cfg, then explicit args, then globals
    if cfg is not None:
        wt_root      = worktester_root      or cfg.worktree_tester
        wt_dashboard = worktester_dashboard or cfg.worktree_tester_app
        eff_repo     = repo_name            or cfg.repo_name
        api_url      = cfg.api_url
    else:
        wt_root      = worktester_root      or WORKTESTER_ROOT
        wt_dashboard = worktester_dashboard or WORKTESTER_DASHBOARD
        eff_repo     = repo_name
        api_url      = None

    if tester_exit_code != 0:
        sys.stdout.write(str(f"  Tester for issue #{issue_num} exited {tester_exit_code} — status: failed") + "\n")
        sys.stdout.flush()
        return (False,
                f"Issue #{issue_num}: tester exited {tester_exit_code}, skipping gates",
                FailureCategory.CRASH)

    # Fetch latest remote state before checking branch presence.  Without this,
    # sprint_manager sees stale remote-tracking refs where the feature branch
    # still appears even after the tester's finish_feature.py deleted it, and
    # _is_branch_merged_into returns False because origin/<target> doesn't yet
    # include the new merge commit — causing a false TESTER_REJECTED (issue #659).
    _try("git", "fetch", "--prune", "origin")

    # Determine merge status by branch presence and git log — no label check.
    # sprint_manager is the sole UAT label writer via transition().
    found_branch = _find_feature_branch(issue_num)
    branch_is_merged = False

    if found_branch:
        branch_is_merged = _is_issue_merged_into_target(issue_num, target_branch, found_branch)
        sys.stdout.write(str(f"  Issue #{issue_num}: feature branch '{found_branch}' merged into "
            f"'{target_branch}': {branch_is_merged}") + "\n")
        if not branch_is_merged:
            # Gate-first flow: tester verifies only; sprint_manager merges after gates pass.
            sys.stdout.write(str(f"  Issue #{issue_num}: branch not merged yet — "
                f"running quality gates before merge") + "\n")
    else:
        # Branch not found locally or remotely — check git log for merge commit.
        branch_is_merged = _was_feature_merged_via_log(issue_num, target_branch)
        if branch_is_merged:
            sys.stdout.write(str(f"  Issue #{issue_num}: feature branch not found but merge commit "
                f"found in '{target_branch}' history — treating as merged") + "\n")
        else:
            sys.stdout.write(str(f"  Issue #{issue_num}: feature branch not found and no merge "
                f"commit found in '{target_branch}' — treating as genuine tester skip") + "\n")
            warning_body = (
                f"**Tester exited 0 but feature branch not found and not merged.**\n\n"
                f"Tester subprocess finished successfully (exit code 0), but no "
                f"`feature/{issue_num}-*` branch exists locally or on origin, and no "
                f"merge commit was found in `{target_branch}`. Re-run the tester."
            )
            try:
                github_client.add_comment(issue_num, warning_body, repo_name=eff_repo)
            except Exception as exc:
                structured_log.warn("missing_merge_comment_failed", f"failed to post missing-merge comment: {exc}", issue_num=issue_num, exc=str(exc))
            if alert_modes:
                dispatch_alerts(
                    alert_modes,
                    title=f"Issue #{issue_num} skipped: tester exited 0 but not merged",
                    body=warning_body[:500],
                    issue_num=issue_num,
                    category=FailureCategory.TESTER_REJECTED,
                    cfg=cfg,
                    repo=eff_repo,
                )
            return (False,
                    f"Issue #{issue_num}: tester exited 0 but feature branch missing and not merged",
                    FailureCategory.TESTER_REJECTED)

    # Tester passed. Merge after gates unless the branch is already in target_branch.
    needs_merge = not branch_is_merged
    already_merged_by_tester = (found_branch is None) and branch_is_merged
    if already_merged_by_tester:
        sys.stdout.write(str(f"  Issue #{issue_num}: feature branch deleted — "
            f"merge already completed; skipping re-merge") + "\n")
    elif not needs_merge:
        sys.stdout.write(str(f"  Issue #{issue_num}: feature branch already merged into "
            f"'{target_branch}' — skipping re-merge") + "\n")
    feature_branch = found_branch or f"feature/{issue_num}-unknown"

    sys.stdout.write(str(f"\nTester finished for issue #{issue_num} -- running quality gates...") + "\n")

    if skip_gates:
        sys.stdout.write(str("  --skip-gates active -- skipping all quality gates, proceeding to merge") + "\n")
        if needs_merge:
            merge_ok, conflict_files = _call_finish_feature(
                issue_num, wt_root, target_branch=target_branch,
                repo_name=eff_repo, cfg=cfg, sprint_label=sprint_label,
            )
            if not merge_ok and conflict_files:
                # Rebase conflict even under skip-gates — label needs-rework (issue #1414 AC4).
                _transition_safe(issue_num, _TicketState.NEEDS_REWORK, actor="sprint_manager", repo_name=eff_repo)
                conflict_body = (
                    f"❌ **Rebase conflict** — automated rebase of `feature/{issue_num}` onto "
                    f"`{target_branch}` failed.\n\n**Conflicting files:**\n"
                    + "\n".join(f"- `{f}`" for f in conflict_files)
                    + "\n\nResolve the conflict manually and re-push the feature branch."
                )
                try:
                    github_client.add_comment(issue_num, conflict_body, repo_name=eff_repo)
                except Exception as exc:
                    structured_log.warn("rebase_conflict_comment_failed", str(exc), issue_num=issue_num)
                return (
                    False,
                    f"Issue #{issue_num}: rebase conflict in {len(conflict_files)} file(s): "
                    + ", ".join(conflict_files[:3]),
                    FailureCategory.REBASE_CONFLICT,
                )
        _post_agent_event("gate:merging", api_url=api_url)
        all_skipped = [
            GateResult(gate="typecheck",     passed=True, skipped=True),
            GateResult(gate="lint",          passed=True, skipped=True),
            GateResult(gate="design",        passed=True, skipped=True),
            GateResult(gate="pytest",        passed=True, skipped=True),
            GateResult(gate="merge-preview", passed=True, skipped=True),
        ]
        _transition_safe(issue_num, _TicketState.UAT, actor="sprint_manager", repo_name=eff_repo)
        _post_success_comment(issue_num, all_skipped, repo_name=eff_repo,
                              gates_skipped=True, target_branch=target_branch)
        _delete_failure_sidecar(issue_num)
        if not needs_merge:
            return True, f"Issue #{issue_num}: merge already done, UAT applied, comment posted", None
        return True, f"Issue #{issue_num}: all gates skipped, merged into {target_branch}, UAT applied", None

    results = _run_quality_gates(
        issue_num=issue_num,
        feature_branch=feature_branch,
        worktester_root=wt_root,
        worktester_dashboard=wt_dashboard,
        skip_all=False,
        gate_pytest=gate_pytest,
        gate_lint=gate_lint,
        gate_merge_preview=gate_merge_preview,
        gate_typecheck=gate_typecheck,
        gate_design=gate_design,
        gate_frontend_lint=gate_frontend_lint,
        gate_monolith=gate_monolith,
        target_branch=target_branch,
        repo_name=eff_repo,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )

    # Check if all gates passed
    all_passed = all(r.passed for r in results)

    if all_passed:
        _post_agent_event("gate:merging", api_url=api_url)
        if needs_merge:
            sys.stdout.write(str(f"  All gates passed -- calling finish_feature.py for issue #{issue_num}") + "\n")
            merge_ok, conflict_files = _call_finish_feature(
                issue_num, wt_root, target_branch=target_branch,
                repo_name=eff_repo, cfg=cfg, sprint_label=sprint_label,
            )
            if not merge_ok:
                if conflict_files:
                    # Automated rebase failed — label needs-rework with exact conflict paths
                    # (issue #1414 AC4/AC7).  This does not halt the sprint (AC5).
                    _transition_safe(issue_num, _TicketState.NEEDS_REWORK, actor="sprint_manager", repo_name=eff_repo)
                    conflict_body = (
                        f"❌ **Rebase conflict** — automated rebase of `feature/{issue_num}` onto "
                        f"`{target_branch}` failed after all quality gates passed.\n\n"
                        f"**Conflicting files:**\n"
                        + "\n".join(f"- `{f}`" for f in conflict_files)
                        + "\n\nResolve the conflict manually and re-push the feature branch."
                    )
                    try:
                        github_client.add_comment(issue_num, conflict_body, repo_name=eff_repo)
                    except Exception as exc:
                        structured_log.warn("rebase_conflict_comment_failed", str(exc), issue_num=issue_num)
                    return (
                        False,
                        f"Issue #{issue_num}: rebase conflict in {len(conflict_files)} file(s): "
                        + ", ".join(conflict_files[:3]),
                        FailureCategory.REBASE_CONFLICT,
                    )
                # finish_feature.py failed for a non-rebase reason (e.g. push failed)
                return (
                    False,
                    f"Issue #{issue_num}: merge failed after all gates passed",
                    FailureCategory.MERGE_CONFLICT,
                )
        else:
            sys.stdout.write(str("  All gates passed -- merge already done, skipping re-merge") + "\n")
        _transition_safe(issue_num, _TicketState.UAT, actor="sprint_manager", repo_name=eff_repo)
        _post_success_comment(issue_num, results, repo_name=eff_repo, target_branch=target_branch)
        _delete_failure_sidecar(issue_num)
        if not needs_merge:
            return True, f"Issue #{issue_num}: all gates passed, merge already done, UAT applied", None
        return True, f"Issue #{issue_num}: all gates passed, merged into {target_branch}, UAT applied", None
    else:
        failed = next((r for r in results if not r.passed), None)
        gate_name = failed.gate if failed else "unknown"
        # Map gate name to fine-grained failure category for needs-rework logic
        gate_category_map = {
            "typecheck":     FailureCategory.GATE_FAIL,
            "design":        FailureCategory.GATE_FAIL,
            "pytest":        FailureCategory.PYTEST_FAIL,
            "lint":          FailureCategory.LINT_FAIL,
            "merge-preview": FailureCategory.MERGE_CONFLICT,
            "monolith":      FailureCategory.GATE_FAIL,
        }
        gate_category = gate_category_map.get(gate_name, FailureCategory.GATE_FAIL)
        return (False,
                f"Issue #{issue_num}: gate failed ({gate_name})",
                gate_category)


# ── agent dispatch helpers ────────────────────────────────────────────────────

def _build_failure_suffix(issue_num: int, repo_root: Optional[Path] = None) -> str:
    """Read the JSON failure sidecar for issue_num and return a prompt suffix.

    Handles both the new unified schema (failure_class / detail) and the legacy
    gate schema (gate / failures list) written by write_sidecar.

    Returns an empty string when no sidecar exists.
    """
    effective_root = repo_root or REPO_ROOT
    # Resolve sidecar path independently of _FAILURE_PARSING_AVAILABLE
    sc_path = effective_root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"

    if not sc_path.exists():
        sys.stdout.write(str(f"  [retry] failure sidecar not found at {sc_path} — using generic prompt") + "\n")
        return ""

    try:
        data = json.loads(sc_path.read_text(encoding="utf-8"))
    except Exception as e:
        structured_log.warn(
            "failure_sidecar_read_error",
            f"could not read failure sidecar: {e}",
            sidecar_path=str(sc_path),
            exc=str(e),
        )
        return ""

    failure_class    = data.get("failure_class")
    summary          = data.get("summary", "")
    detail           = data.get("detail", "")
    files_to_inspect = data.get("files_to_inspect", [])

    # Legacy schema: gate + structured failures list
    gate     = data.get("gate", "")
    failures = data.get("failures", [])

    lines: list[str] = []

    if failure_class:
        # New unified schema
        lines.append(f"\n\nPrevious failure class: {failure_class}.")
        if summary:
            lines.append(f"Summary: {summary}")
        if detail:
            lines.append(f"\nDetail:\n{detail}")
    elif failures:
        # Legacy gate schema
        lines.append(f"\n\nPrevious gate '{gate}' failed. Fix the following before re-submitting:")
        for f in failures:  # no cap — all failures surfaced
            loc   = f.get("location", "")
            ftype = f.get("type", "")
            msg   = f.get("issue", "")
            test  = f.get("test", "")
            entry = f"- {ftype} at {loc}: {msg}"
            if test:
                entry += f" (test: {test})"
            lines.append(entry)
    else:
        return ""

    if files_to_inspect:
        lines.append("\nFiles requiring changes:")
        for fi in files_to_inspect:
            lines.append(f"  {fi}")

    return "\n".join(lines)


# ── unified failure-recording chokepoint ─────────────────────────────────────

def _build_crash_detail(log_path: Path, exit_code: Optional[int] = None,
                        signal: Optional[str] = None, tail_lines: int = 50) -> str:
    """Return a detail string for non-gate failures: log tail + exit code/signal.

    Works even when the log file does not exist.
    """
    parts: list[str] = []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
        if tail:
            parts.append("Log tail:\n" + "\n".join(tail))
    except Exception:
        pass

    if exit_code is not None:
        parts.append(f"Exit code: {exit_code}")
    if signal:
        parts.append(f"Signal: {signal}")

    return "\n".join(parts) if parts else "(no detail available)"


def record_failure(
    issue_num: int,
    failure_class: str,
    detail: str,
    repo_root: Optional[Path] = None,
    summary: Optional[str] = None,
    files_to_inspect: Optional[list] = None,
    log_tail: Optional[list] = None,
) -> Optional[Path]:
    """Write a JSON failure sidecar for any failure class.

    Works independently of _FAILURE_PARSING_AVAILABLE — uses a direct path
    construction so it never depends on the optional post_test_report import.

    `log_tail` is a list of the last N lines of agent stdout/stderr, stored
    as a structured field for hang-redispatch context (issue #787).

    Returns the sidecar path on success, None on write error.
    """
    effective_root = repo_root or REPO_ROOT
    try:
        runtime_dir = effective_root / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc_path = runtime_dir / f"last-failure-{issue_num}.json"

        payload = {
            "issue":            issue_num,
            "failure_class":    failure_class,
            "summary":          summary or f"Issue #{issue_num}: {failure_class} failure",
            "detail":           detail,
            "files_to_inspect": files_to_inspect or [],
            "log_tail":         log_tail or [],
            "run_id":           os.environ.get("COMMANDER_RUN_ID"),
            "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        sc_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        sys.stdout.write(str(f"  [failure] Wrote sidecar ({failure_class}): {sc_path}") + "\n")
        sys.stdout.flush()
        structured_log.info(
            "sidecar_written", f"failure sidecar written for #{issue_num}",
            issue_num=issue_num, failure_class=failure_class, path=str(sc_path),
        )
        return sc_path
    except Exception as e:
        structured_log.error(
            "record_failure_error",
            f"failed to write failure sidecar for issue #{issue_num}: {e}",
            issue_num=issue_num,
            failure_class=failure_class,
            exc=str(e),
        )
        return None


def _delete_failure_sidecar(issue_num: int, repo_root: Optional[Path] = None) -> None:
    """Delete the failure sidecar for issue_num if it exists (success path)."""
    effective_root = repo_root or REPO_ROOT
    sc_path = effective_root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"
    try:
        if sc_path.exists():
            sc_path.unlink()
            sys.stdout.write(str(f"  [failure] Deleted sidecar on success: {sc_path}") + "\n")
            sys.stdout.flush()
    except Exception as e:
        structured_log.warn(
            "sidecar_delete_error",
            f"could not delete failure sidecar for #{issue_num}: {e}",
            issue_num=issue_num,
            exc=str(e),
        )
    # Clear gate failure records on success (gate passed — no analysis needed).
    _clear_gate_failure_records(issue_num, repo_root=repo_root)


def _issue_log_path(issue_num: int, cfg: Optional["SprintConfig"] = None) -> Path:
    logs_dir = cfg.logs_dir if cfg is not None else (DASHBOARD_DIR / "logs")
    return logs_dir / f"sprint-issue-{issue_num}.log"


# ── gate failure analysis (issue #701) ────────────────────────────────────────

def _gate_failures_log_path(cfg: Optional["SprintConfig"] = None) -> Path:
    logs_dir = cfg.logs_dir if cfg is not None else (DASHBOARD_DIR / "logs")
    return logs_dir / "gate-failures.md"


def _gate_failure_records_path(issue_num: int, repo_root: Optional[Path] = None) -> Path:
    effective_root = repo_root or REPO_ROOT
    return effective_root / ".commander" / "runtime" / f"gate-failure-records-{issue_num}.jsonl"


def _write_gate_failure_record(
    issue_num: int,
    gate_name: str,
    output: str,
    repo_root: Optional[Path] = None,
) -> None:
    """Append one gate failure record to the JSONL sidecar for issue_num."""
    path = _gate_failure_records_path(issue_num, repo_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "gate_name": gate_name,
            "output": output,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        structured_log.warn(
            "gate_record_write_error",
            f"could not write gate failure record for #{issue_num}: {e}",
            issue_num=issue_num,
            exc=str(e),
        )


def _read_gate_failure_records(
    issue_num: int,
    repo_root: Optional[Path] = None,
) -> list[dict]:
    """Read all gate failure records for issue_num from the JSONL sidecar."""
    path = _gate_failure_records_path(issue_num, repo_root)
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        structured_log.warn(
            "gate_record_read_error",
            f"could not read gate failure records for #{issue_num}: {e}",
            issue_num=issue_num,
            exc=str(e),
        )
    return records


def _clear_gate_failure_records(
    issue_num: int,
    repo_root: Optional[Path] = None,
) -> None:
    """Delete the gate failure JSONL sidecar for issue_num if it exists."""
    path = _gate_failure_records_path(issue_num, repo_root)
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        structured_log.warn(
            "gate_record_clear_error",
            f"could not clear gate failure records for #{issue_num}: {e}",
            issue_num=issue_num,
            exc=str(e),
        )


def _generate_gate_failure_analysis(
    gate_name: str,
    error_output: str,
    issue_num: int = 0,
    cfg: Optional["SprintConfig"] = None,
) -> dict:
    """Call claude -p (Haiku) to generate root cause + prevention for a gate failure.

    Returns {"root_cause": "...", "prevention": "..."}.
    Returns placeholder strings on any error so the sprint never blocks.
    """
    prompt = (
        f"A quality gate named '{gate_name}' failed with this error output:\n\n"
        f"```\n{error_output[:3000]}\n```\n\n"
        "Respond with a JSON object with exactly two keys:\n"
        '- "root_cause": one concise sentence explaining WHY the submitted code '
        "failed this gate (be specific, e.g. \"Type annotation missing on return "
        "value of `process_item`\")\n"
        '- "prevention": one or two concrete actionable steps the coder should '
        "take before next submission to pass this gate (e.g. \"Run `tsc --noEmit` "
        "locally before marking complete\")\n\n"
        "Output ONLY the JSON object, no other text."
    )
    # Respect the configured model (issue #708) — fall back to the hardcoded
    # default only when cfg is unavailable, so this stays consistent with the
    # per-agent model config added in #700.
    model = cfg.reviewer_model if cfg is not None else "claude-haiku-4-5-20251001"
    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", model,
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            data = _extract_analysis_json(result.stdout)
            if data and "root_cause" in data and "prevention" in data:
                return {"root_cause": str(data["root_cause"]), "prevention": str(data["prevention"])}
    except Exception as e:
        structured_log.warn(
            "gate_analysis_llm_error",
            f"LLM gate analysis failed for #{issue_num} ({gate_name}): {e}",
            issue_num=issue_num,
            gate=gate_name,
            exc=str(e),
        )
    return {
        "root_cause": f"Code did not satisfy the {gate_name} gate requirements.",
        "prevention": (
            f"Review the {gate_name} gate error output and fix all reported issues "
            "before resubmitting."
        ),
    }


def _extract_analysis_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from LLM analysis output."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _post_gate_failure_analysis_comment(
    issue_num: int,
    gate_name: str,
    error_output: str,
    root_cause: str,
    prevention: str,
    repo_name: Optional[str] = None,
) -> None:
    """Post a structured ## Gate Failure Analysis comment to the GitHub issue."""
    comment = (
        f"## Gate Failure Analysis\n\n"
        f"### Gate & Error\n\n"
        f"**Gate:** {gate_name}\n\n"
        f"```\n{error_output}\n```\n\n"
        f"### Root Cause\n\n{root_cause}\n\n"
        f"### Prevention\n\n{prevention}\n"
    )
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        structured_log.warn(
            "gate_analysis_comment_error",
            f"failed to post Gate Failure Analysis for #{issue_num}: {e}",
            issue_num=issue_num,
            gate=gate_name,
            exc=str(e),
        )


def _append_gate_failure_to_sprint_log(
    issue_num: int,
    gate_name: str,
    error_output: str,
    root_cause: str,
    prevention: str,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Append a dated Gate Failure Analysis entry to the sprint gate failures log."""
    log_path = _gate_failures_log_path(cfg)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = (
        f"\n## Gate Failure Analysis — {timestamp} — Issue #{issue_num}\n\n"
        f"### Gate & Error\n\n"
        f"**Gate:** {gate_name}\n\n"
        f"```\n{error_output}\n```\n\n"
        f"### Root Cause\n\n{root_cause}\n\n"
        f"### Prevention\n\n{prevention}\n"
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        structured_log.warn(
            "gate_log_append_error",
            f"failed to append Gate Failure Analysis to sprint log for #{issue_num}: {e}",
            issue_num=issue_num,
            gate=gate_name,
            exc=str(e),
        )


def _publish_gate_failure_analyses(
    issue_num: int,
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Post Gate Failure Analysis comment + sprint log entry for each recorded gate failure.

    Called after the fix-loop is exhausted (all retries consumed or early-abort).
    Reads gate failure records accumulated by _write_gate_failure_record (called
    from _revert_to_sit on each gate failure), processes each independently
    (AC-7: no merging), then clears the records.
    """
    records = _read_gate_failure_records(issue_num)
    if not records:
        return
    for record in records:
        gate_name = record.get("gate_name", "unknown")
        error_output = record.get("output", "")
        analysis = _generate_gate_failure_analysis(
            gate_name, error_output, issue_num=issue_num, cfg=cfg
        )
        _post_gate_failure_analysis_comment(
            issue_num,
            gate_name,
            error_output,
            analysis["root_cause"],
            analysis["prevention"],
            repo_name=repo_name,
        )
        _append_gate_failure_to_sprint_log(
            issue_num,
            gate_name,
            error_output,
            analysis["root_cause"],
            analysis["prevention"],
            cfg=cfg,
        )
    _clear_gate_failure_records(issue_num)


IMPECCABLE_CONTEXT_SCRIPT = ".github/skills/impeccable/scripts/context.mjs"


def _impeccable_context_instruction() -> str:
    """Prompt fragment injected into every headless coder/tester dispatch (issue #713).

    Threads the file-based impeccable skill pack — NOT the Claude Code plugin —
    into the headless ``claude -p`` run so the agent loads the project's design
    rules before touching any frontend code. When a mock is attached the agent
    treats it as the pixel target; UI output must clear ``impeccable detect``.
    """
    return (
        " IMPECCABLE DESIGN CONTEXT (issue #713): before writing or reviewing any"
        f" frontend/UI code, load the design skills by running `node {IMPECCABLE_CONTEXT_SCRIPT}`"
        " from the repo root and follow the rules it prints — this is the"
        " file-based skill pack under .github/skills, not an installed extension."
        " When a mock HTML file is attached under"
        " references/issue-<N>/, treat it as the pixel-accurate visual target and"
        " reproduce it. UI output must pass `npx impeccable detect` on the first try."
    )


def _design_docs_guard(cwd_path: "Path") -> "Optional[str]":
    """Return None when both PRODUCT.md and DESIGN.md exist; error message otherwise.

    Both files are required so any frontend ticket dispatched to the coder always
    has a design vocabulary to reference (AC-5, issue #621).
    """
    missing = [
        doc for doc in ("PRODUCT.md", "DESIGN.md")
        if not (Path(cwd_path) / doc).exists()
    ]
    if not missing:
        return None
    return (
        f"Design docs missing in coder worktree ({cwd_path}): {', '.join(missing)}. "
        "Create them (or run `npx impeccable skills install` and scaffold via "
        "`node .github/skills/impeccable/scripts/context.mjs`) before dispatching."
    )


# ── Coder model routing by ticket size (issue #789) ───────────────────────────
# (Routing logic extracted to model_routing.py — issue #1276)


def _build_estimate_paths_block(estimate: Optional[dict]) -> str:
    """Return a 'Start here' paths block from an estimate dict, or '' if none.

    Prefers files_touched over files_likely_affected (issue #1402).
    """
    if not estimate:
        return ""
    paths = estimate.get("files_touched") or estimate.get("files_likely_affected") or []
    if not paths:
        return ""
    header = "Start here — do not broad-search the repo unless these paths are insufficient."
    path_lines = "\n".join(f"  {p}" for p in paths)
    return f"{header}\n\n{path_lines}"


# ── Pre-dispatch doctor (issue #789) ─────────────────────────────────────────

def _doctor_probe_auth(backend: str = "claude-code") -> Optional[str]:
    """Probe coder CLI auth. Returns None on success, error string on failure.

    backend selects which CLI to probe: 'cline' for Cline headless, anything
    else probes the 'claude' CLI (existing behaviour). Result is cached per
    backend for _DOCTOR_AUTH_PROBE_TTL seconds.
    """
    global _DOCTOR_AUTH_LAST_PROBE, _DOCTOR_CLINE_AUTH_LAST_PROBE
    now = time.monotonic()

    if backend == "cline":
        cli = "cline"
        if now - _DOCTOR_CLINE_AUTH_LAST_PROBE < _DOCTOR_AUTH_PROBE_TTL:
            return None
    else:
        cli = "claude"
        if now - _DOCTOR_AUTH_LAST_PROBE < _DOCTOR_AUTH_PROBE_TTL:
            return None

    try:
        result = subprocess.run(
            [cli, "--version"],
            capture_output=True,
            timeout=10,
            text=True,
        )
        if result.returncode != 0:
            return (
                f"{cli} CLI returned non-zero exit on version check "
                f"(rc={result.returncode}): {result.stderr.strip()}"
            )
        if backend == "cline":
            _DOCTOR_CLINE_AUTH_LAST_PROBE = now
        else:
            _DOCTOR_AUTH_LAST_PROBE = now
        return None
    except FileNotFoundError:
        return f"{cli} CLI not found during auth probe"
    except subprocess.TimeoutExpired:
        return f"{cli} CLI timed out during auth probe (>10 s)"
    except Exception as exc:
        return f"{cli} CLI auth probe failed: {exc}"


def _dispatch_doctor(
    cfg: Optional["SprintConfig"],
    alert_modes: list[str],
    issue_num: Optional[int] = None,
    eff_repo: Optional[str] = None,
) -> Optional[str]:
    """Pre-dispatch environment health check (issue #789).

    Checks: CLI present, auth alive (cached), worktree exists, disk space.
    Returns None when healthy. On any failure fires a dispatch-blocked alert
    and returns the error string so the caller can halt without spawning a worker.
    """
    def _fail(err: str) -> str:
        dispatch_alerts(
            alert_modes,
            title=(
                "dispatch-blocked: environment check failed"
                + (f" (issue #{issue_num})" if issue_num else "")
            ),
            body=err,
            issue_num=issue_num,
            category="dispatch-blocked",
            cfg=cfg,
            repo=eff_repo,
        )
        return err

    # 1. Coder CLI present (backend-aware: cline or claude)
    _backend = cfg.coder_backend if cfg is not None else "claude-code"
    _coder_cli = "cline" if _backend == "cline" else "claude"
    if shutil.which(_coder_cli) is None:
        return _fail(f"dispatch-blocked: {_coder_cli} CLI not found in PATH")

    # 2. Auth alive (cached probe, backend-specific)
    auth_err = _doctor_probe_auth(backend=_backend)
    if auth_err:
        return _fail(f"dispatch-blocked: auth check failed — {auth_err}")

    # 3. Coder worktree path exists
    worktree = cfg.worktree_coder if cfg is not None else None
    if worktree is not None and not Path(worktree).exists():
        return _fail(f"dispatch-blocked: coder worktree path does not exist: {worktree}")

    # 4. Disk space above threshold
    check_path = worktree if worktree is not None else Path.cwd()
    try:
        free_bytes = shutil.disk_usage(str(check_path)).free
        if free_bytes < DOCTOR_MIN_DISK_BYTES:
            free_gb = free_bytes / (1024 ** 3)
            need_gb = DOCTOR_MIN_DISK_BYTES / (1024 ** 3)
            return _fail(
                f"dispatch-blocked: low disk space "
                f"({free_gb:.1f} GB free, {need_gb:.0f} GB required)"
            )
    except OSError:
        pass  # can't stat path — don't block on it

    return None  # all checks passed


def _pool_acquire() -> Optional[Path]:
    """Acquire one worktree slot from the active pool (issue #1411).

    Returns the slot path, or None when no pool is active.  Blocks until a
    free slot is available.
    """
    if _ACTIVE_WORKTREE_POOL is not None:
        return _ACTIVE_WORKTREE_POOL.acquire()
    return None


def _pool_release(slot: Optional[Path]) -> None:
    """Return a pool slot acquired by _pool_acquire (issue #1411)."""
    if slot is not None and _ACTIVE_WORKTREE_POOL is not None:
        _ACTIVE_WORKTREE_POOL.release(slot)


def _dispatch_tester(
    issue_num: int,
    alert_modes: list[str],
    sprint_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    chosen_port: Optional[int] = None,
    rate_limit_events: Optional[list] = None,
    on_running: Optional[object] = None,
    sprint_label: Optional[str] = None,
    pre_dispatch_risk: Optional[str] = None,
    prior_failures: Optional[list] = None,
) -> tuple[int, Optional[str]]:
    """Dispatch a tester agent.  Returns (exit_code, failure_category_if_hang).

    When sprint_branch is not 'develop', sets COMMANDER_MERGE_TARGET in the
    subprocess environment so the tester agent merges the feature branch into
    the sprint branch instead of develop (AC2, AC4).

    Retries up to _RATE_LIMIT_MAX_RETRIES times on 429/rate-limit errors with
    exponential backoff.  Appends events to rate_limit_events when provided.

    on_running: optional zero-argument callable invoked immediately after the
    subprocess is spawned (before proc.wait) to signal tester_running status.

    pre_dispatch_risk: optional pre-computed risk tier ("LOW"/"MEDIUM"/"HIGH").
    When omitted, the tier is derived from the issue's GitHub labels via
    _classify_risk_tier() (issue #790).

    prior_failures: accumulated failure records from the fix-loop (mirrors
    _dispatch_coder).  When provided, the tester receives a stronger fast-path
    prompt that directs it to re-verify the previously-failing gate/AC first
    before spot-checking the remaining criteria.  Also sets is_retry=True for
    worktree hygiene so the feature branch is checked out and rebased rather
    than being treated as a fresh ticket.
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    api_url  = cfg.api_url if cfg else None
    cwd_path = cfg.worktree_tester_app if cfg else WORKTESTER_DASHBOARD
    # Git root for the tester worktree (hygiene needs the repo root, not a subdir).
    _tester_wt_root = cfg.worktree_tester if cfg else WORKTESTER_ROOT

    # Worktree hygiene (issue #788 AC7): same treatment as coder — fetch, stash dirty
    # state, hard-reset — before the tester checks out any code.
    # When prior_failures is non-empty this is a fix-round retry; set is_retry=True so
    # the hygiene step checks out the existing feature branch and rebases rather than
    # treating the ticket as fresh (mirrors _dispatch_coder behaviour).
    _tester_is_retry = bool(prior_failures)
    _tester_wt_sha, _tester_base_sha, _tester_hygiene_err = _worktree_hygiene(
        worktree=_tester_wt_root,
        ticket_id=issue_num,
        merge_target=sprint_branch,
        is_retry=_tester_is_retry,
    )
    if sprint_label:
        _db_update_worktree_shas_sm(issue_num, sprint_label, "tester", _tester_wt_sha, _tester_base_sha)
    if _tester_hygiene_err:
        structured_log.error(
            "tester_worktree_hygiene_failed",
            f"[tester] worktree hygiene blocked dispatch for issue #{issue_num}: {_tester_hygiene_err}",
            issue_num=issue_num,
            hygiene_error=_tester_hygiene_err,
        )
        return 1, _tester_hygiene_err

    _crg_update_worktree(_tester_wt_root, role="tester")

    sys.stdout.write(str(f"  Dispatching tester for issue #{issue_num} ...") + "\n")
    sys.stdout.flush()
    try:
        structured_log.event(
            "tester.dispatch",
            run_id=os.environ.get("COMMANDER_RUN_ID"),
            issue_num=issue_num,
            sprint_label=sprint_label,
            agent_role="tester",
        )
    except Exception:
        pass
    _post_agent_event(f"tester:issue-{issue_num}", api_url=api_url)

    log_path = _issue_log_path(issue_num, cfg=cfg)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")

    # Build prompt
    if cfg and cfg.tester_prompt_template:
        issue_url = f"https://github.com/{_r(eff_repo)}/issues/{issue_num}"
        prompt = cfg.tester_prompt_template.format(issue_url=issue_url)
    else:
        prompt = (
            f"You are running in autonomous sprint mode. "
            f"Read the issue at https://github.com/{_r(eff_repo)}/issues/{issue_num} "
            "and verify it as a tester following the project's testing workflow. "
            "Use the BA/coder/tester workflow defined in CLAUDE.md. "
        )

    # Always inject autonomous enforcement — custom templates omit this, so append
    # unconditionally unless the template already references finish_feature.
    if "finish_feature" not in prompt:
        prompt += (
            " IMPORTANT — autonomous sprint mode: when your verdict is READY_FOR_UAT"
            " exit with code 0. Do NOT run finish_feature.py or merge the branch —"
            " sprint_manager runs quality gates after you exit, then merges via"
            " finish_feature.py and applies the UAT label."
            " NEVER apply the UAT-approved label or close the issue — UAT-approved is set ONLY by the human"
            " via the dashboard Approve button or scripts/approve_ticket.py."
            " Do NOT output language like 'let me know if you want me to...' —"
            " complete testing autonomously and stop once READY_FOR_UAT is reached."
            # AC-1 / AC-2 (issue #311): explicit prohibition of direct merge paths
            " MERGE PATH ENFORCEMENT (issue #311): you must NOT merge. You are FORBIDDEN"
            " from running finish_feature.py, `git merge`, opening or merging a PR directly,"
            " or pushing commits to the target branch. Merging is sprint_manager's job"
            " after gates pass. Violating this constitutes a workflow failure — halt and report."
        )
    # Label boundary (issue #509): tester must never touch GitHub labels.
    if "DO NOT modify any GitHub label" not in prompt:
        prompt += (
            " DO NOT modify any GitHub label on this issue or any other issue."
            " Label transitions are managed by sprint_manager."
            " Do not run update_ticket.py, gh issue edit --add-label, or any other"
            " label-mutation command."
        )
    # Single pytest run: the tester still WRITES the pytest tests for each AC and
    # verifies the UAT steps, but does NOT execute the pytest suite itself — the
    # sprint_manager pytest gate runs it once after the tester exits. Avoids the
    # double pytest run (tester + gate). Toggle off by setting
    # COMMANDER_TESTER_RUN_PYTEST=1 (then the tester runs pytest too).
    if os.environ.get("COMMANDER_TESTER_RUN_PYTEST", "").strip().lower() not in ("1", "true", "yes", "on"):
        if "do not execute the pytest" not in prompt.lower():
            prompt += (
                " PYTEST EXECUTION: write a pytest test for each acceptance criterion,"
                " but do NOT execute the pytest suite yourself — sprint_manager's"
                " quality gate runs pytest once after you exit. Verify the UAT steps"
                " (HTTP / browser / inspection), confirm the tests you wrote are"
                " anchored to their AC, then report READY_FOR_UAT; the gate validates"
                " that the tests pass."
            )

    # Re-run fast path: if a prior attempt failed, point the tester at the
    # previously-failing AC / gate FIRST so a re-verify isn't a full from-scratch
    # pass.  When prior_failures is supplied (fix-loop path) inject a stronger
    # accumulated-history prompt; fall back to the sidecar file for single-attempt
    # re-runs where prior_failures is not passed.
    if prior_failures:
        # Fix-round FAST PATH: accumulated history from the fix-loop.
        _fp_lines = [
            f"\n\nTESTER FIX-ROUND FAST PATH: this is fix-round attempt"
            f" {len(prior_failures) + 1}. Prior coder/gate attempts failed:"
        ]
        for _h in prior_failures:
            _cat = _h.get("category", "?")
            _detail = _h.get("reason") or _h.get("summary") or ""
            _fp_lines.append(f"  - Attempt {_h.get('attempt', 0) + 1}: {_cat}: {_detail}")
        _fp_lines.append(
            "TESTER FOCUS: re-verify the acceptance criterion / gate tied to the"
            " LAST failure above FIRST. If it now passes, quickly spot-check the"
            " remaining AC rather than re-testing everything from scratch."
            " Do NOT rewrite existing passing tests."
        )
        prompt += "\n".join(_fp_lines)
    else:
        try:
            _sc = REPO_ROOT / ".commander" / "runtime" / f"last-failure-{issue_num}.json"
            if _sc.exists():
                _scd = json.loads(_sc.read_text(encoding="utf-8"))
                _fc = _scd.get("failure_class") or _scd.get("category")
                if _fc:
                    prompt += (
                        f" RE-RUN FAST PATH: a prior attempt failed ({_fc}). Re-verify the"
                        " acceptance criterion / gate tied to that failure FIRST; if it now"
                        " passes, quickly confirm the remaining AC rather than re-testing"
                        " everything from scratch."
                    )
        except Exception:
            pass

    # Impeccable design context (issue #713): inject into every tester dispatch so
    # the tester verifies UI tickets against the same design rules via context.mjs.
    if "context.mjs" not in prompt:
        prompt += _impeccable_context_instruction()

    # ── Risk-tier model routing (issue #790) ──────────────────────────────────
    # Derive risk before dispatch so the model is selected before the subprocess
    # is spawned.  Use the caller-supplied tier if given, otherwise classify from
    # the issue's current GitHub labels (best-effort: defaults to LOW on error).
    if pre_dispatch_risk is None:
        try:
            issue_labels = list(_get_issue_labels(issue_num, repo_name=eff_repo))
        except Exception:
            issue_labels = []
        pre_dispatch_risk = _classify_risk_tier(labels=issue_labels)

    risk_tier = pre_dispatch_risk.upper() if pre_dispatch_risk else "LOW"
    _by_risk = (cfg.tester_by_risk if cfg is not None else None) or {
        "LOW":    "claude-haiku-4-5",
        "MEDIUM": "claude-haiku-4-5",
        "HIGH":   "claude-sonnet-4-6",
    }
    tester_model = _by_risk.get(risk_tier) or (
        cfg.tester_model if cfg is not None else "claude-sonnet-4-6"
    )
    sys.stdout.write(str(f"  [risk-tier] issue #{issue_num}: risk={risk_tier}, model={tester_model}") + "\n")
    sys.stdout.flush()

    # Inject the pre-dispatch risk tier into the prompt so the tester knows what
    # was computed before invocation and can use it as a baseline (AC3).  The
    # tester's own derivation acts as a fallback / sanity check.
    prompt += (
        f" PRE-DISPATCH RISK TIER (issue #790): the sprint_manager classified"
        f" this ticket as risk={risk_tier} before invocation."
        f" If your own analysis produces a different risk tier, output a line"
        f" '[risk-tier] <YOUR_TIER>' (e.g. '[risk-tier] MEDIUM') anywhere in"
        f" your run output so the disagreement can be detected and logged."
    )

    # Same persona fix as the coder: load the tester subagent for the headless run.
    cmd = [
        "claude",
        "--model", tester_model,
        "--dangerously-skip-permissions",
    ]
    tester_persona = _load_agent_persona("tester", cwd_path)
    if tester_persona:
        cmd += ["--append-system-prompt", tester_persona]
    cmd += ["-p", prompt]

    # Build subprocess environment: inherit current env, set COMMANDER_MERGE_TARGET
    # when in sprint mode (AC2), COMMANDER_APP_PORT if a port was chosen (issue #62),
    # COMMANDER_PROJECT so log output is tagged by project (issue #122), and
    # COMMANDER_SPRINT_RUNNING so child scripts enforce RUN_MUTABLE_LABELS (issue #506).
    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    sub_env.update(_agent_identity_env("tester", issue_num))  # tag hooks/telemetry as the docs prescribe
    sub_env["CLAUDE_MODEL"] = tester_model  # hook records model_name on token_usage rows
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo
    if sprint_label:
        sub_env["COMMANDER_SPRINT_RUNNING"] = sprint_label
    if sprint_branch not in ("develop",):
        sub_env["COMMANDER_MERGE_TARGET"] = sprint_branch
        # Always append sprint-mode instructions regardless of whether a custom
        # tester_prompt_template is configured (issue #72 regression fix).
        sprint_hint = (
            f" IMPORTANT: The env var COMMANDER_MERGE_TARGET is set to {sprint_branch!r}."
            f" When running finish_feature.py, pass --target-branch {sprint_branch!r}"
            f" so that the feature branch merges into {sprint_branch!r} instead of develop."
        )
        cmd[-1] = cmd[-1] + sprint_hint
    if chosen_port is not None:
        sub_env["COMMANDER_APP_PORT"] = str(chosen_port)
        sys.stdout.write(str(f"  [port] COMMANDER_APP_PORT={chosen_port} injected into tester env") + "\n")

    uat_env_vars, uat_err = _resolve_uat_env_for_tester(cfg, cwd_path)
    if uat_err:
        sys.stdout.write(str(f"  [uat-env] ERROR: {uat_err}") + "\n")
        sys.stdout.flush()
        record_failure(
            int(issue_num),
            "uat-env",
            detail=uat_err,
            repo_root=_tester_wt_root,
        )
        return 1, "uat-env"
    if uat_env_vars:
        sub_env.update(uat_env_vars)
        cmd[-1] += (
            f" UAT PRE-VALIDATED (sprint_manager Step 0):"
            f" UAT_BASE_URL={uat_env_vars['UAT_BASE_URL']!r},"
            f" UAT_PORT={uat_env_vars['UAT_PORT']!r},"
            f" UAT_REPO={uat_env_vars['UAT_REPO']!r}."
            f" These env vars are already set — do NOT re-run Step 0 bash guards"
            f" and do NOT refuse based on ENVIRONMENT= in .env."
            f" Proceed directly to Step 1."
        )

    # ── GITHUB_ISSUE_TEST_REPO injection (issue #301) ─────────────────────────
    # Read GITHUB_ISSUE_TEST_REPO at tester-dispatch time and inject it into the
    # tester's environment along with an explicit prompt distinction between the
    # work repo (GITHUB_REPO / eff_repo) and the issue-test target repo.
    issue_test_repo = os.environ.get("GITHUB_ISSUE_TEST_REPO", "").strip()
    if issue_test_repo:
        sub_env["GITHUB_ISSUE_TEST_REPO"] = issue_test_repo
        test_repo_hint = (
            f" GITHUB REPO SEGREGATION (issue #301):"
            f" GITHUB_ISSUE_TEST_REPO is set to {issue_test_repo!r}."
            f" The work repo is {eff_repo!r} (all coder commits, sprint issues, and branch operations stay here)."
            f" Any UAT step that creates a GitHub issue or applies/removes labels MUST target"
            f" GITHUB_ISSUE_TEST_REPO ({issue_test_repo!r}), NOT the work repo ({eff_repo!r})."
            f" Use GITHUB_ISSUE_TEST_REPO as the --repo argument for any `gh issue create` or label operations."
        )
        cmd[-1] = cmd[-1] + test_repo_hint
        sys.stdout.write(str(f"  [issue-test-repo] GITHUB_ISSUE_TEST_REPO={issue_test_repo!r} injected into tester env") + "\n")
    else:
        # GITHUB_ISSUE_TEST_REPO not set — tester must skip live issue/label tests
        test_repo_hint = (
            " GITHUB REPO SEGREGATION (issue #301):"
            " GITHUB_ISSUE_TEST_REPO is NOT set."
            " Any UAT step that would create a GitHub issue or apply/remove labels on a repo"
            " MUST be skipped — do NOT perform those operations against the work repo."
            " Include exactly the note"
            " \"GITHUB_ISSUE_TEST_REPO not configured — skipped live issue/label verification.\""
            " in the test report for each skipped step."
        )
        cmd[-1] = cmd[-1] + test_repo_hint
        sys.stdout.write(str("  [issue-test-repo] GITHUB_ISSUE_TEST_REPO not set — tester will skip live issue/label tests") + "\n")

    # ── agent-browser injection (issue #710) ──────────────────────────────────
    # Make the optional live-browser UAT runner available to the tester and tell
    # Step 6 how to route browser-interaction UAT steps. We probe availability
    # here so the prompt can state whether real browser runs are possible or
    # everything will fall back to MANUAL. The probe is best-effort — any failure
    # degrades to "not available" so dispatch never crashes on the runner.
    try:
        browser_available = agent_browser_runner.is_available()
    except Exception:
        browser_available = False
    sub_env["COMMANDER_AGENT_BROWSER_AVAILABLE"] = "1" if browser_available else "0"
    sub_env.setdefault(
        "AGENT_BROWSER_SCREENSHOT_DIR", str(agent_browser_runner.SCREENSHOT_DIR)
    )
    browser_hint = (
        " LIVE BROWSER UAT (issue #710): In Step 6, route each UAT step with"
        " services/sprint_manager/agent_browser_runner.py."
        " If the step is flagged agent-testable OR its text describes a browser"
        " interaction (keywords: open, navigate, click, see, expect, page),"
        " execute it via agent_browser_runner.run_browser_step(step_text, base_url)"
        " instead of marking MANUAL. Record the result as PASS or FAIL in the test"
        " report with the returned screenshot_path attached. A FAIL browser step"
        " sets the overall ticket status to NEEDS_FIXES, identical to a failed AC."
        " Mark a step MANUAL ONLY when the runner returns status 'uncovered' or"
        " agent_browser_runner.is_available() is False. HTTP-only UAT steps"
        " continue to run via httpx unchanged."
        f" COMMANDER_AGENT_BROWSER_AVAILABLE={'1' if browser_available else '0'} in your env."
        " SCREENSHOT CAPTURE (issue #712): for browser steps, save each step's"
        " screenshot via a single agent_browser_runner.ScreenshotRecorder(sprints_dir,"
        " sprint_num, issue_num) — call recorder.capture(screenshot_path) once per"
        " browser step in order (it caps resolution, skips consecutive duplicates,"
        " and names files step-<k>.png under"
        " .commander/sprints/sprint-<N>/screenshots/issue-<N>/). After the run, if"
        " recorder.saved is non-empty, call"
        " agent_browser_runner.upload_screenshots(recorder.saved, repo, issue_num,"
        " sprint_num) to get a {filename: url} map, then append"
        " agent_browser_runner.build_screenshot_section("
        " agent_browser_runner.collect_issue_screenshots(sprints_dir, sprint_num,"
        " issue_num, url_map=urls)) to the test report markdown before posting. If a"
        " ticket produced zero browser steps, add NO screenshot section. The whole"
        " capture/upload/embed flow is best-effort — never let it abort the test run"
        " or report posting."
    )
    cmd[-1] = cmd[-1] + browser_hint
    sys.stdout.write(str(f"  [agent-browser] available={browser_available} injected into tester env") + "\n")

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        _dispatch_t0 = time.monotonic()
        structured_log.info(
            "dispatch_start", f"tester dispatch #{issue_num} (attempt {attempt + 1})",
            issue_num=issue_num, agent_role="tester", sprint_label=sprint_label,
            attempt=attempt + 1, model=tester_model, cmd=cmd[:4],
        )
        try:
            with log_path.open("a") as log_f:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=log_f,
                    cwd=str(cwd_path),
                    env=sub_env,
                )
        except FileNotFoundError:
            _allow_stub = os.environ.get("COMMANDER_ALLOW_STUB_SUCCESS", "") == "1"
            if _allow_stub:
                sys.stdout.write(str("  [tester] claude CLI not found -- stub success") + "\n")
                if on_running is not None:
                    try:
                        on_running()
                    except Exception:
                        pass
                return 0, None
            err_msg = (
                f"[tester] ERROR: claude CLI not found for issue #{issue_num}.\n"
                f"PATH={sub_env.get('PATH', '<empty>')}\n"
                "Sprint cannot proceed. Install claude CLI or set COMMANDER_ALLOW_STUB_SUCCESS=1 for testing.\n"
            )
            structured_log.error("claude_cli_not_found", f"claude CLI not found for issue #{issue_num}", issue_num=issue_num, subprocess="tester", path=sub_env.get("PATH", ""))
            try:
                with log_path.open("a") as lf:
                    lf.write(err_msg)
            except OSError:
                pass
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{issue_num}: claude CLI not found",
                body=f"_dispatch_tester failed to spawn 'claude' subprocess: file not found. PATH={sub_env.get('PATH', '<empty>')}. Sprint cannot proceed.",
                issue_num=issue_num,
                category=FailureCategory.CRASH,
                cfg=cfg,
                repo=eff_repo,
            )
            return -1, FailureCategory.CRASH

        if on_running is not None:
            try:
                on_running()
            except Exception:
                pass

        detector = HangDetector(issue_num=issue_num, log_path=log_path, proc=proc,
                                 agent_role="tester", attempt=attempt + 1)
        detector.start()
        rc = proc.wait()
        detector.stop()

        _dispatch_secs = round(time.monotonic() - _dispatch_t0, 1)
        if rc == 0:
            structured_log.info(
                "dispatch_finished", f"tester #{issue_num} finished",
                issue_num=issue_num, agent_role="tester", sprint_label=sprint_label,
                attempt=attempt + 1, exit_code=0, duration_s=_dispatch_secs,
            )
        else:
            _stderr_tail = ""
            try:
                _stderr_tail = log_path.read_text(encoding="utf-8", errors="replace")[-500:]
            except Exception:
                pass
            structured_log.error(
                "dispatch_failed", f"tester #{issue_num} exited {rc}",
                issue_num=issue_num, agent_role="tester", sprint_label=sprint_label,
                attempt=attempt + 1, exit_code=rc, duration_s=_dispatch_secs,
                stderr_tail=_stderr_tail,
            )

        # Exit code 0 means success unconditionally — check before detector.killed
        # to guard against a race where the hang detector fires after the process
        # already exited cleanly (proc.kill raises ProcessLookupError but still
        # sets _killed=True, causing a false HANG failure on exit 0).
        if rc == 0:
            sys.stdout.write(str(f"  Tester for issue #{issue_num} exited 0 — status: passed") + "\n")
            sys.stdout.flush()
            # Check for risk-tier disagreement (AC4, issue #790) — non-fatal.
            try:
                _check_risk_disagreement(risk_tier, log_path, issue_num)
            except Exception:
                pass
            return 0, None

        if detector.killed:
            reason = f"Tester: no log activity for {HANG_KILL_SECS//60} minutes"
            _add_blocked_label(issue_num, reason, repo_name=eff_repo, sprint_label=sprint_label)
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{issue_num}: HANG detected in tester",
                body=f"The tester subprocess produced no output for {HANG_KILL_SECS//60} minutes.",
                issue_num=issue_num,
                category=FailureCategory.HANG,
                cfg=cfg,
                repo=eff_repo,
            )
            return -1, FailureCategory.HANG

        # Non-zero exit: inspect log for rate-limit signal
        log_content = ""
        if log_path.exists():
            try:
                log_content = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

        is_rl, retry_after = _is_rate_limit_error(log_content)

        if is_rl and attempt < _RATE_LIMIT_MAX_RETRIES:
            delay = retry_after if retry_after is not None else _RATE_LIMIT_BACKOFF_DELAYS[attempt]
            retry_num = attempt + 1
            sys.stdout.write(str(f"  Rate limit hit, retrying in {delay} seconds (attempt {retry_num}/{_RATE_LIMIT_MAX_RETRIES})") + "\n")
            sys.stdout.flush()
            if rate_limit_events is not None:
                rate_limit_events.append({
                    "issue_num": issue_num,
                    "role": "tester",
                    "attempt": retry_num,
                    "delay_secs": delay,
                    "timestamp": _utcnow(),
                })
            time.sleep(delay)
            continue

        if is_rl:
            sys.stdout.write(str(f"  Subscription rate limit exhausted for tester issue #{issue_num} after {_RATE_LIMIT_MAX_RETRIES} retries") + "\n")
            sys.stdout.flush()
            if rate_limit_events is not None:
                rate_limit_events.append({
                    "issue_num": issue_num,
                    "role": "tester",
                    "attempt": _RATE_LIMIT_MAX_RETRIES,
                    "delay_secs": 0,
                    "exhausted": True,
                    "timestamp": _utcnow(),
                })
            return rc, FailureCategory.RETRY_EXHAUSTED

        return rc, None

    # Should not be reached
    return rc, None


# ── sprint summary report — extracted to summary.py (issue #1287) ────────────
# LEARNINGS_STUB, _follow_up_action, _build_screenshots_section,
# _load_screenshot_url_map, generate_sprint_summary, create_summary_github_issue,
# _prompt_learnings, _is_stale_summary, and write_sprint_summary are now in
# services/sprint_manager/summary.py and re-imported above (search for
# "Summary generation helpers extracted").



def _ensure_github_labels(labels: list[str], repo_name: Optional[str] = None) -> None:
    """Create GitHub labels if they don't exist (best-effort, AC-2)."""
    r = _r(repo_name)
    for label in labels:
        try:
            subprocess.run(
                ["gh", "label", "create", label, "--repo", r, "--force"],
                capture_output=True, text=True, check=False,
            )
        except Exception:
            pass


def _close_cancelled_sprint_summary(
    sprint_number: Optional[int],
    sprint_label: str,
    repo_name: Optional[str] = None,
) -> None:
    """Close any open sprint summary issue created before cancellation arrived (AC-4 issue #365).

    Called from main()'s SystemExit handler when _sprint_user_cancelled is True.
    Searches by title so it catches issues created in the same run even if their
    number/URL was never persisted to the state JSON.
    """
    n = _summary_sprint_display(sprint_label, sprint_number)
    title = f"Sprint {n} Executive Summary"
    try:
        existing = github_client.search_issues_by_title(title, repo_name=repo_name)
    except Exception as exc:
        structured_log.warn(
            "cancel_summary_search_failed",
            f"could not search for summary issue to close: {exc}",
            exc=str(exc),
        )
        return
    for issue in existing:
        if issue.get("state") == "open":
            num = issue.get("number")
            try:
                github_client.add_comment(
                    num,
                    "Sprint was cancelled; summary not applicable",
                    repo_name=repo_name,
                )
                github_client.close_issue(num, repo_name=repo_name)
                sys.stdout.write(str(f"  [cancel] Closed summary issue #{num} — sprint was cancelled.") + "\n")
            except Exception as exc:
                structured_log.warn(
                    "cancel_summary_close_failed",
                    f"could not close summary issue #{num}: {exc}",
                    issue_num=num,
                    exc=str(exc),
                )


# ── Issue Estimator integration ───────────────────────────────────────────────

SERIOUS_RISK_FLAGS = {"touches-db-schema", "security-sensitive", "breaks-tests"}

# Labels that unconditionally elevate risk to HIGH (issue #790).
_HIGH_RISK_LABELS: frozenset[str] = frozenset({
    "security", "security-sensitive", "auth", "authentication", "crypto",
})

# Path fragments whose presence in any touched file elevates risk to HIGH.
_HIGH_RISK_PATH_FRAGMENTS: tuple[str, ...] = (
    "auth", "security", "credential", "password", "token", "crypto", "oauth",
)

# Diff line counts thresholds for risk elevation.
_MEDIUM_DIFF_THRESHOLD = 300   # lines → MEDIUM
_HIGH_DIFF_THRESHOLD   = 800   # lines → HIGH


def _classify_risk_tier(
    labels: list[str],
    diff_lines: int = 0,
    paths_touched: list[str] | None = None,
) -> str:
    """Derive a risk tier (LOW / MEDIUM / HIGH) from ticket metadata (issue #790).

    Signals evaluated in descending priority:
    1. HIGH-risk label present → HIGH
    2. Security-sensitive path touched → HIGH
    3. Diff size >= HIGH threshold → HIGH
    4. Diff size >= MEDIUM threshold → MEDIUM
    5. Otherwise → LOW
    """
    label_set = {lbl.lower() for lbl in labels}
    if label_set & {lbl_r.lower() for lbl_r in _HIGH_RISK_LABELS}:
        return "HIGH"

    if paths_touched:
        for path in paths_touched:
            lower_path = path.lower()
            if any(frag in lower_path for frag in _HIGH_RISK_PATH_FRAGMENTS):
                return "HIGH"

    if diff_lines >= _HIGH_DIFF_THRESHOLD:
        return "HIGH"
    if diff_lines >= _MEDIUM_DIFF_THRESHOLD:
        return "MEDIUM"

    return "LOW"


def _check_risk_disagreement(
    pre_dispatch_risk: str,
    log_path: "Path",
    issue_num: int,
) -> None:
    """Parse tester log for a self-reported [risk-tier] marker; log if it
    disagrees with `pre_dispatch_risk`. Non-fatal — never raises (issue #790).
    """
    _MARKER = "[risk-tier]"
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    tester_risk: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_MARKER):
            candidate = stripped[len(_MARKER):].strip().upper()
            if candidate in ("LOW", "MEDIUM", "HIGH"):
                tester_risk = candidate
                break

    if tester_risk is not None and tester_risk != pre_dispatch_risk.upper():
        sys.stdout.write(str(f"  [risk-tier] disagreement for issue #{issue_num}: "
            f"pre-dispatch={pre_dispatch_risk}, tester-derived={tester_risk}") + "\n")
        sys.stdout.flush()


def _load_estimate(issue_num: int) -> Optional[dict]:
    """Load .commander/estimates/issue-<N>.json by walking up from REPO_ROOT."""
    current = REPO_ROOT.resolve()
    while True:
        estimate_path = current / ".commander" / "estimates" / f"issue-{issue_num}.json"
        if estimate_path.exists():
            try:
                return json.loads(estimate_path.read_text())
            except (json.JSONDecodeError, OSError):
                return None
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _bump_estimate_size(issue_num: int) -> Optional[str]:
    """Bump a ticket's cached size one tier (S→M→L→XL) on a fix-round, so the
    budget/forecast and model-routing reflect that a failed ticket is bigger than
    first sized (resume-from-failure TODO). Returns the new size, or None when
    there's no estimate or it's already XL."""
    _order = ["S", "M", "L", "XL"]
    current = REPO_ROOT.resolve()
    while True:
        p = current / ".commander" / "estimates" / f"issue-{issue_num}.json"
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                return None
            sz = str(data.get("size") or "").upper()
            if sz in _order and _order.index(sz) < len(_order) - 1:
                new_sz = _order[_order.index(sz) + 1]
                data["size"] = new_sz
                data["size_bumped_on_failure"] = True
                try:
                    p.write_text(json.dumps(data, indent=2))
                    return new_sz
                except OSError:
                    return None
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _load_sprint_plan(sprints_dir: Path, label: str) -> Optional[list[int]]:
    """Read {label}-plan.json; return issue-number list or None on failure.

    Handles both old list format ([42, 17, 88]) and new dict format
    ({"state": "...", "tickets": [42, 17, 88], ...}).
    """
    path = sprints_dir / f"{label}-plan.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and all(isinstance(n, int) for n in data):
            return data
        if isinstance(data, dict):
            tickets = data.get("tickets")
            if isinstance(tickets, list) and all(isinstance(n, int) for n in tickets):
                return tickets
    except (OSError, json.JSONDecodeError):
        pass
    return None


# _build_sprint_dag_layers, _compute_dispatch_levels, _warn_file_conflicts
# extracted to pipeline.py (issue #1289); re-imported above.


# -- GitHub issue listing --

def _classify(labels: set[str]) -> str:
    if "UAT-approved" in labels:
        return "done"
    if "UAT" in labels:
        return "uat"
    if "SIT" in labels:
        return "sit"
    if "in-progress" in labels:
        return "in-progress"
    return "backlog"


_REWORK_LABELS = frozenset({"needs-rework", "need-rework", "tester-rejected"})


def _is_dispatchable(labels: set[str]) -> bool:
    """True when an open issue on a sprint label should be picked up for a run.

    Re-run sub-sprints often carry SIT / in-progress / needs-rework from a prior
    attempt; treating only pure backlog tickets as dispatchable caused instant
    no-op runs (``No backlog issues found``) on labels like sprint-68.3.
    """
    cls = _classify(labels)
    if cls in ("backlog", "sit", "in-progress"):
        return True
    return bool(labels & _REWORK_LABELS)


def _list_labeled_open_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return all open issues carrying ``label``, excluding sprint-summary docs."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", r,
                "--label", label,
                "--state", "open",
                "--json", "number,title,labels",
                "--limit", "200",
            ],
            capture_output=True, text=True, check=True,
        )
        issues = json.loads(out.stdout)
        result = []
        for issue in issues:
            labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
            is_summary = (
                "sprint-summary" in labels_set
                or bool(_SUMMARY_TITLE_RE.match(issue.get("title", "") or ""))
            )
            if is_summary:
                structured_log.info(
                    "dispatch_skipped_summary",
                    f"skipping summary ticket #{issue['number']} from backlog",
                    issue_num=issue["number"],
                )
                continue
            result.append(issue)
        return sorted(result, key=lambda i: i["number"])
    except Exception as e:
        structured_log.warn("list_issues_failed", f"could not list issues: {e}", label=label, exc=str(e))
        return []


# list_backlog_issues extracted to pipeline.py (issue #1289); re-imported above.


def _issues_from_plan_numbers(
    label: str,
    plan_numbers: list[int],
    repo_name: Optional[str] = None,
) -> list[dict]:
    """Resolve plan.json ticket numbers to labeled GitHub issues when gh list is empty."""
    if not plan_numbers:
        return []
    by_num = {i["number"]: i for i in _list_labeled_open_issues(label, repo_name=repo_name)}
    result: list[dict] = []
    for num in plan_numbers:
        issue = by_num.get(num)
        if not issue:
            continue
        labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
        if _classify(labels_set) in ("uat", "done"):
            continue
        if _is_dispatchable(labels_set):
            result.append(issue)
    return result


# ── Sprint state JSON mirror helpers ────────────────────────────────────────────
# Issue #758 removed the Neon dual-write. These helpers now only maintain the
# local sprint state JSON ({label}.json), which the dashboard reads directly.

def _neon_sprint_json_path(sprint_label: str, sprints_dir: Path) -> Path:
    return sprints_dir / f"{sprint_label}.json"


def _neon_sprint_json_write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except OSError as _e:
        structured_log.error("sprint_json_write_error", f"could not write {path}: {_e}", path=str(path), exc=str(_e))


def _neon_sprint_json_read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _neon_sprint_init(
    label: str,
    issues: "list[IssueState]",
    project: str,
    sprints_dir: Path,
) -> None:
    """Write the sprint state JSON ({label}.json) on a fresh start (issue #758:
    the Neon sprint/ticket creation was removed — local JSON only)."""
    goal = os.environ.get("SPRINT_GOAL", "").strip()
    if not goal:
        goal_file = sprints_dir / f"{label}-goal.txt"
        if goal_file.exists():
            try:
                goal = goal_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass
    if not goal:
        goal = label

    # Write JSON mirror.
    json_path = _neon_sprint_json_path(label, sprints_dir)
    _neon_sprint_json_write(json_path, {
        "label": label,
        "goal": goal,
        "project": project,
        "status": "running",
        "tickets": [
            {"issue_number": s.number, "position": i, "status": "pending"}
            for i, s in enumerate(issues)
        ],
    })


def _neon_ticket_status(
    sprint_label: str,
    issue_number: int,
    neon_status: str,
    sprints_dir: Path,
    total_tokens: int = 0,
) -> None:
    """Patch a ticket's status in the sprint state JSON (issue #758: Neon write
    removed; `total_tokens` retained for call-site compatibility)."""
    json_path = _neon_sprint_json_path(sprint_label, sprints_dir)
    data = _neon_sprint_json_read(json_path)
    if "tickets" in data:
        for t in data["tickets"]:
            if t.get("issue_number") == issue_number:
                t["status"] = neon_status
                break
        _neon_sprint_json_write(json_path, data)


def _regenerate_status_md(cfg: Optional["SprintConfig"], dry_run: bool = False) -> None:
    """Regenerate STATUS.md at the project root after sprint completes (#584)."""
    if dry_run:
        return
    script = SCRIPTS_DIR / "generate_status.py"
    if not script.exists():
        structured_log.warning("status_md_skip", "generate_status.py not found; skipping STATUS.md regeneration")
        return
    try:
        subprocess.run(
            [sys.executable, str(script)],
            check=False,
            timeout=60,
        )
    except Exception as exc:
        structured_log.warning("status_md_error", f"STATUS.md regeneration failed: {exc}")


def _neon_sprint_status(sprint_label: str, neon_status: str, sprints_dir: Path) -> None:
    """Patch the sprint status in the sprint state JSON (issue #758: Neon write removed)."""
    json_path = _neon_sprint_json_path(sprint_label, sprints_dir)
    data = _neon_sprint_json_read(json_path)
    if data:
        data["status"] = neon_status
        _neon_sprint_json_write(json_path, data)


# _run_pipeline_dispatch extracted to pipeline.py (issue #1289); re-imported above.

# ── sprint loop ────────────────────────────────────────────────────────────────


@dataclasses.dataclass
class _SprintPreflightResult:
    """Structured result returned by run_sprint_preflight."""
    state: "SprintState"
    state_path: "Path"
    summary: "SprintSummary"
    sprint_num: Optional[int]
    sprint_branch: str
    target_branch: str
    eff_repo: Optional[str]
    api_url: Optional[str]
    run_id: str
    rerun_decisions: dict
    eff_sprints_dir: "Path"
    dispatch_levels: list
    level_nums_by_idx: list
    pipeline_on: bool
    start_time: float
    early_exit: bool = False


def run_sprint_preflight(
    label: str,
    alert_modes: list,
    repo_name: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = False,
    retry_failed: bool = False,
    target_branch: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    token_budget: int = 0,
    skip_estimator: bool = True,
    rerun_manifest: Optional[dict] = None,
    pipeline_mode: Optional[bool] = None,
    max_coder_slots: Optional[int] = None,
    max_tester_slots: Optional[int] = None,
) -> _SprintPreflightResult:
    """Preflight and branch-setup phase for run_sprint.

    Runs all pre-dispatch preparation: environment setup, state loading/building,
    sprint branch creation, estimator, DAG ordering, worktree pool initialisation,
    and pipeline-mode resolution.  Returns a _SprintPreflightResult carrying every
    local variable that the dispatch loop in run_sprint needs.

    When early_exit is True on the returned result, the caller must immediately
    return (result.summary, result.state) without entering the dispatch loop.
    """
    # Effective repo: explicit arg > config > github_client
    eff_repo   = repo_name or (cfg.repo_name if cfg else None)
    api_url    = cfg.api_url if cfg else None
    # Tag every agent_runs row written during this run with its project so the
    # timeline/run-stats/history stop mixing same-labelled sprints across repos.
    global _CURRENT_RUN_PROJECT
    _CURRENT_RUN_PROJECT = eff_repo

    summary    = SprintSummary()
    sprint_num = _sprint_number(label)
    state_path = _state_path(sprint_num, label, cfg=cfg)

    # Mint run_id before any agent work; propagates to subprocesses via COMMANDER_RUN_ID
    _sprint_num_str = str(sprint_num) if sprint_num is not None else label.replace("sprint-", "")
    _run_id = mint_run_id("sprint", _sprint_num_str)
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="sprint", sprint_label=label, project=eff_repo)

    # AC-2: write PID file and register cleanup handlers
    if not dry_run:
        _setup_pid_file(sprint_num)

    # Log config info when running against a second repo
    if cfg and cfg.repo_name:
        sys.stdout.write(str("\n=== SprintConfig ===") + "\n")
        sys.stdout.write(str(f"  repo:         {cfg.repo_name}") + "\n")
        sys.stdout.write(str(f"  coder-wt:     {cfg.worktree_coder}") + "\n")
        sys.stdout.write(str(f"  tester-wt:    {cfg.worktree_tester}") + "\n")
        sys.stdout.write(str(f"  tester-app:   {cfg.worktree_tester_app}") + "\n")
        sys.stdout.write(str(f"  logs-dir:     {cfg.logs_dir}") + "\n")
        sys.stdout.write(str(f"  sprints-dir:  {cfg.sprints_dir}") + "\n")
        sys.stdout.write(str(f"  api-url:      {cfg.api_url}") + "\n")

    # Determine the sprint branch name and per-ticket merge target.
    # Child sprint branches (sprint/sprint-N.M) are created off the base branch;
    # per-ticket merges land on THIS sprint's branch (gates fail → no merge).
    # Passing --target-branch develop explicitly is still supported as a
    # deliberate override (AC-5 of issue #269).
    sprint_branch = _sprint_branch_for_label(label)
    base_merge_target = _base_sprint_branch(label)
    if target_branch is None:
        target_branch = sprint_branch

    # Build rerun decisions map (issue → action) when running from a rerun manifest
    rerun_decisions: dict[int, str] = {}
    if rerun_manifest:
        rerun_decisions = {
            d["issue_num"]: d["action"]
            for d in rerun_manifest.get("decisions", [])
        }

    # Load or build state
    if (resume or retry_failed) and state_path.exists():
        sys.stdout.write(str(f"  Loading existing sprint state from {state_path}") + "\n")
        state = SprintState.from_dict(json.loads(state_path.read_text()))
        if token_budget:
            state.token_budget = token_budget
        # Backfill project if missing from saved state (legacy runs)
        if not state.project and eff_repo:
            state.project = eff_repo
    elif rerun_manifest:
        # Build state from manifest decisions (skip UAT; dispatch coder/tester for rest)
        raw_issues = [
            {"number": d["issue_num"], "title": d["issue_title"]}
            for d in rerun_manifest.get("decisions", [])
            if d["action"] != "skip"
        ]
        if not raw_issues:
            sys.stdout.write(str("No issues to dispatch for this re-run (all skipped).") + "\n")
            state = SprintState(
                sprint_label    = label,
                sprint_number   = sprint_num,
                project         = eff_repo or "",
                start_timestamp = _utcnow(),
            )
            return _SprintPreflightResult(
                state=state, state_path=state_path, summary=summary,
                sprint_num=sprint_num, sprint_branch=sprint_branch,
                target_branch=target_branch, eff_repo=eff_repo, api_url=api_url,
                run_id=_run_id, rerun_decisions=rerun_decisions,
                eff_sprints_dir=cfg.sprints_dir if cfg is not None else SPRINTS_DIR,
                dispatch_levels=[], level_nums_by_idx=[], pipeline_on=False,
                start_time=time.monotonic(), early_exit=True,
            )

        state = SprintState(
            sprint_label    = label,
            sprint_number   = sprint_num,
            project         = eff_repo or "",
            start_timestamp = _utcnow(),
            token_budget    = token_budget,
            issues=[
                IssueState(number=i["number"], title=i["title"], agent_status="queued")
                for i in raw_issues
            ],
        )
    else:
        raw_issues = list_backlog_issues(label, repo_name=eff_repo)
        if not raw_issues:
            eff_sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
            plan_nums = _load_sprint_plan(eff_sprints_dir, label)
            if plan_nums:
                raw_issues = _issues_from_plan_numbers(label, plan_nums, repo_name=eff_repo)
                if raw_issues:
                    sys.stdout.write(str(
                        f"  Loaded {len(raw_issues)} issue(s) from plan.json "
                        f"(GitHub backlog filter was empty)."
                    ) + "\n")
        if not raw_issues:
            sys.stdout.write(str(
                "No dispatchable issues found for this label "
                "(check sprint label + status labels on GitHub)."
            ) + "\n")
            state = SprintState(
                sprint_label  = label,
                sprint_number = sprint_num,
                project       = eff_repo or "",
                start_timestamp = _utcnow(),
            )
            return _SprintPreflightResult(
                state=state, state_path=state_path, summary=summary,
                sprint_num=sprint_num, sprint_branch=sprint_branch,
                target_branch=target_branch, eff_repo=eff_repo, api_url=api_url,
                run_id=_run_id, rerun_decisions=rerun_decisions,
                eff_sprints_dir=cfg.sprints_dir if cfg is not None else SPRINTS_DIR,
                dispatch_levels=[], level_nums_by_idx=[], pipeline_on=False,
                start_time=time.monotonic(), early_exit=True,
            )

        state = SprintState(
            sprint_label    = label,
            sprint_number   = sprint_num,
            project         = eff_repo or "",
            start_timestamp = _utcnow(),
            token_budget    = token_budget,
            issues=[
                IssueState(number=i["number"], title=i["title"], agent_status="queued")
                for i in raw_issues
            ],
        )

    # ── Slot capacity resolution (issue #1415) ────────────────────────────────
    # Resolve max_coder_slots / max_tester_slots at run start so the running-pane
    # status payload reflects the correct lane capacity from the very first post.
    # Precedence: sprint-level param > project settings (cfg) > serial default (1).
    state.max_coder_slots = (
        max_coder_slots if max_coder_slots is not None
        else (cfg.max_coder_slots if cfg is not None and cfg.max_coder_slots is not None else 1)
    )
    state.max_tester_slots = (
        max_tester_slots if max_tester_slots is not None
        else (cfg.max_tester_slots if cfg is not None and cfg.max_tester_slots is not None else 1)
    )
    _post_sprint_status(state, api_url=api_url)

    sys.stdout.write(str(f"\n=== Sprint Manager: label={label} ===") + "\n")
    sys.stdout.write(str(f"Target branch: {target_branch}") + "\n")
    sys.stdout.write(str(f"Found {len(state.issues)} issue(s): {[i.number for i in state.issues]}") + "\n")
    try:
        structured_log.event(
            "sprint.start",
            run_id=_run_id,
            issue_num=None,
            sprint_label=label,
            agent_role="sprint",
            issue_count=len(state.issues),
            target_branch=target_branch,
        )
    except Exception:
        pass

    # Create sprint branch (idempotent). Skip when merging directly into develop.
    if target_branch != "develop":
        parent_ref = base_merge_target if _is_child_sprint_label(label) else "develop"
        if dry_run:
            sys.stdout.write(str(
                f"  [dry-run] would create sprint branch {sprint_branch!r} off {parent_ref}"
            ) + "\n")
        else:
            _create_sprint_branch(sprint_branch, parent_ref=parent_ref)
    else:
        sys.stdout.write(str(f"  Using custom target branch {target_branch!r} — sprint branch creation skipped.") + "\n")

    # Warn about shared-file conflicts before dispatching
    _warn_file_conflicts(state.issues)

    # ── Neon dual-write: initialise sprint + tickets ───────────────────────────
    _eff_sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
    if not dry_run and not resume and not retry_failed:
        _neon_sprint_init(label, state.issues, eff_repo or "", _eff_sprints_dir)

    # ── Sprint estimator (issue #166) ──────────────────────────────────────────
    # Runs BEFORE the per-ticket loop so the human can see estimates on the
    # dashboard before any coding starts.  Failure never blocks the sprint.
    if not skip_estimator and not dry_run and not resume and not retry_failed:
        sys.stdout.write(str("\n  [estimator] Running sprint estimator ...") + "\n")
        try:
            from sprint_estimator import run_estimator  # noqa: PLC0415
            eff_sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
            est_result = run_estimator(
                sprint_label = label,
                repo_name    = eff_repo,
                sprints_dir  = eff_sprints_dir,
                cfg          = cfg,
            )
            # Merge estimates into state keyed by issue number (int)
            state.estimates = {
                num: est.to_dict()
                for num, est in est_result.estimates.items()
            }
            state.estimator_status        = "succeeded"
            state.estimator_total_minutes = est_result.total_minutes
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)
            sys.stdout.write(str(f"  [estimator] Estimator succeeded: "
                f"{len(est_result.estimates)} tickets, "
                f"{est_result.total_minutes} total minutes") + "\n")
        except ImportError:
            structured_log.warn("estimator_module_missing", "[estimator] sprint_estimator module not found — skipping")
            state.estimator_status = "failed"
            state.save(state_path)
        except Exception as e:
            structured_log.warn("estimator_sprint_failed", f"[estimator] estimator failed: {e}", exc=str(e))
            state.estimator_status = "failed"
            state.save(state_path)
    elif skip_estimator:
        sys.stdout.write(str("  [estimator] --skip-estimator active — skipping estimation") + "\n")
        state.estimator_status = "skipped"
    # ── end estimator ──────────────────────────────────────────────────────────

    # -- Topological dispatch setup (issue #445) --
    _plan_order = _load_sprint_plan(_eff_sprints_dir, label)
    if _plan_order is None:
        structured_log.warn(
            "dispatch_plan_missing",
            "plan.json missing or unreadable — falling back to ascending issue-number order",
            sprint_label=label,
        )

    _dag_layers = _build_sprint_dag_layers(state.issues)
    if _dag_layers is None:
        structured_log.warn(
            "dispatch_dag_missing",
            "DAG data missing or unreadable — falling back to ascending issue-number order",
            sprint_label=label,
        )

    _dispatch_levels = _compute_dispatch_levels(state.issues, _plan_order, _dag_layers)
    _level_nums_by_idx = [[iss.number for iss in lvl] for lvl in _dispatch_levels]

    # Persist the resolved dispatch order to sprint_ticket_order (issue #757).
    # This is the exact order tickets are processed below — the durable record
    # of "what ran in what order", replacing plan.json as source of truth.
    if not dry_run:
        _flat_order = [num for lvl in _level_nums_by_idx for num in lvl]
        _sprint_db_set_ticket_order_sm(label, _flat_order)

    # Tag each IssueState with its 1-based execution level (issue #613: seg bar / level-sep UI)
    for _lvl_idx, _lvl_issues in enumerate(_dispatch_levels):
        for _lvl_iss in _lvl_issues:
            _lvl_iss.dispatch_level = _lvl_idx + 1

    start_time = time.monotonic()

    # Clear any stale in-progress / SIT labels left by a prior interrupted or
    # crashed run before dispatch begins — otherwise those tickets show stuck
    # spinners on the board, and in pipeline mode a ghost SIT would violate the
    # at-most-one-SIT invariant (issue #738 AC5).
    _sweep_stale_in_progress(label, eff_repo)
    _sweep_stale_status("SIT", label, eff_repo)

    # ── Worktree pool setup (issue #1411) ──────────────────────────────────────
    # Reconcile any orphaned worktrees from a prior crash, then create a fresh
    # pool of K isolated worktrees for concurrent coder dispatch.
    if not dry_run:
        global _ACTIVE_WORKTREE_POOL
        _pool_repo_root = Path(cfg.worktree_coder).parent if cfg is not None else REPO_ROOT
        _pool_commander = (
            discover_commander_dir(cfg.worktree_coder if cfg is not None else None)
        )
        _pool_dir = _pool_commander / "runtime" / "worktree-pool"
        _WorktreePool.reconcile_orphans(_pool_dir, _pool_repo_root)
        _pool_slots = state.max_coder_slots
        _pool_req = (cfg.worktree_coder / "requirements.txt") if cfg is not None else None
        if _pool_req is not None and not Path(_pool_req).exists():
            _pool_req = None
        _pool = _WorktreePool(
            pool_dir=_pool_dir,
            repo_root=_pool_repo_root,
            base_branch=sprint_branch,
            slots=_pool_slots,
            requirements_file=Path(_pool_req) if _pool_req else None,
        )
        _pool.create()
        _ACTIVE_WORKTREE_POOL = _pool
        sys.stdout.write(str(
            f"  [worktree-pool] {_pool_slots} slot(s) ready"
            f" (max_coder_slots={_pool_slots})\n"
        ))

    _emit_sprint_lifecycle_event(
        type="sprint_started",
        target=f"sprint-{sprint_num}" if sprint_num is not None else label,
        actor="system",
        detail={"ticket_count": len(state.issues), "levels": len(_dispatch_levels)},
        project=eff_repo or label,
        action_id=_run_id,
    )
    # ── Dispatch mode resolution (issue #737) ────────────────────────────────
    # Pipeline mode runs one coder + one tester worker concurrently per level.
    # Precedence: kill-switch env > per-sprint setting > per-project (cfg) > serial.
    _project_pipeline = cfg.pipeline_mode if cfg is not None else False
    if not _project_pipeline:
        try:
            import settings_repo as _settings_repo  # noqa: PLC0415
            _stored = _settings_repo.get_setting("app_config", project=eff_repo)
            if isinstance(_stored, dict) and _stored.get("pipeline_mode"):
                _project_pipeline = True
        except Exception:
            pass
    _pipeline_on = _pipeline_mode_enabled(
        sprint_setting=pipeline_mode,
        project_setting=_project_pipeline,
        env=os.environ,
    )
    # Dry-run never spawns workers — fall back to the serial no-op path.
    if dry_run:
        _pipeline_on = False
    # Persist on state so the dashboard /live snapshot can gate dual-agent UI (issue #739).
    state.pipeline_mode = _pipeline_on
    # Slot capacity was resolved at run start (issue #1415) — no re-assignment needed.
    sys.stdout.write(str("  Dispatch mode: "
        + ("pipeline (1 coder + 1 tester concurrent)" if _pipeline_on else "serial")) + "\n")
    try:
        structured_log.event(
            "dispatch.mode",
            run_id=_run_id, issue_num=None, sprint_label=label,
            agent_role="sprint", pipeline=_pipeline_on,
        )
    except Exception:
        pass

    return _SprintPreflightResult(
        state=state,
        state_path=state_path,
        summary=summary,
        sprint_num=sprint_num,
        sprint_branch=sprint_branch,
        target_branch=target_branch,
        eff_repo=eff_repo,
        api_url=api_url,
        run_id=_run_id,
        rerun_decisions=rerun_decisions,
        eff_sprints_dir=_eff_sprints_dir,
        dispatch_levels=_dispatch_levels,
        level_nums_by_idx=_level_nums_by_idx,
        pipeline_on=_pipeline_on,
        start_time=start_time,
        early_exit=False,
    )


def run_sprint_loop(
    pf: "_SprintPreflightResult",
    *,
    label: str,
    preflight_approved: "Optional[list[int]]",
    dry_run: bool,
    resume: bool,
    retry_failed: bool,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    gate_typecheck: bool,
    gate_design: bool,
    gate_frontend_lint: bool,
    gate_monolith: bool,
    gate_scope: str,
    alert_modes: list,
    cfg: "Optional[SprintConfig]",
) -> None:
    """Per-ticket iteration loop for run_sprint.

    Processes each ticket in the flat dispatch list sequentially:
    dispatches the coder agent, then the tester agent, runs post-tester
    gates, and updates sprint state.  Emits level_start / level_complete
    events at dispatch-level boundaries.

    Mutates ``pf.state`` and ``pf.summary`` in place.  Called by
    ``run_sprint`` after the preflight and (optional) pipeline-dispatch
    phases complete.
    """
    state              = pf.state
    state_path         = pf.state_path
    summary            = pf.summary
    sprint_num         = pf.sprint_num
    sprint_branch      = pf.sprint_branch
    target_branch      = pf.target_branch
    eff_repo           = pf.eff_repo
    api_url            = pf.api_url
    _run_id            = pf.run_id
    rerun_decisions    = pf.rerun_decisions
    _eff_sprints_dir   = pf.eff_sprints_dir
    _dispatch_levels   = pf.dispatch_levels
    _level_nums_by_idx = pf.level_nums_by_idx
    _pipeline_on       = pf.pipeline_on
    start_time         = pf.start_time
    total_issues       = len(state.issues)

    # Flat iteration preserving level boundaries for level_start / level_complete events.
    # Empty when pipeline mode handled dispatch above.
    _flat_dispatch: list[tuple[int, IssueState]] = [] if _pipeline_on else [
        (lvl_idx, iss)
        for lvl_idx, lvl in enumerate(_dispatch_levels)
        for iss in lvl
    ]
    _prev_level_idx = -1
    _level_merged_before = 0
    _level_skipped_before = 0
    for _flat_pos, (_cur_level_idx, issue_state) in enumerate(_flat_dispatch):
        if _cur_level_idx != _prev_level_idx:
            if _prev_level_idx >= 0:
                try:
                    structured_log.event(
                        "level_complete",
                        run_id=_run_id,
                        issue_num=None,
                        sprint_label=label,
                        agent_role="sprint",
                        level_index=_prev_level_idx,
                        merged=summary.merged[_level_merged_before:],
                        skipped=summary.skipped[_level_skipped_before:],
                        total=len(_level_nums_by_idx[_prev_level_idx]),
                    )
                except Exception:
                    pass
            _level_merged_before = len(summary.merged)
            _level_skipped_before = len(summary.skipped)
            _prev_level_idx = _cur_level_idx
            try:
                structured_log.event(
                    "level_start",
                    run_id=_run_id,
                    issue_num=None,
                    sprint_label=label,
                    agent_role="sprint",
                    level_index=_cur_level_idx,
                    tickets=_level_nums_by_idx[_cur_level_idx],
                )
            except Exception:
                pass
            sys.stdout.write(str(f"\n=== Dispatch level {_cur_level_idx}: tickets {_level_nums_by_idx[_cur_level_idx]} ===") + "\n")
        idx = _flat_pos + 1
        num   = issue_state.number
        title = issue_state.title
        progress = f"[{idx}/{total_issues}]"

        # Resume: skip already-done/skipped
        if resume and issue_state.status in ("done", "skipped"):
            sys.stdout.write(str(f"\n--- {progress} Issue #{num}: {title} --- [SKIP: already {issue_state.status}]") + "\n")
            try:
                structured_log.event(
                    "issue.skip",
                    run_id=_run_id,
                    issue_num=num,
                    sprint_label=label,
                    agent_role="sprint",
                    skip_reason=f"already {issue_state.status}",
                )
            except Exception:
                pass
            summary.processed.append(f"#{num}")
            if issue_state.status == "done":
                summary.merged.append(f"#{num}")
            else:
                summary.skipped.append(f"#{num} ({issue_state.skip_reason or 'skipped'})")
            continue

        # retry_failed: skip only done issues
        if retry_failed and issue_state.status == "done":
            sys.stdout.write(str(f"\n--- {progress} Issue #{num}: {title} --- [SKIP: already done]") + "\n")
            try:
                structured_log.event(
                    "issue.skip",
                    run_id=_run_id,
                    issue_num=num,
                    sprint_label=label,
                    agent_role="sprint",
                    skip_reason="already done",
                )
            except Exception:
                pass
            summary.processed.append(f"#{num}")
            summary.merged.append(f"#{num}")
            continue

        # Already-merged guard (hotfix E2): if this ticket's feature branch is
        # already merged into the sprint merge target, it passed in a prior run —
        # never re-dispatch it, even if its UAT label was stripped. That is
        # exactly how sprint-73's #931 (merged at 19:35, UAT stripped at 19:50)
        # got re-run into a divergent-branch crash. Trust git, not the label, and
        # re-apply UAT so the board reflects reality.
        if issue_state.status not in ("done", "skipped"):
            # Prune stale local feature branch before E2 merge-check so we read
            # fresh state and don't misidentify an ancestor as merged.
            _e2_cwd = cfg.worktree_coder if cfg is not None else REPO_ROOT
            _prune_stale_local_feature_branch(num, target_branch, cwd=_e2_cwd)
            # Use strict merge check to avoid false-positives from stale ancestor
            # branches that pass git branch --merged without ever being merged.
            _e2_merged = _is_issue_merged_into_target(num, target_branch)
            if _e2_merged:
                sys.stdout.write(str(
                    f"\n--- {progress} Issue #{num}: {title} --- "
                    f"[SKIP: already merged into {target_branch} in a prior run]") + "\n")
                try:
                    structured_log.event(
                        "issue.skip", run_id=_run_id, issue_num=num,
                        sprint_label=label, agent_role="sprint",
                        skip_reason=f"already merged into {target_branch}",
                    )
                except Exception:
                    pass
                issue_state.status = "done"
                issue_state.set_agent_status("merged")
                _transition_safe(
                    num, _TicketState.UAT,
                    actor="sprint_manager:already_merged", repo_name=eff_repo,
                )
                summary.processed.append(f"#{num}")
                summary.merged.append(f"#{num}")
                state.save(state_path)
                continue

        sys.stdout.write(str(f"\n--- {progress} Issue #{num}: {title} ---") + "\n")
        try:
            structured_log.event(
                "issue.start",
                run_id=_run_id,
                issue_num=num,
                sprint_label=label,
                agent_role="sprint",
            )
        except Exception:
            pass
        summary.processed.append(f"#{num}")

        # AC-3: check for pause file before dispatching this issue
        _wait_if_paused(sprint_num, state, api_url=api_url)

        # Log estimate info if available
        _est = _load_estimate(num)
        if _est:
            _size  = _est.get("size", "?")
            _hours = _est.get("estimated_hours", "?")
            _conf  = _est.get("confidence", "?")
            try:
                _h = float(_hours)
                _time_str = f"~{int(_h * 60)}min" if _h < 1 else f"~{_h}h"
            except (TypeError, ValueError):
                _time_str = f"~{_hours}h"
            sys.stdout.write(str(f"  [estimate] size={_size} ({_time_str}), confidence={_conf}") + "\n")
            _risk = _est.get("risk_flags", [])
            if _risk:
                sys.stdout.write(str(f"  [estimate] risk flags: {', '.join(_risk)}") + "\n")
                _serious = [f for f in _risk if f in SERIOUS_RISK_FLAGS]
                if _serious:
                    structured_log.warn("estimate_serious_risk", f"serious risk flags: {', '.join(_serious)}", issue_num=num, risk_flags=_serious)

        # Preflight filter: skip issues not approved by pre-flight review
        if preflight_approved is not None and num not in preflight_approved:
            sys.stdout.write(str("  [preflight] skipped by pre-flight review") + "\n")
            try:
                structured_log.event(
                    "issue.skip",
                    run_id=_run_id,
                    issue_num=num,
                    sprint_label=label,
                    agent_role="sprint",
                    skip_reason="preflight-skipped",
                )
            except Exception:
                pass
            issue_state.set_agent_status("failed")
            issue_state.failure_reason  = "preflight-skipped"
            issue_state.status          = "skipped"
            issue_state.skip_reason     = "preflight-skipped"
            summary.skipped.append(f"#{num} (preflight-skipped)")
            _emit_ticket_failed(
                num, "coder", "preflight-skipped", "preflight-skipped",
                project=eff_repo or label, action_id=_run_id, cfg=cfg, sprint_label=label,
            )
            _neon_ticket_status(label, num, "skipped", _eff_sprints_dir)
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url, project=eff_repo)
            continue

        if dry_run:
            sys.stdout.write(str("  [dry-run] would dispatch coder + tester") + "\n")
            try:
                structured_log.event(
                    "issue.dry_run",
                    run_id=_run_id,
                    issue_num=num,
                    sprint_label=label,
                    agent_role="sprint",
                )
            except Exception:
                pass
            issue_state.status = "skipped"
            issue_state.skip_reason = "dry-run"
            summary.skipped.append(f"#{num} (dry-run)")
            _neon_ticket_status(label, num, "skipped", _eff_sprints_dir)
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url, project=eff_repo)
            continue

        # -- Port detection (issue #62, AC-5/6/7/8) --
        chosen_port: Optional[int] = None
        if cfg is not None and cfg.app_default_port is not None:
            chosen_port = _detect_port(cfg)
            if chosen_port is not None:
                _write_runtime_port(cfg.worktree_coder, chosen_port)

        # Determine rerun routing: dispatch_tester skips coder entirely
        _skip_coder = rerun_decisions.get(num) == "dispatch_tester"

        # -- Lifecycle: queued --
        issue_state.set_agent_status("queued")
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)

        # -- Bounded fix-loop: coder → tester → gates (issue #618) ──────────────
        # K read from COMMANDER_MAX_FIX_ROUNDS (default 3). _skip_coder path
        # (rerun-tester-direct) runs tester+gates once inside the same loop body
        # but never re-dispatches coder on logic failure.
        _max_fix_rounds  = int(os.environ.get("COMMANDER_MAX_FIX_ROUNDS", "3"))
        _fix_rounds      = 1 if _skip_coder else _max_fix_rounds
        _fix_history: list[dict] = []       # per-attempt failure records
        _last_failure_sig: Optional[str] = None  # for consecutive-dup detection
        _gate_passed  = False               # gate passed → loop exits success
        _infra_exit   = False               # infra failure → skip to next issue
        _loop_aborted = False               # dup/early-abort → RETRY_EXHAUSTED
        # Hang-redispatch state (issue #787): track how many times this ticket
        # has been redispatched after a hang.  At most 1 redispatch per ticket.
        _hang_redispatch_count = 0
        _hang_continuation: Optional[dict] = None   # context for next dispatch
        _next_attempt_kind = "initial"              # initial / fix_round / hang_continue
        _next_coder_backend = _select_coder_backend(num, cfg, repo_name=eff_repo)  # issue #920: tracked so escalation from cline to claude-code persists across fix rounds

        for _fix_attempt in range(_fix_rounds):

            if _skip_coder:
                sys.stdout.write(str(f"  [rerun] SIT ticket: dispatching tester directly for #{num}") + "\n")
                try:
                    structured_log.event(
                        "issue.rerun_tester_direct",
                        run_id=_run_id,
                        issue_num=num,
                        sprint_label=label,
                        agent_role="sprint",
                    )
                except Exception:
                    pass
            else:
                # -- Dispatch coder --
                issue_state.set_agent_status("coder_dispatched")
                issue_state.coder_started_at = issue_state.status_changed_at
                # Pre-compute model + routing_reason (issue #789) before opening the
                # agent_runs row so the selection is captured at dispatch time.
                _ser_est = _load_estimate(num)
                _ser_coder_model, _ser_route_reason = _resolve_coder_model(num, cfg, estimate=_ser_est)
                issue_state.coder_model = _ser_coder_model  # surface size-routed model on the live running pane (bug: coder badge had no model)
                issue_state.coder_backend = _effective_coder_backend(label, cfg, _fix_history if _fix_history else None)
                _db_agent_start_sm(
                    num, label, "coder",
                    model_used=_ser_coder_model, routing_reason=_ser_route_reason,
                    attempt_kind=_next_attempt_kind,
                    log_path=str(_issue_log_path(num, cfg=cfg)),
                    backend=_next_coder_backend,
                )  # issue #764, #789, #787, #783, #920
                _emit_sprint_lifecycle_event(
                    type="ticket_dispatched",
                    target=f"#{num}",
                    actor="system",
                    detail={"agent": "CODER"},
                    project=eff_repo or label,
                    action_id=_run_id,
                )
                state.save(state_path)
                _post_sprint_status(state, api_url=api_url)

                # AC-5 (issue #311): apply in-progress label when coder starts so the
                # board reflects active work during coding.  Best-effort — never blocks
                # the sprint on label failure.
                _transition_safe(num, _TicketState.IN_PROGRESS, actor="sprint_manager", repo_name=eff_repo)

                def _on_coder_running(
                    _is=issue_state, _st=state, _sp=state_path, _api=api_url,
                    _lbl=label, _n=num, _sd=_eff_sprints_dir,
                ) -> None:
                    _is.set_agent_status("coder_running")
                    _neon_ticket_status(_lbl, _n, "running", _sd)
                    _st.save(_sp)
                    _post_sprint_status(_st, api_url=_api)

                _coder_t0 = time.monotonic()
                _coder_utc0 = _token_window_utc_now()
                _serial_pool_slot = _pool_acquire()
                try:
                    coder_ok, coder_category = _dispatch_coder(
                        num, alert_modes, sprint_branch=sprint_branch, repo_name=eff_repo, cfg=cfg,
                        chosen_port=chosen_port, rate_limit_events=state.rate_limit_events,
                        on_running=_on_coder_running, sprint_label=label,
                        prior_failures=_fix_history if _fix_history else None,
                        hang_continuation=_hang_continuation,
                        attempt_kind=_next_attempt_kind,
                        coder_backend_override=_next_coder_backend,
                        worktree_override=_serial_pool_slot,
                    )
                except SystemExit:
                    _remaining = [i for i in state.issues if i.status not in ("done", "skipped")]
                    _emit_sprint_lifecycle_event(
                        type="sprint_cancelled",
                        target=f"sprint-{sprint_num}" if sprint_num is not None else label,
                        actor="system",
                        detail={
                            "tickets_remaining": len(_remaining),
                            "duration": round(time.monotonic() - start_time),
                        },
                        project=eff_repo or label,
                        action_id=_run_id,
                    )
                    raise
                finally:
                    _pool_release(_serial_pool_slot)
                _coder_elapsed = time.monotonic() - _coder_t0
                _ctin, _ctout = _token_window_sums("coder", _coder_utc0)
                state.total_tokens_in += _ctin
                state.total_tokens_out += _ctout
                _db_agent_finish_sm(  # issue #764: close the run with precise duration
                    num, label, "coder",
                    duration_seconds=_coder_elapsed,
                    outcome="success" if coder_ok else "failed",
                    total_tokens=(_ctin + _ctout) or None,
                )
                _coder_m, _coder_s = divmod(int(_coder_elapsed), 60)
                sys.stdout.write(str(f"  Total time used on coder dispatch: {_coder_m}m {_coder_s}s") + "\n")
                # Clear hang_continuation after each dispatch so subsequent
                # fix_round dispatches don't inherit stale hang context.
                _hang_continuation = None
                _next_attempt_kind = "fix_round"
                try:
                    structured_log.event(
                        "coder.done",
                        run_id=_run_id,
                        issue_num=num,
                        sprint_label=label,
                        agent_role="coder",
                        elapsed_secs=round(_coder_elapsed),
                        success=coder_ok,
                    )
                except Exception:
                    pass

                if not coder_ok:
                    category = coder_category or FailureCategory.CRASH
                    if category == FailureCategory.RETRY_EXHAUSTED:
                        reason = "Subscription rate limit exhausted"
                    else:
                        reason = f"Coder failed with category {category}"
                    structured_log.error("coder_failed", f"coder failed for #{num}: {category}", issue_num=num, category=category)
                    try:
                        structured_log.event(
                            "coder.failed",
                            run_id=_run_id,
                            issue_num=num,
                            sprint_label=label,
                            agent_role="coder",
                            category=category,
                            reason=reason,
                        )
                    except Exception:
                        pass

                    if category in _LOGIC_FAILURE_CATEGORIES:
                        # Logic failure: record, check for consecutive dup, retry
                        record_failure(
                            num, category,
                            detail=_build_crash_detail(_issue_log_path(num, cfg=cfg)),
                        )
                        _sig = category
                        _fix_history.append({
                            "attempt": _fix_attempt, "category": category, "reason": reason,
                        })
                        if _sig == _last_failure_sig:
                            sys.stdout.write(str(f"  [fix-loop] consecutive identical coder failure ({_sig}): "
                                f"aborting early") + "\n")
                            sys.stdout.flush()
                            _loop_aborted = True
                            break
                        _last_failure_sig = _sig
                        sys.stdout.write(str(f"  [fix-loop] coder logic failure ({_sig}), "
                            f"attempt {_fix_attempt + 1}/{_fix_rounds}: will retry") + "\n")
                        sys.stdout.flush()
                        _next_attempt_kind = "fix_round"
                        # issue #920: escalate from cline to claude-code after a Cline gate failure.
                        if _next_coder_backend == "cline":
                            _next_coder_backend = "claude-code"
                            sys.stdout.write(str(f"  [cline-escalation] #{num}: Cline coder failed — escalating to claude-code for next fix round") + "\n")
                            sys.stdout.flush()
                            try:
                                structured_log.event(
                                    "coder_backend_escalated",
                                    category="agent",
                                    issue_num=num,
                                    sprint_label=label,
                                    project=eff_repo,
                                    agent_role="coder",
                                    from_backend="cline",
                                    to_backend="claude-code",
                                )
                            except Exception:
                                pass
                        continue

                    # Hang-redispatch path (issue #787): on first hang, redispatch once
                    # with continuation context when COMMANDER_HANG_REDISPATCH != "0".
                    if category == FailureCategory.HANG:
                        _hang_redispatch_enabled = os.environ.get(
                            "COMMANDER_HANG_REDISPATCH", "1"
                        ) != "0"
                        if _hang_redispatch_enabled and _hang_redispatch_count == 0:
                            _hang_redispatch_count += 1
                            # Read log_tail from the sidecar written by _dispatch_coder.
                            _hc_log_tail: list[str] = []
                            try:
                                _sc = REPO_ROOT / ".commander" / "runtime" / f"last-failure-{num}.json"
                                if cfg:
                                    _sc = cfg.worktree_coder.parent / ".commander" / "runtime" / f"last-failure-{num}.json"
                                if _sc.exists():
                                    _sc_data = json.loads(_sc.read_text(encoding="utf-8"))
                                    _hc_log_tail = _sc_data.get("log_tail", [])
                            except Exception:
                                pass
                            _hang_continuation = {
                                "timestamp": _utcnow(),
                                "log_tail": _hc_log_tail,
                            }
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
                            sys.stdout.write(str(f"  [hang-redispatch] #{num}: first hang, redispatching with "
                                f"continuation context (log_tail lines: {len(_hc_log_tail)})") + "\n")
                            sys.stdout.flush()
                            _next_attempt_kind = "hang_continue"
                            _hang_continuation_set = _hang_continuation  # for clarity
                            continue
                        # Second hang or redispatch disabled: fall through to infra-failure path.

                    # Infra failure: existing path, no retry
                    issue_state.set_agent_status("failed")
                    issue_state.coder_finished_at = issue_state.status_changed_at
                    issue_state.failure_reason    = reason
                    issue_state.status            = "skipped"
                    issue_state.skip_reason       = reason
                    issue_state.category          = category
                    summary.skipped.append(f"#{num} (coder failed)")
                    dispatch_alerts(
                        alert_modes,
                        title=f"Issue #{num} skipped: {category}",
                        body=reason,
                        issue_num=num,
                        category=category,
                        cfg=cfg,
                        repo=eff_repo,
                    )
                    _emit_ticket_failed(
                        num, "coder", reason, category,
                        project=eff_repo or label, action_id=_run_id, cfg=cfg, sprint_label=label,
                    )
                    _neon_ticket_status(label, num, "failed", _eff_sprints_dir)
                    state.save(state_path)
                    _post_sprint_status(state, api_url=api_url, project=eff_repo)
                    _infra_exit = True
                    break

                # -- Detect CODER_NO_WORK: coder exited 0 but created no feature branch --
                if _find_feature_branch(num) is None:
                    category = FailureCategory.CODER_NO_WORK
                    reason   = f"Coder exited 0 but no feature/{num}-* branch was created"
                    sys.stdout.write(str(f"  {reason}") + "\n")
                    # Logic failure: record, check dup, retry
                    record_failure(num, category, detail=reason)
                    _sig = category
                    _fix_history.append({
                        "attempt": _fix_attempt, "category": category, "reason": reason,
                    })
                    if _sig == _last_failure_sig:
                        sys.stdout.write(str("  [fix-loop] consecutive identical CODER_NO_WORK: aborting early") + "\n")
                        sys.stdout.flush()
                        _loop_aborted = True
                        break
                    _last_failure_sig = _sig
                    sys.stdout.write(str(f"  [fix-loop] CODER_NO_WORK, "
                        f"attempt {_fix_attempt + 1}/{_fix_rounds}: will retry") + "\n")
                    sys.stdout.flush()
                    # issue #920: escalate from cline to claude-code on CODER_NO_WORK too.
                    if _next_coder_backend == "cline":
                        _next_coder_backend = "claude-code"
                        sys.stdout.write(str(f"  [cline-escalation] #{num}: Cline coder no-work — escalating to claude-code for next fix round") + "\n")
                        sys.stdout.flush()
                        try:
                            structured_log.event(
                                "coder_backend_escalated",
                                category="agent",
                                issue_num=num,
                                sprint_label=label,
                                project=eff_repo,
                                agent_role="coder",
                                from_backend="cline",
                                to_backend="claude-code",
                            )
                        except Exception:
                            pass
                    continue

                # -- Lifecycle: coder_done --
                issue_state.set_agent_status("coder_done")
                issue_state.coder_finished_at = issue_state.status_changed_at
                _emit_sprint_lifecycle_event(
                    type="ticket_agent_finished",
                    target=f"#{num}",
                    actor="system",
                    detail={"agent": "CODER", "duration": round(_coder_elapsed)},
                    project=eff_repo or label,
                    action_id=_run_id,
                )
                state.save(state_path)
                _post_sprint_status(state, api_url=api_url)

            # Transition to SIT before dispatching the tester (issue #509).
            _transition_safe(num, _TicketState.SIT, actor="sprint_manager", repo_name=eff_repo)

            # -- Lifecycle: tester_dispatched --
            issue_state.set_agent_status("tester_dispatched")
            issue_state.tester_started_at = issue_state.status_changed_at

            # Pre-dispatch risk classification (issue #790): classify before
            # recording the agent_runs row so risk_tier and model_used are
            # captured at dispatch time.
            try:
                _tester_labels = list(_get_issue_labels(num, repo_name=eff_repo))
            except Exception:
                _tester_labels = []
            _tester_risk = _classify_risk_tier(labels=_tester_labels)
            _by_risk_map = (cfg.tester_by_risk if cfg is not None else None) or {
                "LOW": "claude-haiku-4-5",
                "MEDIUM": "claude-haiku-4-5",
                "HIGH": "claude-sonnet-4-6",
            }
            _tester_model_selected = _by_risk_map.get(_tester_risk) or (
                cfg.tester_model if cfg is not None else "claude-sonnet-4-6"
            )

            _db_agent_start_sm(
                num, label, "tester",
                risk_tier=_tester_risk, model_used=_tester_model_selected,
                log_path=str(_issue_log_path(num, cfg=cfg)),
            )  # issue #764, #790, #783
            # Record the tester attempt so analytics can count rejections exactly
            # going forward (issue #718).
            issue_state.tester_attempt_count += 1
            _emit_sprint_lifecycle_event(
                type="ticket_dispatched",
                target=f"#{num}",
                actor="system",
                detail={"agent": "TESTER"},
                project=eff_repo or label,
                action_id=_run_id,
            )
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)

            def _on_tester_running(
                _is=issue_state, _st=state, _sp=state_path, _api=api_url
            ) -> None:
                _is.set_agent_status("tester_running")
                _st.save(_sp)
                _post_sprint_status(_st, api_url=_api)

            # -- Dispatch tester --
            _tester_t0 = time.monotonic()
            _tester_utc0 = _token_window_utc_now()
            try:
                tester_rc, hang_category = _dispatch_tester(
                    num, alert_modes, sprint_branch=sprint_branch, repo_name=eff_repo, cfg=cfg,
                    chosen_port=chosen_port, rate_limit_events=state.rate_limit_events,
                    on_running=_on_tester_running, sprint_label=label,
                    pre_dispatch_risk=_tester_risk,
                    prior_failures=_fix_history if _fix_history else None,
                )
            except SystemExit:
                _remaining = [i for i in state.issues if i.status not in ("done", "skipped")]
                _emit_sprint_lifecycle_event(
                    type="sprint_cancelled",
                    target=f"sprint-{sprint_num}" if sprint_num is not None else label,
                    actor="system",
                    detail={
                        "tickets_remaining": len(_remaining),
                        "duration": round(time.monotonic() - start_time),
                    },
                    project=eff_repo or label,
                    action_id=_run_id,
                )
                raise
            _tester_elapsed = time.monotonic() - _tester_t0
            _ttin, _ttout = _token_window_sums("tester", _tester_utc0)
            state.total_tokens_in += _ttin
            state.total_tokens_out += _ttout
            _db_agent_finish_sm(  # issue #764: close the run with precise duration
                num, label, "tester",
                duration_seconds=_tester_elapsed,
                outcome="pass" if tester_rc == 0 else "fail",
                total_tokens=(_ttin + _ttout) or None,
            )
            _tester_m, _tester_s = divmod(int(_tester_elapsed), 60)
            sys.stdout.write(str(f"  Total time used on tester dispatch: {_tester_m}m {_tester_s}s") + "\n")
            try:
                structured_log.event(
                    "tester.done",
                    run_id=_run_id,
                    issue_num=num,
                    sprint_label=label,
                    agent_role="tester",
                    elapsed_secs=round(_tester_elapsed),
                    exit_code=tester_rc,
                )
            except Exception:
                pass

            if hang_category == FailureCategory.HANG:
                issue_state.set_agent_status("failed")
                issue_state.tester_finished_at = issue_state.status_changed_at
                issue_state.failure_reason     = "Tester HANG detected"
                issue_state.status             = "skipped"
                issue_state.skip_reason        = "Tester HANG detected"
                issue_state.category           = FailureCategory.HANG
                summary.skipped.append(f"#{num} (tester hang)")
                _emit_ticket_failed(
                    num, "tester", "Tester HANG detected", FailureCategory.HANG,
                    project=eff_repo or label, action_id=_run_id, cfg=cfg, sprint_label=label,
                )
                _neon_ticket_status(label, num, "failed", _eff_sprints_dir)
                state.save(state_path)
                _post_sprint_status(state, api_url=api_url, project=eff_repo)
                _infra_exit = True
                break

            if hang_category == FailureCategory.RETRY_EXHAUSTED:
                issue_state.set_agent_status("failed")
                issue_state.tester_finished_at = issue_state.status_changed_at
                issue_state.failure_reason     = "Subscription rate limit exhausted"
                issue_state.status             = "skipped"
                issue_state.skip_reason        = "Subscription rate limit exhausted"
                issue_state.category           = FailureCategory.RETRY_EXHAUSTED
                summary.skipped.append(f"#{num} (rate limit exhausted)")
                _emit_ticket_failed(
                    num, "tester", "Subscription rate limit exhausted",
                    FailureCategory.RETRY_EXHAUSTED,
                    project=eff_repo or label, action_id=_run_id, cfg=cfg, sprint_label=label,
                )
                _neon_ticket_status(label, num, "failed", _eff_sprints_dir)
                state.save(state_path)
                _post_sprint_status(state, api_url=api_url)
                _infra_exit = True
                break

            # -- Lifecycle: tester_done --
            issue_state.set_agent_status("tester_done")
            issue_state.tester_finished_at = issue_state.status_changed_at
            _emit_sprint_lifecycle_event(
                type="ticket_agent_finished",
                target=f"#{num}",
                actor="system",
                detail={"agent": "TESTER", "duration": round(_tester_elapsed)},
                project=eff_repo or label,
                action_id=_run_id,
            )
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)

            # -- Post-tester gates --
            merged, summary_line, gate_category = handle_post_tester(
                issue_num          = num,
                tester_exit_code   = tester_rc,
                skip_gates         = skip_gates,
                gate_pytest        = gate_pytest,
                gate_lint          = gate_lint,
                gate_merge_preview = gate_merge_preview,
                gate_typecheck     = gate_typecheck,
                gate_design        = gate_design,
                gate_frontend_lint = gate_frontend_lint,
                gate_monolith      = gate_monolith,
                target_branch      = target_branch,
                repo_name          = eff_repo,
                cfg                = cfg,
                base_branch        = target_branch or "develop",
                gate_scope         = gate_scope,
                documentor_enabled = cfg.documentor_enabled if cfg else False,
                alert_modes        = alert_modes,
                sprint_label       = label,
            )
            sys.stdout.write(str(f"  {summary_line}") + "\n")
            try:
                structured_log.event(
                    "issue.merged" if merged else "issue.skipped",
                    run_id=_run_id,
                    issue_num=num,
                    sprint_label=label,
                    agent_role="sprint",
                    summary_line=summary_line,
                )
            except Exception:
                pass

            _issue_tokens = issue_state.tokens_in + issue_state.tokens_out
            if merged:
                issue_state.set_agent_status("completed")
                issue_state.status = "done"
                summary.merged.append(f"#{num}")
                _neon_ticket_status(label, num, "done", _eff_sprints_dir, total_tokens=_issue_tokens)
                _gate_passed = True
                break

            # Gate failed
            category = gate_category or FailureCategory.CRASH
            if not _skip_coder and category in _LOGIC_FAILURE_CATEGORIES:
                # Logic gate failure: record, check dup, retry coder
                record_failure(num, category, detail=summary_line)
                _sig = f"{category}:{summary_line[:80]}"
                _fix_history.append({
                    "attempt": _fix_attempt, "category": category, "summary": summary_line,
                })
                if _sig == _last_failure_sig:
                    sys.stdout.write(str(f"  [fix-loop] consecutive identical gate failure ({category}): "
                        f"aborting early") + "\n")
                    sys.stdout.flush()
                    _loop_aborted = True
                    break
                _last_failure_sig = _sig
                sys.stdout.write(str(f"  [fix-loop] gate logic failure ({category}), "
                    f"attempt {_fix_attempt + 1}/{_fix_rounds}: will retry coder") + "\n")
                sys.stdout.flush()
                # Re-estimate after tester-triggered failure: bump size one tier so
                # the next coder dispatch uses the correct model/budget routing.
                _bumped = _bump_estimate_size(num)
                if _bumped:
                    sys.stdout.write(str(f"  [re-estimate] #{num}: size bumped to {_bumped} after tester gate failure") + "\n")
                    sys.stdout.flush()
                continue

            # Non-logic gate failure or _skip_coder path: final failure handling
            issue_state.set_agent_status("failed")
            issue_state.failure_reason  = summary_line
            issue_state.status          = "skipped"
            issue_state.skip_reason     = summary_line
            issue_state.category        = category
            if "gate failed" in summary_line:
                summary.gate_failures.append(summary_line)
            else:
                summary.skipped.append(f"#{num} ({category})")
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{num} skipped: {category}",
                body=summary_line,
                issue_num=num,
                category=category,
                cfg=cfg,
                repo=eff_repo,
                sprint_label=label,
            )
            _emit_ticket_failed(
                num, "tester", summary_line, category,
                project=eff_repo or label, action_id=_run_id, cfg=cfg,
                gate=True, sprint_label=label,
            )
            if category in _LOGIC_FAILURE_CATEGORIES:
                _transition_safe(num, _TicketState.NEEDS_REWORK, actor="sprint_manager", repo_name=eff_repo)
            _neon_ticket_status(label, num, "failed", _eff_sprints_dir, total_tokens=_issue_tokens)
            _infra_exit = True
            break

        else:
            # for/else: all _fix_rounds completed without a break → loop exhausted
            _loop_aborted = True

        # -- After fix-loop: handle exhaustion/early-abort ────────────────────────
        if _loop_aborted:
            category = FailureCategory.RETRY_EXHAUSTED
            history_str = "; ".join(
                f"attempt {h['attempt'] + 1}: {h.get('category', '?')}"
                for h in _fix_history
            )
            reason = (
                f"Fix-loop exhausted after {len(_fix_history)} attempt(s)"
                + (f" ({history_str})" if history_str else "")
            )
            sys.stdout.write(str(f"  [fix-loop] {reason} — tagging needs-rework") + "\n")
            sys.stdout.flush()
            try:
                structured_log.error(
                    "fix_loop_exhausted", reason,
                    issue_num=num, fix_history=_fix_history,
                )
            except Exception:
                pass
            issue_state.set_agent_status("failed")
            issue_state.failure_reason = reason
            issue_state.status         = "skipped"
            issue_state.skip_reason    = reason
            issue_state.category       = category
            summary.skipped.append(f"#{num} (fix-loop exhausted)")
            _emit_ticket_failed(
                num, "coder", reason, category,
                project=eff_repo or label, action_id=_run_id, cfg=cfg, sprint_label=label,
            )
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{num} skipped: {category}",
                body=reason,
                issue_num=num,
                category=category,
                cfg=cfg,
                repo=eff_repo,
                sprint_label=label,
            )
            _transition_safe(num, _TicketState.NEEDS_REWORK, actor="sprint_manager", repo_name=eff_repo)
            # Post Gate Failure Analysis comment + sprint log entry for each gate
            # failure recorded during the fix loop (issue #701).
            _publish_gate_failure_analyses(num, repo_name=eff_repo, cfg=cfg)
            _neon_ticket_status(label, num, "failed", _eff_sprints_dir)
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url, project=eff_repo)
            continue

        if _infra_exit:
            # Terminal per-ticket failure (coder crash, divergent-branch, final
            # hang kill, retry-exhausted): surface it as needs-rework on GitHub
            # instead of leaving the ticket stuck on in-progress/SIT for the
            # end-of-run reconcile to flag as "stale status labels"
            # (sprint-lifecycle.md). The TESTER_REJECTED merge-detection race is
            # the documented exception — a re-run resolves it, so it must not be
            # mislabelled needs-rework.
            if getattr(issue_state, "category", None) != FailureCategory.TESTER_REJECTED:
                _transition_safe(
                    num, _TicketState.NEEDS_REWORK,
                    actor="sprint_manager:infra_fail", repo_name=eff_repo,
                )
            continue

        elapsed = time.monotonic() - start_time
        state.wall_clock_secs = elapsed
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url, project=eff_repo)

    # Emit level_complete for the last level
    if _prev_level_idx >= 0:
        try:
            structured_log.event(
                "level_complete",
                run_id=_run_id,
                issue_num=None,
                sprint_label=label,
                agent_role="sprint",
                level_index=_prev_level_idx,
                merged=summary.merged[_level_merged_before:],
                skipped=summary.skipped[_level_skipped_before:],
                total=len(_level_nums_by_idx[_prev_level_idx]),
            )
        except Exception:
            pass



def run_sprint(
    label: str,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    gate_typecheck: bool = True,
    gate_design: bool = True,
    gate_frontend_lint: bool = True,
    gate_monolith: bool = True,
    alert_modes: Optional[list[str]] = None,
    repo_name: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = False,
    retry_failed: bool = False,
    target_branch: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    preflight_approved: Optional[list[int]] = None,
    gate_scope: str = "changed",
    token_budget: int = 0,
    skip_estimator: bool = True,
    rerun_manifest: Optional[dict] = None,
    pipeline_mode: Optional[bool] = None,
    max_coder_slots: Optional[int] = None,
    max_tester_slots: Optional[int] = None,
) -> tuple[SprintSummary, SprintState]:
    """Main sprint loop -- processes backlog issues sequentially.

    Returns (SprintSummary, SprintState).
    Supports resume/retry_failed from persisted state.

    target_branch: branch to merge feature branches into after gates pass.
    Defaults to this sprint's branch (sprint/sprint-N or sprint/sprint-N.M).
    Chain promotion (child → base → develop) happens only at Merge Sprint.
    Pass 'develop' to override (AC-5 #269).

    preflight_approved: optional list of issue numbers approved by the pre-flight
    review. When provided, only issues in this list are dispatched; others are
    skipped with reason 'preflight-skipped'.

    gate_scope: 'changed' (default) scopes pytest/lint gates to files changed
    relative to the base branch; 'full' restores legacy full-codebase behaviour.
    """
    if alert_modes is None:
        alert_modes = [AlertMode.DASHBOARD_BANNER]

    pf = run_sprint_preflight(
        label=label,
        alert_modes=alert_modes,
        repo_name=repo_name,
        dry_run=dry_run,
        resume=resume,
        retry_failed=retry_failed,
        target_branch=target_branch,
        cfg=cfg,
        token_budget=token_budget,
        skip_estimator=skip_estimator,
        rerun_manifest=rerun_manifest,
        pipeline_mode=pipeline_mode,
        max_coder_slots=max_coder_slots,
        max_tester_slots=max_tester_slots,
    )
    if pf.early_exit:
        return pf.summary, pf.state

    state              = pf.state
    state_path         = pf.state_path
    summary            = pf.summary
    sprint_num         = pf.sprint_num
    sprint_branch      = pf.sprint_branch
    target_branch      = pf.target_branch
    eff_repo           = pf.eff_repo
    api_url            = pf.api_url
    _run_id            = pf.run_id
    rerun_decisions    = pf.rerun_decisions
    _eff_sprints_dir   = pf.eff_sprints_dir
    _dispatch_levels   = pf.dispatch_levels
    _level_nums_by_idx = pf.level_nums_by_idx
    _pipeline_on       = pf.pipeline_on
    start_time         = pf.start_time
    total_issues       = len(state.issues)

    if _pipeline_on:
        _run_pipeline_dispatch(
            state=state, state_path=state_path, summary=summary,
            dispatch_levels=_dispatch_levels, level_nums_by_idx=_level_nums_by_idx,
            label=label, sprint_num=sprint_num, eff_repo=eff_repo, api_url=api_url,
            target_branch=target_branch, sprint_branch=sprint_branch,
            alert_modes=alert_modes, cfg=cfg, run_id=_run_id,
            eff_sprints_dir=_eff_sprints_dir, rerun_decisions=rerun_decisions,
            skip_gates=skip_gates, gate_pytest=gate_pytest, gate_lint=gate_lint,
            gate_merge_preview=gate_merge_preview, gate_typecheck=gate_typecheck,
            gate_design=gate_design, gate_frontend_lint=gate_frontend_lint,
            gate_monolith=gate_monolith,
            gate_scope=gate_scope, resume=resume, retry_failed=retry_failed,
        )

    run_sprint_loop(
        pf,
        label=label,
        preflight_approved=preflight_approved,
        dry_run=dry_run,
        resume=resume,
        retry_failed=retry_failed,
        skip_gates=skip_gates,
        gate_pytest=gate_pytest,
        gate_lint=gate_lint,
        gate_merge_preview=gate_merge_preview,
        gate_typecheck=gate_typecheck,
        gate_design=gate_design,
        gate_frontend_lint=gate_frontend_lint,
        gate_monolith=gate_monolith,
        gate_scope=gate_scope,
        alert_modes=alert_modes,
        cfg=cfg,
    )

    # Final elapsed time
    state.wall_clock_secs = time.monotonic() - start_time
    _neon_sprint_status(label, "complete", _eff_sprints_dir)
    state.save(state_path)
    _emit_sprint_lifecycle_event(
        type="sprint_finished",
        target=f"sprint-{sprint_num}" if sprint_num is not None else label,
        actor="system",
        detail={
            "done": len(summary.merged),
            "failed": len(summary.gate_failures),
            "skipped": len(summary.skipped),
            "duration": round(state.wall_clock_secs),
        },
        project=eff_repo or label,
        action_id=_run_id,
    )

    # Auto-refresh calibration cache with newly completed sprint data (issue #1333)
    try:
        from sizing import SIZE_TO_MINUTES as _SIZE_TO_MINUTES  # noqa: PLC0415
        _cal_project_root = _eff_sprints_dir.parent.parent
        _cal_minutes = dict(_SIZE_TO_MINUTES)
        _run_calibration_cache_refresh(_cal_project_root, _cal_minutes, project=eff_repo or label)
    except Exception:
        pass

    # Run documentor once for all merged tickets, before reviewer (issue #697)
    _eff_documentor = cfg.documentor_enabled if cfg is not None else False
    if _eff_documentor and summary.merged:
        _merged_issue_nums = [
            int(s.lstrip("#").split()[0])
            for s in summary.merged
            if s.lstrip("#").split()[0].isdigit()
        ]
        if _merged_issue_nums:
            _run_documentor(_merged_issue_nums, label, eff_repo, cfg=cfg)

    # ── Worktree pool teardown (issue #1411) ───────────────────────────────────
    # Tear down the pool at sprint end so no stray worktrees remain.
    global _ACTIVE_WORKTREE_POOL
    if _ACTIVE_WORKTREE_POOL is not None:
        try:
            _ACTIVE_WORKTREE_POOL.teardown()
        except Exception as _pool_ex:
            sys.stdout.write(str(f"  [worktree-pool] WARNING: teardown error: {_pool_ex}\n"))
        finally:
            _ACTIVE_WORKTREE_POOL = None  # type: ignore[assignment]

    return summary, state


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Sprint Manager -- orchestrate coder+tester agents with gates, "
                    "failure categorisation, hang detection, and alert channels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("label", help="GitHub label identifying the sprint (e.g. sprint-5)")
    p.add_argument("--repo", default=None, help="owner/repo override")

    # Config file (AC-4)
    p.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "Path to .commander/sprint.yaml config file.  "
            "When provided, all path/repo settings are read from it.  "
            "Incompatible with env-var fallback path."
        ),
    )

    # Gate control flags
    p.add_argument(
        "--skip-gates",
        action="store_true",
        default=False,
        help="Skip all quality gates and force auto-merge after tester passes",
    )
    p.add_argument(
        "--gate-typecheck",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("COMMANDER_GATE_TYPECHECK", "1") != "0",
        help="Enable/disable typecheck gate — mypy/tsc (default: enabled; env: COMMANDER_GATE_TYPECHECK=0 to disable)",
    )
    p.add_argument(
        "--gate-lint",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable lint gate — ruff + eslint/prettier (default: enabled)",
    )
    p.add_argument(
        "--gate-frontend-lint",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("COMMANDER_GATE_FRONTEND_LINT", "1") != "0",
        help="Enable/disable frontend lint portion — eslint/biome + prettier (default: enabled; env: COMMANDER_GATE_FRONTEND_LINT=0 to disable)",
    )
    p.add_argument(
        "--gate-design",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("COMMANDER_GATE_DESIGN", "1") != "0",
        help="Enable/disable design gate — impeccable UI anti-pattern detector (default: enabled; env: COMMANDER_GATE_DESIGN=0 to disable)",
    )
    p.add_argument(
        "--gate-pytest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable pytest gate (default: enabled)",
    )
    p.add_argument(
        "--gate-monolith",
        action=argparse.BooleanOptionalAction,
        default=_monolith_gate_enabled(),
        help="Enable/disable monolith gate — rejects server.py growth (default: enabled; env: COMMANDER_GATE_MONOLITH=false to disable)",
    )
    p.add_argument(
        "--gate-merge-preview",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable merge-preview gate (default: enabled)",
    )

    # Sprint control flags
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List issues but do not dispatch agents",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume from existing state file, skipping done/skipped issues",
    )
    p.add_argument(
        "--retry-failed",
        action="store_true",
        default=False,
        help="Re-dispatch only skipped/failed issues from existing state",
    )
    p.add_argument(
        "--target-branch",
        default=None,
        help=(
            "Branch to merge feature branches into. "
            "Defaults to sprint/<label>. "
            "Pass 'develop' to restore legacy behaviour."
        ),
    )

    # Token budget (AC-1)
    p.add_argument(
        "--budget",
        type=int,
        default=0,
        metavar="TOKENS",
        help="Token estimate from the planning view. Stored in sprint state and broadcast to dashboard.",
    )

    # Gate scope (AC-9)
    p.add_argument(
        "--gate-scope",
        default="changed",
        choices=["changed", "full"],
        help=(
            "Scope for pytest and lint gates. "
            "'changed' (default): only check files changed relative to the base branch. "
            "'full': run pytest -x and ruff check . against the whole codebase (legacy behaviour)."
        ),
    )

    # Alert modes (AC-3)
    p.add_argument(
        "--alert-mode",
        default=AlertMode.DASHBOARD_BANNER,
        help=(
            "Comma-separated alert modes: "
            "dashboard-banner, email, discord, file, none  (default: dashboard-banner)"
        ),
    )

    # Pre-flight review
    p.add_argument(
        "--preflight",
        action="store_true",
        default=False,
        help=(
            "Run sprint_review.py pre-flight BA check before dispatching any issues. "
            "Aborts the sprint run (exit 1) if the user quits from the prompt."
        ),
    )

    # Summary override
    p.add_argument(
        "--force-summary",
        action="store_true",
        default=False,
        help=(
            "Always update the sprint summary GitHub issue regardless of whether an "
            "existing issue is detected as valid or stale."
        ),
    )

    # Reviewer control (issue #159)
    p.add_argument(
        "--skip-reviewer",
        action="store_true",
        default=False,
        help="Skip the post-sprint reviewer agent entirely.",
    )

    # Documenter control (issue #165)
    p.add_argument(
        "--skip-documenter",
        action="store_true",
        default=False,
        help="Skip the post-summary documenter agent entirely.",
    )

    # Estimator control (issue #166, #696, #704) — default skips; --no-skip-estimator opts in
    p.add_argument(
        "--skip-estimator",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip the estimator run (on by default; pass --no-skip-estimator to enable).",
    )

    # Sprint-level slot overrides (issue #1415) — take precedence over project settings.
    p.add_argument(
        "--max-coder-slots",
        type=int,
        default=None,
        metavar="N",
        help="Override max concurrent coder slots for this run (overrides sprint.yaml setting).",
    )
    p.add_argument(
        "--max-tester-slots",
        type=int,
        default=None,
        metavar="N",
        help="Override max concurrent tester slots for this run (overrides sprint.yaml setting).",
    )

    # Rerun manifest (issue #332) — written by the server rerun endpoint
    p.add_argument(
        "--rerun-manifest",
        default=None,
        metavar="PATH",
        help=argparse.SUPPRESS,
    )

    args = p.parse_args()
    install_orchestrator_stdout_timestamps()

    # ── Config resolution (AC-4 + AC-5 + AC-6) ───────────────────────────────
    cfg: Optional[SprintConfig] = None

    if args.config:
        # Explicit --config flag (AC-4)
        config_path = Path(args.config).expanduser().resolve()
        if not config_path.exists():
            p.error(f"Config file not found: {config_path}")
        sys.stdout.write(str(f"  Using config: {config_path}") + "\n")
        cfg = load_config(config_path)
    else:
        # Auto-discovery: walk up from CWD (AC-5)
        discovered = discover_config()
        if discovered:
            sys.stdout.write(str(f"  Auto-discovered config: {discovered}") + "\n")
            cfg = load_config(discovered)
        else:
            # Backward-compatible default (AC-6)
            cfg = _default_config()

    raw_modes   = [m.strip() for m in args.alert_mode.split(",") if m.strip()]
    alert_modes = []
    for m in raw_modes:
        if m not in AlertMode.ALL_MODES:
            p.error(f"Unknown alert mode: {m!r}. Valid: {', '.join(sorted(AlertMode.ALL_MODES))}")
        alert_modes.append(m)

    if not alert_modes:
        alert_modes = [AlertMode.DASHBOARD_BANNER]

    # --repo flag overrides config (explicit always wins)
    eff_repo = args.repo or (cfg.repo_name if cfg else None)

    # ── Per-project PID lock (issues #122, #155) ─────────────────────────────
    # Both dispatch paths (HTTP server and CLI) use the same locking protocol.
    # When dispatched by the server the server has already atomically claimed
    # the slot by writing <label>-pid (via two-phase rename from -pid.pending).
    # _acquire_pid_lock detects our own PID in that file and skips the
    # "already running" guard, then re-confirms the file still points to us.
    # On CLI dispatch the file does not yet exist and is created fresh.
    _project_id = eff_repo or _r(None)
    _pid_path = _acquire_pid_lock(args.label, _project_id, cfg=cfg)

    def _cleanup_pid() -> None:
        _release_pid_lock(_pid_path)

    atexit.register(_cleanup_pid)

    def _sigterm_handler(signum: int, frame: object) -> None:
        _sprint_user_cancelled.set()
        _cleanup_pid()
        # Best-effort state write before exit. A user cancel is a needs_rework
        # ending under the unified lifecycle — the "why" lives in end_reason.
        _ended_at = datetime.now(timezone.utc).isoformat()
        _plan_json_set_state_sm(
            args.label, "needs_rework", cfg=cfg,
            ended_at=_ended_at, end_reason="stopped by user",
        )
        _sprint_db_set_state_sm(
            args.label, "needs_rework", project=eff_repo or "",
            ended_at=_ended_at, end_reason="stopped by user",
        )
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # Write state=running now that PID lock is confirmed (issue #507 plan.json,
    # #757 DB)
    if not args.dry_run:
        _started_at = datetime.now(timezone.utc).isoformat()
        _plan_json_set_state_sm(
            args.label, "running", cfg=cfg,
            started_at=_started_at,
        )
        _sprint_db_set_state_sm(
            args.label, "running", project=eff_repo or "",
            started_at=_started_at,
        )

    # ── Pre-flight review (AC-1 of issue #33) ────────────────────────────────
    preflight_approved: Optional[list] = None  # None = no preflight, list = approved numbers
    if args.preflight:
        from sprint_review import run_preflight  # lazy import — only needed with --preflight
        sprints_dir = cfg.sprints_dir if cfg else SPRINTS_DIR
        _all_results, approved = run_preflight(
            sprint_label = args.label,
            repo_name    = eff_repo,
            sprints_dir  = sprints_dir,
            interactive  = True,
        )
        # run_preflight exits(1) if user chose Q — if we reach here, proceed
        preflight_approved = [r.number for r in approved]
        sys.stdout.write(str(f"[preflight] Approved {len(preflight_approved)} issue(s) for this run.") + "\n")

    _rerun_manifest: Optional[dict] = None
    if args.rerun_manifest:
        _manifest_path = Path(args.rerun_manifest)
        if not _manifest_path.exists():
            p.error(f"Rerun manifest not found: {_manifest_path}")
        _rerun_manifest = json.loads(_manifest_path.read_text(encoding="utf-8"))

    # Pre-initialize so the except block can reference them if run_sprint() raises.
    summary: Optional[SprintSummary] = None
    state: Optional[SprintState] = None
    summary_path: Optional[Path] = None
    sprint_branch: str = f"sprint/{args.label}"
    effective_target: str = args.target_branch or sprint_branch

    try:
        summary, state = run_sprint(
            label                = args.label,
            skip_gates           = args.skip_gates,
            gate_pytest          = args.gate_pytest,
            gate_lint            = args.gate_lint,
            gate_merge_preview   = args.gate_merge_preview,
            gate_typecheck       = args.gate_typecheck,
            gate_design          = args.gate_design,
            gate_frontend_lint   = args.gate_frontend_lint,
            gate_monolith        = args.gate_monolith,
            alert_modes          = alert_modes,
            repo_name            = eff_repo,
            dry_run              = args.dry_run,
            resume               = args.resume,
            retry_failed         = args.retry_failed,
            target_branch        = args.target_branch,
            cfg                  = cfg,
            preflight_approved   = preflight_approved,
            gate_scope           = args.gate_scope,
            token_budget         = args.budget,
            skip_estimator       = args.skip_estimator,
            rerun_manifest       = _rerun_manifest,
            max_coder_slots      = args.max_coder_slots,
            max_tester_slots     = args.max_tester_slots,
        )

        # Derive sprint_branch for summary (mirrors run_sprint logic)
        sprint_branch = f"sprint/{args.label}"
        effective_target = args.target_branch or sprint_branch

        # AC-1/2/3: write extended summary, create GitHub issue, prompt for learnings
        if state.issues and not args.dry_run:
            end_reason   = "complete" if not summary.skipped else "stopped"
            summary_path = write_sprint_summary(
                state         = state,
                elapsed_secs  = state.wall_clock_secs,
                alert_modes   = alert_modes,
                end_reason    = end_reason,
                repo_name     = eff_repo,
                cfg           = cfg,
                sprint_branch = effective_target,
                dry_run       = args.dry_run,
                force_summary = args.force_summary,
                merge_target  = effective_target,
            )
        else:
            summary_path = None

        if args.dry_run and state is not None:
            try:
                _dry_state = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
                if _dry_state.exists():
                    _dry_state.unlink()
            except Exception:
                pass

    except SystemExit:
        if _sprint_user_cancelled.is_set():
            # Race condition (issue #365 AC-4): SIGTERM arrived while write_sprint_summary
            # was executing inside create_summary_github_issue.  Close any open summary
            # issue that was created in this run before the signal was processed.
            _close_cancelled_sprint_summary(
                state.sprint_number if state is not None else None,
                args.label,
                eff_repo,
            )
        raise
    except Exception as _crash_exc:
        # Crash catch-all (logging only): turn a silent sprint death into a
        # logged stack trace, then re-raise so behavior is unchanged.
        structured_log.error(
            "sprint_crashed",
            f"sprint {args.label} crashed: {_crash_exc}",
            sprint_label=args.label,
            project=eff_repo,
            error=str(_crash_exc),
            traceback=traceback.format_exc(),
        )
        raise

    # Clean exit: write the unified-lifecycle terminal state at the source
    # (sprint-lifecycle.md): ready_to_merge when every ticket passed,
    # needs_rework on any recorded failure — no more inference downstream.
    if not args.dry_run:
        _ended_at = datetime.now(timezone.utc).isoformat()
        if not state or not state.issues:
            _terminal_state = "needs_rework"
            _terminal_reason = "no-dispatchable-tickets"
        else:
            # A sprint is a clean (ready_to_merge) finish only when every ticket
            # landed. Besides an explicit agent failure, a ticket left SKIPPED by a
            # failure category — RETRY_EXHAUSTED / TESTER_REJECTED / gate fail — is
            # unfinished work that did NOT merge, so it must drive needs_rework
            # (rerunnable), not let the sprint complete "naturally". Those infra
            # categories deliberately skip the per-ticket needs-rework *label*
            # (see ~line 1441), but at the sprint level an unmerged failing ticket
            # still means the sprint has rework to do. A clean operator skip (no
            # failure category) is not a failure and stays natural.
            _any_failed = any(
                (iss.agent_status == "failed")
                or iss.failure_reason
                or (iss.status == "skipped" and iss.category)
                for iss in state.issues
            )
            if _any_failed:
                _terminal_state = "needs_rework"
                _terminal_reason = "ticket-failures"
            else:
                _terminal_state = "ready_to_merge"
                _terminal_reason = "natural"
        _plan_json_set_state_sm(
            args.label, _terminal_state, cfg=cfg,
            ended_at=_ended_at,
            end_reason=_terminal_reason,
        )
        _sprint_db_set_state_sm(
            args.label, _terminal_state, project=eff_repo or "",
            ended_at=_ended_at, end_reason=_terminal_reason,
        )

    # Regenerate STATUS.md after sprint closes (#584)
    _regenerate_status_md(cfg=cfg, dry_run=args.dry_run)

    # Dispatch documenter after sprint summary, before sprint PR (issue #165)
    if not args.skip_documenter and not args.dry_run and state.issues:
        doc_cwd = str(cfg.worktree_tester) if cfg else None
        try:
            r_head = subprocess.run(
                ["git", "rev-parse", f"origin/{effective_target}"],
                capture_output=True, text=True, check=False, cwd=doc_cwd,
            )
            r_base = subprocess.run(
                ["git", "merge-base", f"origin/{effective_target}", "origin/develop"],
                capture_output=True, text=True, check=False, cwd=doc_cwd,
            )
            doc_head_sha = r_head.stdout.strip() or "HEAD"
            doc_base_sha = r_base.stdout.strip() or "develop"
        except Exception:
            doc_head_sha = "HEAD"
            doc_base_sha = "develop"

        state_path_doc = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
        # issue #764: track documenter as a sprint-level agent run (issue 0).
        _db_agent_start_sm(0, state.sprint_label, "documenter")
        _doc_t0 = time.monotonic()
        try:
            _dispatch_documenter(
                state         = state,
                sprint_branch = effective_target,
                base_sha      = doc_base_sha,
                head_sha      = doc_head_sha,
                cfg           = cfg,
                repo_name     = eff_repo,
                merge_target  = effective_target,
            )
            _db_agent_finish_sm(
                0, state.sprint_label, "documenter",
                duration_seconds=time.monotonic() - _doc_t0,
                outcome=state.documenter_status or "succeeded",
            )
        except RuntimeError as e_doc:
            # AC6: documenter errors must fail the pipeline loudly, not silently skip
            _db_agent_finish_sm(
                0, state.sprint_label, "documenter",
                duration_seconds=time.monotonic() - _doc_t0, outcome="failed",
            )
            structured_log.error("documenter_failed", f"documenter failed: {e_doc}", exc=str(e_doc))
            sys.stdout.write(str(f"\n[ERROR] Documenter failed: {e_doc}") + "\n")
            sys.stdout.flush()
            raise

        # Persist documenter outcome into the state JSON
        if state_path_doc.exists():
            try:
                sd3 = json.loads(state_path_doc.read_text())
                sd3["documenter_status"]        = state.documenter_status
                sd3["documenter_files_touched"] = state.documenter_files_touched
                sd3["documenter_commit_sha"]    = state.documenter_commit_sha
                state_path_doc.write_text(json.dumps(sd3, indent=2))
            except Exception as e_persist:
                structured_log.warn("documenter_state_persist_failed", f"could not persist documenter outcome: {e_persist}", exc=str(e_persist))

    # Write per-sprint brief after documenter (issue #860)
    if not args.dry_run and state.issues and _BRIEF_GENERATOR_AVAILABLE:
        _brief_git_root = cfg.worktree_tester if cfg else Path.cwd()
        _brief_state_path = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
        _brief_summary_issue_num: Optional[int] = None
        if _brief_state_path.exists():
            try:
                _bsd = json.loads(_brief_state_path.read_text())
                _burl = _bsd.get("summary_issue_url", "")
                _bm = re.search(r"/issues/(\d+)$", _burl)
                if _bm:
                    _brief_summary_issue_num = int(_bm.group(1))
            except Exception:
                pass
        try:
            _write_sprint_brief(
                state             = state,
                repo_name         = eff_repo,
                git_root          = Path(_brief_git_root),
                summary_issue_num = _brief_summary_issue_num,
            )
        except Exception as e_brief:
            structured_log.warn("brief_generator_failed", f"write_sprint_brief failed (non-fatal): {e_brief}", exc=str(e_brief))
            sys.stdout.write(str(f"  [brief_generator] WARNING: brief generation failed: {e_brief}") + "\n")

    # AC6, AC7: child sprints get an open PR into the base branch at sprint end.
    # develop is reached only at Merge Sprint — no auto-merge here.
    sprint_pr_url: Optional[str] = None
    if (
        state.issues
        and not args.dry_run
        and args.target_branch != "develop"
        and _is_child_sprint_label(args.label)
    ):
        sprint_pr_url = _create_sprint_pr(
            sprint_branch  = sprint_branch,
            sprint_label   = args.label,
            sprint_number  = _sprint_number(args.label),
            state          = state,
            repo_name      = eff_repo,
            pr_base        = _base_sprint_branch(args.label),
            merge_target   = effective_target,
        )

    # Dispatch reviewer after sprint PR creation (issue #159)
    if not args.skip_reviewer and not args.dry_run and state.issues:
        rev_cwd = str(cfg.worktree_coder) if cfg else None
        try:
            r_head = subprocess.run(
                ["git", "rev-parse", f"origin/{effective_target}"],
                capture_output=True, text=True, check=False, cwd=rev_cwd,
            )
            r_base = subprocess.run(
                ["git", "merge-base", f"origin/{effective_target}", "origin/develop"],
                capture_output=True, text=True, check=False, cwd=rev_cwd,
            )
            head_sha = r_head.stdout.strip() or "HEAD"
            base_sha = r_base.stdout.strip() or "develop"
        except Exception:
            head_sha = "HEAD"
            base_sha = "develop"

        # Read summary_issue_num from state JSON (set by write_sprint_summary)
        summary_issue_num_for_reviewer: Optional[int] = None
        state_path_rev = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
        if state_path_rev.exists():
            try:
                sd = json.loads(state_path_rev.read_text())
                surl = sd.get("summary_issue_url", "")
                m_issue = re.search(r"/issues/(\d+)$", surl)
                if m_issue:
                    summary_issue_num_for_reviewer = int(m_issue.group(1))
            except Exception:
                pass

        # issue #764: track reviewer as a sprint-level agent run (issue 0).
        _db_agent_start_sm(0, state.sprint_label, "reviewer")
        _rev_t0 = time.monotonic()
        try:
            _dispatch_reviewer(
                state             = state,
                summary_issue_num = summary_issue_num_for_reviewer,
                sprint_branch     = effective_target,
                base_sha          = base_sha,
                head_sha          = head_sha,
                cfg               = cfg,
                repo_name         = eff_repo,
                merge_target      = effective_target,
            )
            _db_agent_finish_sm(
                0, state.sprint_label, "reviewer",
                duration_seconds=time.monotonic() - _rev_t0,
                outcome=state.reviewer_status or "succeeded",
            )
            # Persist reviewer outcome into the state JSON
            if state_path_rev.exists():
                try:
                    sd2 = json.loads(state_path_rev.read_text())
                    sd2["reviewer_status"]      = state.reviewer_status
                    sd2["reviewer_comment_url"] = state.reviewer_comment_url
                    sd2["reviewer_findings"]    = state.reviewer_findings
                    state_path_rev.write_text(json.dumps(sd2, indent=2))
                except Exception as e_persist:
                    structured_log.warn("reviewer_state_persist_failed", f"could not persist reviewer outcome: {e_persist}", exc=str(e_persist))
        except Exception as e_rev:
            _db_agent_finish_sm(
                0, state.sprint_label, "reviewer",
                duration_seconds=time.monotonic() - _rev_t0, outcome="failed",
            )
            structured_log.error("reviewer_failed", f"reviewer stage failed: {e_rev}", exc=str(e_rev))
            state.reviewer_status = "failed"

        # Enrich follow-up tickets with BA rewrite + estimator
        follow_ups = (state.reviewer_findings or {}).get("follow_up_tickets", [])
        if follow_ups and eff_repo:
            _enrich_followup_tickets(
                follow_up_tickets = follow_ups,
                eff_repo          = eff_repo,
                cfg               = cfg,
                state             = state,
            )

    # Post-sprint reconciliation (issue #856): surface loose ends — a missing
    # summary issue, an unmerged sprint PR, or stale status labels. Runs for any
    # finished sprint (explicit Finish or natural end) since both reach here.
    # Read-only and best-effort: never fail the sprint over reconciliation.
    if state is not None and state.issues and not args.dry_run:
        try:
            from services.sprint_manager.reconciliation import (
                gather_inputs_via_gh,
                run_reconciliation,
            )
            rec_state_path = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
            rec_inputs = gather_inputs_via_gh(
                state.sprint_label,
                eff_repo,
                sprint_pr_url,
                [i.number for i in state.issues],
            )
            rec_result = run_reconciliation(
                sprint_label   = state.sprint_label,
                sprint_number  = state.sprint_number,
                project        = eff_repo or state.sprint_label,
                state_path     = rec_state_path,
                summary_issues = rec_inputs["summary_issues"],
                pr_info        = rec_inputs["pr_info"],
                tickets        = rec_inputs["tickets"],
                emit_event     = _emit_sprint_lifecycle_event,
                action_id      = os.environ.get("COMMANDER_RUN_ID"),
            )
            state.reconciliation = rec_result
            _status = "all clear" if rec_result["all_clear"] else "loose ends found"
            sys.stdout.write(str(f"  Reconciliation: {_status}") + "\n")
        except Exception as _e_rec:
            structured_log.warn(
                "reconciliation_failed",
                f"post-sprint reconciliation failed: {_e_rec}",
                exc=str(_e_rec),
            )

    # Persist final state + ingest run artifacts into DB (lifecycle P3).
    if state is not None and not args.dry_run:
        rec_state_path = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
        try:
            state.save(rec_state_path)
        except Exception:
            pass
        _sprint_db_ingest_run_sm(
            state.sprint_label,
            state,
            project=eff_repo or "",
            summary_path=str(summary_path) if summary_path else None,
            cfg=cfg,
        )

    sys.stdout.write(str("\n=== Sprint Summary ===") + "\n")
    sys.stdout.write(str(f"Processed: {', '.join(summary.processed) or 'none'}") + "\n")
    sys.stdout.write(str(f"Merged:    {', '.join(summary.merged) or 'none'}") + "\n")
    if summary.gate_failures:
        sys.stdout.write(str("Gate failures:") + "\n")
        for line in summary.gate_failures:
            sys.stdout.write(str(f"  {line}") + "\n")
    if summary.skipped:
        sys.stdout.write(str(f"Skipped:   {', '.join(summary.skipped)}") + "\n")
    if summary_path:
        sys.stdout.write(str(f"Summary:   {summary_path}") + "\n")
    if sprint_pr_url:
        sys.stdout.write(str(f"Sprint PR: {sprint_pr_url}") + "\n")


if __name__ == "__main__":
    main()
