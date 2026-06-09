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
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5 --skip-gates
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5 --gate-pytest=false
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5 --alert-mode dashboard-banner,file
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5 --resume
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5 --retry-failed
    python3 ~/commander/services/sprint_manager/sprint_manager.py sprint-5 --dry-run

Run from the git root of the repository.
"""
from __future__ import annotations

import argparse
import atexit
import email.mime.text
import json
import os
import re
import signal
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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

try:
    from services.sprint_manager import sprint_repo as _sprint_repo
    _SPRINT_REPO_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    _sprint_repo = None  # type: ignore[assignment]
    _SPRINT_REPO_AVAILABLE = False

try:
    from services.sprint_manager.state_machine import (  # noqa: PLC0415
        transition as _sm_transition,
        TicketState as _TicketState,
        TransitionError as _TransitionError,
    )
    _STATE_MACHINE_AVAILABLE = True
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    _sm_transition = None  # type: ignore[assignment]
    _TicketState = None  # type: ignore[assignment]
    _TransitionError = Exception  # type: ignore[assignment,misc]
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


def _load_agent_persona(role: str, base_dir: "Path | None" = None) -> str:
    """Return the .claude/agents/<role>.md persona (minus YAML frontmatter).

    A headless ``claude -p`` run does NOT auto-load project subagents and cannot
    use the interactive ``/coder`` // ``/tester`` slash commands, so without this
    the dispatched agent runs with only a terse inline prompt — markedly weaker
    than a manual session that delegates to the rich subagent persona. We read
    the same .md the interactive agent uses and pass it via --append-system-prompt.

    Looks in ``base_dir`` (the clone the agent runs in) first, then REPO_ROOT.
    Returns "" if not found, so callers degrade gracefully to the old behaviour.
    """
    candidates = []
    if base_dir is not None:
        candidates.append(Path(base_dir) / ".claude" / "agents" / f"{role}.md")
    candidates.append(REPO_ROOT / ".claude" / "agents" / f"{role}.md")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Strip leading YAML frontmatter (--- ... ---) if present.
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                text = text[end + 4:]
        text = text.strip()
        if text:
            return text
    return ""

sys.path.insert(0, str(REPO_ROOT))      # allow `from services.*` imports
sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(DASHBOARD_DIR / ".env")
import github_client

from services.run_id import mint_run_id
from services.logging import log as structured_log
from services.sprint_manager import agent_browser_runner  # issue #710: live-browser UAT

try:
    from db import record_event as _db_record_event  # type: ignore[import]
    _RECORD_EVENT_AVAILABLE = True
except ImportError:
    _db_record_event = None  # type: ignore[assignment]
    _RECORD_EVENT_AVAILABLE = False

# Import failure-parsing helpers from post_test_report (no circular deps)
try:
    sys.path.insert(0, str(SCRIPTS_DIR))
    from post_test_report import (  # type: ignore[import]
        parse_failures,
        build_failure_block,
        write_sidecar,
        sidecar_path,
    )
    _FAILURE_PARSING_AVAILABLE = True
except ImportError:
    _FAILURE_PARSING_AVAILABLE = False

# Default paths — can be overridden via env vars or CLI for testing
WORKTESTER_ROOT      = Path(os.environ.get("WORKTESTER_ROOT",
                             Path.home() / "commander" / "work-tester"))
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

# Set by _sigterm_handler when the user cancels the sprint (issue #365, #514).
# Checked in write_sprint_summary and in main()'s SystemExit handler.
# threading.Event for thread-safe signaling across worker threads.
_sprint_user_cancelled: threading.Event = threading.Event()


def _emit_sprint_lifecycle_event(
    type: str,
    target: str,
    actor: str,
    detail: dict,
    project: str,
    action_id: "str | None" = None,
) -> None:
    """Write one lifecycle event to the events table. Silently no-ops on any error."""
    if not _RECORD_EVENT_AVAILABLE or _db_record_event is None:
        return
    try:
        _db_record_event(
            project=project,
            source="agent",
            actor=actor,
            type=type,
            target=target,
            detail=detail,
            action_id=action_id,
        )
    except Exception:
        pass


# ── SprintConfig dataclass + loader ──────────────────────────────────────────

@dataclass
class SprintConfig:
    """All runtime paths and settings for a sprint.

    Replaces the six module-level path constants.  When no config file is
    present the class is populated with the same env-var + hardcoded defaults
    that existed before this ticket, so backward compatibility is preserved.
    """
    repo_name:             Optional[str]  = None
    worktree_coder:        Path           = field(default_factory=lambda: Path.home() / "commander" / "work-coder")
    worktree_tester:       Path           = field(default_factory=lambda: WORKTESTER_ROOT)
    tester_app_subdir:     str            = "apps/dashboard"
    scripts_dir:           Path           = field(default_factory=lambda: SCRIPTS_DIR)
    logs_dir:              Path           = field(default_factory=lambda: DASHBOARD_DIR / "logs")
    sprints_dir:           Path           = field(default_factory=lambda: SPRINTS_DIR)
    alerts_dir:            Path           = field(default_factory=lambda: ALERTS_DIR)
    api_url:               str            = field(default_factory=lambda: DASHBOARD_API_URL)
    coder_prompt_template:  Optional[str] = None
    tester_prompt_template: Optional[str] = None
    # Port detection (issue #62)
    app_default_port:      Optional[int]  = None
    app_port_strategy:     str            = "prefer_default"
    # Documentor (issue #103)
    documentor_enabled:    bool           = False
    # Reviewer (issue #159)
    reviewer_prompt_template: Optional[str] = None
    # Documenter (issue #165)
    documenter_prompt_template: Optional[str] = None
    # Agent models (issue #700) — defaults match current hardcoded values
    coder_model:       str = "claude-sonnet-4-6"
    tester_model:      str = "claude-sonnet-4-6"
    reviewer_model:    str = "claude-haiku-4-5"
    estimator_model:   str = "claude-sonnet-4-6"
    documentor_model:  str = "claude-sonnet-4-6"

    @property
    def worktree_tester_app(self) -> Path:
        """Resolved path where tests/app lives inside the tester worktree."""
        if self.tester_app_subdir:
            return self.worktree_tester / self.tester_app_subdir
        return self.worktree_tester

    @property
    def finish_feature_script(self) -> Path:
        return self.scripts_dir / "finish_feature.py"


def _resolve_path(raw: str, base_dir: Path) -> Path:
    """Expand ~ and resolve relative paths against base_dir."""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def load_config(path: Path) -> "SprintConfig":
    """Parse .commander/sprint.yaml and return a SprintConfig.

    Relative paths in paths.* are resolved relative to the YAML file's
    directory.  Raises SystemExit on validation errors.
    """
    if yaml is None:
        sys.exit("PyYAML is not installed. Install it with: pip install pyyaml")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"Cannot read config file {path}: {e}")

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        sys.exit(f"YAML parse error in {path}: {e}")

    base_dir = path.parent  # directory containing sprint.yaml

    # ── required fields ───────────────────────────────────────────────────────
    missing = []
    repo_name = os.environ.get("COMMANDER_REPO", "").strip() or (data.get("repo_name") or "").strip()
    if not repo_name:
        missing.append("repo_name")

    wt = data.get("worktrees") or {}
    coder_raw  = (wt.get("coder") or "").strip()
    tester_raw = (wt.get("tester") or "").strip()
    if not coder_raw:
        missing.append("worktrees.coder")
    if not tester_raw:
        missing.append("worktrees.tester")

    if missing:
        sys.exit(
            f"Config file {path} is missing required field(s): "
            + ", ".join(missing)
        )

    worktree_coder  = _resolve_path(coder_raw, base_dir)
    worktree_tester = _resolve_path(tester_raw, base_dir)
    tester_app_subdir = (wt.get("tester_app_subdir") or "")

    # ── validate worktree paths ────────────────────────────────────────────────
    path_errors = []
    if not worktree_coder.exists():
        path_errors.append(f"worktrees.coder path does not exist: {worktree_coder}")
    if not worktree_tester.exists():
        path_errors.append(f"worktrees.tester path does not exist: {worktree_tester}")
    if path_errors:
        sys.exit("Config validation error:\n  " + "\n  ".join(path_errors))

    # ── optional paths ────────────────────────────────────────────────────────
    paths = data.get("paths") or {}

    scripts_raw = (paths.get("scripts_dir") or "").strip()
    scripts_dir = _resolve_path(scripts_raw, base_dir) if scripts_raw else SCRIPTS_DIR

    logs_raw  = (paths.get("logs_dir") or "").strip()
    logs_dir  = _resolve_path(logs_raw, base_dir) if logs_raw else base_dir / "logs"

    sprints_raw  = (paths.get("sprints_dir") or "").strip()
    sprints_dir  = _resolve_path(sprints_raw, base_dir) if sprints_raw else base_dir / "sprints"

    alerts_raw  = (paths.get("alerts_dir") or "").strip()
    alerts_dir  = _resolve_path(alerts_raw, base_dir) if alerts_raw else base_dir / "alerts"

    # ── dashboard section ─────────────────────────────────────────────────────
    dashboard = data.get("dashboard") or {}
    api_url   = (dashboard.get("api_url") or DASHBOARD_API_URL).strip()

    # ── agents section ────────────────────────────────────────────────────────
    agents = data.get("agents") or {}
    coder_prompt   = agents.get("coder_prompt_template") or None
    tester_prompt  = agents.get("tester_prompt_template") or None

    # ── app section (issue #62: per-project port detection) ───────────────────
    app_section = data.get("app") or {}
    app_default_port: Optional[int] = None
    app_port_strategy: str = "prefer_default"
    if app_section:
        raw_port = app_section.get("default_port")
        if raw_port is not None:
            try:
                app_default_port = int(raw_port)
            except (TypeError, ValueError):
                sys.exit(f"Config error: app.default_port must be an integer, got {raw_port!r}")
        raw_strategy = (app_section.get("port_strategy") or "prefer_default").strip()
        if raw_strategy not in ("prefer_default", "always_random"):
            sys.exit(
                f"Config error: app.port_strategy must be 'prefer_default' or 'always_random', "
                f"got {raw_strategy!r}"
            )
        app_port_strategy = raw_strategy

    # ── documentor section (issue #103) ──────────────────────────────────────
    documentor_enabled: bool = bool(data.get("documentor_enabled", False))

    # ── reviewer section (issue #159) ────────────────────────────────────────
    reviewer_prompt = agents.get("reviewer_prompt_template") or None

    # ── documenter section (issue #165) ──────────────────────────────────────
    documenter_prompt = agents.get("documenter_prompt_template") or None

    # ── agent_config section (issue #700) ────────────────────────────────────
    agent_cfg = data.get("agent_config") or {}
    _default_model: Optional[str] = (agent_cfg.get("default_model") or None) if isinstance(agent_cfg, dict) else None

    def _resolve_model(key: str, hardcoded: str) -> str:
        """Return per-agent override → default_model → hardcoded, in that order."""
        if isinstance(agent_cfg, dict) and key in agent_cfg:
            return str(agent_cfg[key])
        if _default_model:
            return _default_model
        return hardcoded

    coder_model      = _resolve_model("coder_model",      "claude-sonnet-4-6")
    tester_model     = _resolve_model("tester_model",     "claude-sonnet-4-6")
    reviewer_model   = _resolve_model("reviewer_model",   "claude-haiku-4-5")
    estimator_model  = _resolve_model("estimator_model",  "claude-sonnet-4-6")
    documentor_model = _resolve_model("documentor_model", "claude-sonnet-4-6")

    return SprintConfig(
        repo_name             = repo_name,
        worktree_coder        = worktree_coder,
        worktree_tester       = worktree_tester,
        tester_app_subdir     = tester_app_subdir,
        scripts_dir           = scripts_dir,
        logs_dir              = logs_dir,
        sprints_dir           = sprints_dir,
        alerts_dir            = alerts_dir,
        api_url               = api_url,
        coder_prompt_template = coder_prompt,
        tester_prompt_template= tester_prompt,
        app_default_port         = app_default_port,
        app_port_strategy        = app_port_strategy,
        documentor_enabled          = documentor_enabled,
        reviewer_prompt_template    = reviewer_prompt,
        documenter_prompt_template  = documenter_prompt,
        coder_model                 = coder_model,
        tester_model                = tester_model,
        reviewer_model              = reviewer_model,
        estimator_model             = estimator_model,
        documentor_model            = documentor_model,
    )


def discover_config(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Walk up from start_dir looking for .commander/sprint.yaml.

    Returns the Path if found, None otherwise.
    """
    if start_dir is None:
        start_dir = Path.cwd()
    current = start_dir.resolve()
    while True:
        candidate = current / ".commander" / "sprint.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:  # reached filesystem root
            break
        current = parent
    return None


def _default_config() -> "SprintConfig":
    """Build a SprintConfig from env-vars + hardcoded defaults (backward compat)."""
    return SprintConfig(
        repo_name          = None,  # will use github_client.repo()
        worktree_coder     = Path.home() / "commander" / "work-coder",
        worktree_tester    = WORKTESTER_ROOT,
        tester_app_subdir  = "apps/dashboard",
        scripts_dir        = SCRIPTS_DIR,
        logs_dir           = DASHBOARD_DIR / "logs",
        sprints_dir        = SPRINTS_DIR,
        alerts_dir         = ALERTS_DIR,
        api_url            = DASHBOARD_API_URL,
        documentor_enabled = False,
    )


# ── per-project PID lock ──────────────────────────────────────────────────────

def _pid_file_path(sprint_label: str, cfg: Optional["SprintConfig"] = None) -> Path:
    """Return the per-(project, sprint_label) PID file path."""
    if cfg is not None:
        sprints_dir = cfg.sprints_dir
    else:
        sprints_dir = REPO_ROOT / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    return sprints_dir / f"{sprint_label}-pid"


def _acquire_pid_lock(sprint_label: str, project: str,
                      cfg: Optional["SprintConfig"] = None) -> Path:
    """Write a PID file scoped to (project, sprint_label).

    Handles three cases:
    1. No file exists (CLI dispatch): creates the file fresh.
    2. File already exists with *our own* PID (server-dispatch two-phase claim,
       issue #155): file is already correct — nothing to do.
    3. File exists with a different PID:
       a. Process alive → another instance is running; exit with an error.
       b. Process dead → stale lock; log, clean up, then write fresh.

    Returns the path so the caller can release it on exit.
    """
    pid_path = _pid_file_path(sprint_label, cfg)
    my_pid   = os.getpid()

    if pid_path.exists():
        stale_pid: Optional[int] = None
        try:
            stale_pid = int(pid_path.read_text().strip())
        except (ValueError, OSError):
            pass

        # Case 2: server already wrote our PID via two-phase claim — nothing to do.
        if stale_pid == my_pid:
            return pid_path

        alive = False
        if stale_pid is not None:
            try:
                os.kill(stale_pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True  # process exists but is owned by another user

        if alive:
            sys.exit(
                f"Sprint {sprint_label} is already running on {project} (PID {stale_pid})"
            )
        else:
            print(
                f"  [pid-lock] Stale lock from PID {stale_pid} cleaned up",
                file=sys.stderr,
            )
            try:
                pid_path.unlink()
            except OSError:
                pass

    pid_path.write_text(str(my_pid))
    return pid_path


def _release_pid_lock(pid_path: Path) -> None:
    """Remove the PID file; idempotent."""
    try:
        pid_path.unlink()
    except OSError:
        pass


# ── Plan.json state helpers (issue #507) ─────────────────────────────────────

def _plan_json_path(label: str, cfg: Optional["SprintConfig"] = None) -> Path:
    """Return path to {label}-plan.json in the sprints directory."""
    sprints_dir = cfg.sprints_dir if cfg is not None else REPO_ROOT / ".commander" / "sprints"
    return sprints_dir / f"{label}-plan.json"


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
        print(
            f"  [label-guard] Blocked removal of sprint label(s): {blocked_removes}",
            file=sys.stderr,
        )

    if sprint_label is not None:
        safe_add = [lbl for lbl in add if lbl in RUN_MUTABLE_LABELS]
        deferred = [lbl for lbl in add if lbl not in RUN_MUTABLE_LABELS]
        if deferred:
            print(
                f"  [label-guard] Deferred non-mutable label addition(s) until post-run: {deferred}",
                file=sys.stderr,
            )
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
        print(f"  [run-mutable-guard] {msg}", file=sys.stderr)
        structured_log.warn("run_mutable_guard", msg, label=lbl, op=op)
    if violations:
        raise ValueError(f"Label mutation blocked: {violations!r} outside RUN_MUTABLE_LABELS")

# ── end sprint-label protection ───────────────────────────────────────────────

# Rate-limit retry constants
_RATE_LIMIT_MAX_RETRIES     = 3
_RATE_LIMIT_BACKOFF_DELAYS  = [30, 60, 120]   # seconds per attempt
_RATE_LIMIT_SIGNALS         = ["429", "rate limit", "too many requests",
                                "subscription rate limit", "rate_limit"]


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_BANGKOK_TZ = timezone(timedelta(hours=7))


def _bangkok_now() -> str:
    """Return current Bangkok time (UTC+7) as YYYY-MM-DDTHH:MM:SS+07:00."""
    return datetime.now(_BANGKOK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")


def _to_bangkok(utc_str: str) -> str:
    """Convert a UTC timestamp string ending in Z to Bangkok local time."""
    try:
        dt = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(_BANGKOK_TZ).strftime("%Y-%m-%dT%H:%M:%S+07:00")
    except (ValueError, TypeError):
        return _bangkok_now()


def _sprint_number(label: str) -> Optional[int]:
    m = re.search(r"(\d+)", label)
    return int(m.group(1)) if m else None


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


def _state_path(
    sprint_number: Optional[int],
    sprint_label: str,
    cfg: Optional["SprintConfig"] = None,
) -> Path:
    sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    n = sprint_number if sprint_number is not None else sprint_label
    return sprints_dir / f"sprint-{n}-state.json"


def _summary_path(
    sprint_number: Optional[int],
    sprint_label: str,
    cfg: Optional["SprintConfig"] = None,
) -> Path:
    sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    n   = sprint_number if sprint_number is not None else sprint_label
    day = datetime.now().strftime("%Y-%m-%d")
    return sprints_dir / f"sprint-{n}-summary-{day}.md"


# ── PID file management (AC-2) ───────────────────────────────────────────────

_active_pid_path: Optional[Path] = None


def _remove_pid_file() -> None:
    """Remove the sprint PID file if it exists (called by atexit + signal handlers)."""
    global _active_pid_path
    if _active_pid_path and _active_pid_path.exists():
        try:
            _active_pid_path.unlink()
        except OSError:
            pass


def _setup_pid_file(sprint_num: Optional[int]) -> None:
    """Write PID to dashboard/sprints/sprint-N.pid and register cleanup handlers."""
    global _active_pid_path
    if sprint_num is None:
        return
    sprints_dir = SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    pid_path = sprints_dir / f"sprint-{sprint_num}.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    _active_pid_path = pid_path

    atexit.register(_remove_pid_file)

    def _sig_handler(signum: int, frame: object) -> None:
        _remove_pid_file()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)


# ── Pause check (AC-3) ───────────────────────────────────────────────────────

def _wait_if_paused(
    sprint_num: Optional[int],
    state: "SprintState",
    api_url: Optional[str] = None,
) -> None:
    """Block until the sprint-N.pause file is removed, then broadcast resume."""
    if sprint_num is None:
        return
    pause_file = SPRINTS_DIR / f"sprint-{sprint_num}.pause"
    if not pause_file.exists():
        return
    print("Paused — waiting for resume…", flush=True)
    while pause_file.exists():
        time.sleep(5)
    print("Resuming sprint…", flush=True)
    _post_sprint_status(state, api_url=api_url)


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


# Logic failures signal bad code/spec and warrant needs-rework label.
# Infrastructure failures (CRASH, HANG, RETRY_EXHAUSTED, TESTER_REJECTED) are transient and do not.
# TESTER_REJECTED means tests passed (exit 0) but merge was not detected — a process/infra issue,
# not a code quality problem, so it must not apply needs-rework.
_LOGIC_FAILURE_CATEGORIES: frozenset[str] = frozenset({
    FailureCategory.CODER_NO_WORK,
    FailureCategory.MERGE_CONFLICT,
    FailureCategory.LINT_FAIL,
    FailureCategory.PYTEST_FAIL,
})


# ── alert modes ───────────────────────────────────────────────────────────────

class AlertMode:
    DASHBOARD_BANNER = "dashboard-banner"
    EMAIL            = "email"
    DISCORD          = "discord"
    FILE             = "file"
    NTFY             = "ntfy"
    NONE             = "none"

    ALL_MODES = {DASHBOARD_BANNER, EMAIL, DISCORD, FILE, NTFY, NONE}


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class IssueState:
    number:      int
    title:       str
    status:      str              = "pending"   # pending | done | skipped
    skip_reason: Optional[str]   = None
    category:    Optional[str]   = None         # FailureCategory value
    tokens_in:   int             = 0
    tokens_out:  int             = 0
    # Per-ticket agent lifecycle fields (issue #131)
    agent_status:         Optional[str] = None  # queued | coder_dispatched | coder_running | coder_done | tester_dispatched | tester_running | tester_done | completed | failed
    status_changed_at:    Optional[str] = None  # ISO 8601 UTC
    coder_started_at:     Optional[str] = None
    coder_finished_at:    Optional[str] = None
    tester_started_at:    Optional[str] = None
    tester_finished_at:   Optional[str] = None
    failure_reason:       Optional[str] = None
    dispatch_level:       int           = 0   # 1-based execution level; 0 = unset
    tester_attempt_count: int           = 0   # incremented on each tester dispatch (issue #718)

    def to_dict(self) -> dict:
        return {
            "number":             self.number,
            "title":              self.title,
            "status":             self.status,
            "skip_reason":        self.skip_reason,
            "category":           self.category,
            "tokens_in":          self.tokens_in,
            "tokens_out":         self.tokens_out,
            "agent_status":       self.agent_status,
            "status_changed_at":  self.status_changed_at,
            "coder_started_at":   self.coder_started_at,
            "coder_finished_at":  self.coder_finished_at,
            "tester_started_at":  self.tester_started_at,
            "tester_finished_at": self.tester_finished_at,
            "failure_reason":     self.failure_reason,
            "dispatch_level":     self.dispatch_level,
            "tester_attempt_count": self.tester_attempt_count,
        }

    @staticmethod
    def from_dict(d: dict) -> "IssueState":
        iss = IssueState(
            number             = d["number"],
            title              = d["title"],
            status             = d.get("status", "pending"),
            skip_reason        = d.get("skip_reason"),
            category           = d.get("category"),
            tokens_in          = d.get("tokens_in", 0),
            tokens_out         = d.get("tokens_out", 0),
            agent_status       = d.get("agent_status"),
            status_changed_at  = d.get("status_changed_at"),
            coder_started_at   = d.get("coder_started_at"),
            coder_finished_at  = d.get("coder_finished_at"),
            tester_started_at  = d.get("tester_started_at"),
            tester_finished_at = d.get("tester_finished_at"),
            failure_reason     = d.get("failure_reason"),
        )
        iss.dispatch_level = d.get("dispatch_level", 0)
        iss.tester_attempt_count = d.get("tester_attempt_count", 0)
        return iss

    def set_agent_status(self, status: str) -> None:
        """Set agent_status and record ISO 8601 UTC timestamp on status_changed_at."""
        self.agent_status      = status
        self.status_changed_at = _utcnow()


@dataclass
class SprintState:
    sprint_label:       str
    sprint_number:      Optional[int]
    project:            str              = ""
    issues:             list[IssueState]  = field(default_factory=list)
    start_timestamp:    str              = ""
    total_tokens_in:    int              = 0
    total_tokens_out:   int              = 0
    wall_clock_secs:    float            = 0.0
    token_budget:       int              = 0
    rate_limit_events:  list[dict]       = field(default_factory=list)
    # Reviewer fields (issue #159)
    reviewer_status:      Optional[str]  = None   # "skipped" | "succeeded" | "failed"
    reviewer_comment_url: Optional[str]  = None
    reviewer_findings:    Optional[dict] = None   # {blockers, suggestions, nits, follow_up_tickets}
    # Documenter fields (issue #165)
    documenter_status:       Optional[str]       = None   # "skipped" | "succeeded" | "failed"
    documenter_files_touched: list               = field(default_factory=list)
    documenter_commit_sha:   Optional[str]       = None
    # Estimator fields (issue #166)
    estimator_status:        Optional[str]       = None   # "succeeded" | "failed" | "skipped"
    estimator_total_minutes: Optional[int]       = None
    estimates:               dict                = field(default_factory=dict)   # keyed by issue number (int)

    def to_dict(self) -> dict:
        return {
            "project":              self.project,
            "sprint_label":         self.sprint_label,
            "sprint_number":        self.sprint_number,
            "issues":               [i.to_dict() for i in self.issues],
            "start_timestamp":      self.start_timestamp,
            "total_tokens_in":      self.total_tokens_in,
            "total_tokens_out":     self.total_tokens_out,
            "wall_clock_secs":      self.wall_clock_secs,
            "token_budget":         self.token_budget,
            "rate_limit_events":    self.rate_limit_events,
            "reviewer_status":      self.reviewer_status,
            "reviewer_comment_url": self.reviewer_comment_url,
            "reviewer_findings":    self.reviewer_findings,
            "documenter_status":         self.documenter_status,
            "documenter_files_touched":  self.documenter_files_touched,
            "documenter_commit_sha":     self.documenter_commit_sha,
            "estimator_status":          self.estimator_status,
            "estimator_total_minutes":   self.estimator_total_minutes,
            "estimates":                 self.estimates,
        }

    @staticmethod
    def from_dict(d: dict) -> "SprintState":
        s = SprintState(
            sprint_label     = d["sprint_label"],
            sprint_number    = d.get("sprint_number"),
            project          = d.get("project", ""),
            start_timestamp  = d.get("start_timestamp", ""),
            total_tokens_in  = d.get("total_tokens_in", 0),
            total_tokens_out = d.get("total_tokens_out", 0),
            wall_clock_secs  = d.get("wall_clock_secs", 0.0),
            token_budget     = d.get("token_budget", 0),
        )
        s.issues              = [IssueState.from_dict(i) for i in d.get("issues", [])]
        s.rate_limit_events   = d.get("rate_limit_events", [])
        s.reviewer_status     = d.get("reviewer_status")
        s.reviewer_comment_url = d.get("reviewer_comment_url")
        s.reviewer_findings   = d.get("reviewer_findings")
        s.documenter_status        = d.get("documenter_status")
        s.documenter_files_touched = d.get("documenter_files_touched", [])
        s.documenter_commit_sha    = d.get("documenter_commit_sha")
        s.estimator_status         = d.get("estimator_status")
        s.estimator_total_minutes  = d.get("estimator_total_minutes")
        s.estimates                = d.get("estimates", {})
        return s

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(self.to_dict(), indent=2))
        except OSError as _e:
            structured_log.error("sprint_state_write_error", f"could not write state JSON: {_e}", path=str(path), exc=str(_e))


@dataclass
class GateResult:
    gate:    str
    passed:  bool
    skipped: bool = False
    output:  str  = ""

    @property
    def symbol(self) -> str:
        if self.skipped:
            return "skipped"
        return "PASS" if self.passed else "FAIL"


@dataclass
class SprintSummary:
    processed: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ── subprocess helpers ────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)
    return r.stdout.strip()


def _try(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


def _run_timed(*cmd, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout, r.stderr


# ── port detection (issue #62) ───────────────────────────────────────────────

def _detect_port(cfg: "SprintConfig") -> Optional[int]:
    """Call find_port.py and return the chosen port, or None if no app section.

    AC-5: Called before coder is dispatched when app_default_port is set.
    """
    if cfg.app_default_port is None:
        return None  # non-server project: skip port detection

    find_port_script = cfg.scripts_dir / "find_port.py"
    cmd = [
        sys.executable, str(find_port_script),
        "--prefer", str(cfg.app_default_port),
        "--strategy", cfg.app_port_strategy,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        port_str = result.stdout.strip()
        chosen_port = int(port_str)
        print(f"  [port] chosen port: {chosen_port} "
              f"(preferred: {cfg.app_default_port}, strategy: {cfg.app_port_strategy})")
        return chosen_port
    except (subprocess.CalledProcessError, ValueError) as e:
        structured_log.warn("port_detection_failed", f"find_port.py failed: {e}", exc=str(e))
        return None


def _write_runtime_port(worktree_coder: Path, port: int) -> None:
    """Write chosen port to <coder_worktree>/.commander/runtime/port.

    AC-6: Creates parent dirs as needed.
    """
    runtime_dir = worktree_coder / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    port_file = runtime_dir / "port"
    port_file.write_text(str(port), encoding="utf-8")
    print(f"  [port] wrote {port} to {port_file}")


def _changed_py_files(base_branch: str, cwd: Path) -> list[str]:
    """Return .py files added/modified in HEAD relative to base_branch.

    Uses git diff <base_branch> --name-only --diff-filter=ACM to find files
    that were Added, Copied, or Modified relative to base_branch.
    Returns a list of relative paths (e.g. ['server.py', 'tests/test_foo.py']).
    """
    rc, out, _ = _run_timed(
        "git", "diff", base_branch, "--name-only", "--diff-filter=ACM",
        cwd=cwd,
    )
    if rc != 0:
        return []
    return [f for f in out.splitlines() if f.endswith(".py")]


_JS_TS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def _changed_js_ts_files(base_branch: str, cwd: Path) -> list[str]:
    """Return JS/TS files added/modified in HEAD relative to base_branch."""
    rc, out, _ = _run_timed(
        "git", "diff", base_branch, "--name-only", "--diff-filter=ACM",
        cwd=cwd,
    )
    if rc != 0:
        return []
    return [f for f in out.splitlines() if any(f.endswith(ext) for ext in _JS_TS_EXTENSIONS)]


# ── dashboard integration ─────────────────────────────────────────────────────

def _post_agent_event(
    tool_name: str,
    agent_id: str = "sprint-manager",
    api_url: Optional[str] = None,
) -> None:
    """POST to /api/agent-event to update the dashboard agent card."""
    base = api_url or DASHBOARD_API_URL
    try:
        payload = json.dumps({
            "agent_id":  agent_id,
            "tool_name": tool_name,
            "timestamp": time.time(),
        }).encode()
        req = urllib.request.Request(
            f"{base}/api/agent-event",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        # Fail silently — dashboard may not be running
        pass


def _post_sprint_status(
    state: "SprintState",
    api_url: Optional[str] = None,
    project: Optional[str] = None,
) -> None:
    """POST the current sprint state to /api/sprint-status."""
    base = api_url or DASHBOARD_API_URL
    try:
        data = state.to_dict()
        if project:
            data["project"] = project
        payload = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{base}/api/sprint-status",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


# ── alert dispatch ────────────────────────────────────────────────────────────

def dispatch_alerts(
    alert_modes: list[str],
    title: str,
    body: str,
    issue_num: Optional[int] = None,
    category: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    repo: Optional[str] = None,
) -> None:
    """Dispatch an alert through all configured channels."""
    api_url    = cfg.api_url    if cfg is not None else None
    alerts_dir = cfg.alerts_dir if cfg is not None else None
    for mode in alert_modes:
        if mode == AlertMode.NONE:
            continue
        try:
            if mode == AlertMode.DASHBOARD_BANNER:
                _alert_dashboard_banner(title, body, issue_num, category, api_url=api_url, repo=repo)
            elif mode == AlertMode.EMAIL:
                _alert_email(title, body)
            elif mode == AlertMode.DISCORD:
                _alert_discord(title, body)
            elif mode == AlertMode.FILE:
                _alert_file(title, body, alerts_dir=alerts_dir)
            elif mode == AlertMode.NTFY:
                _alert_ntfy(title, body, category)
        except Exception as e:
            structured_log.error("alert_dispatch_error", f"[alert:{mode}] error: {e}", mode=mode, exc=str(e))


def _alert_dashboard_banner(
    title: str,
    body: str,
    issue_num: Optional[int],
    category: Optional[str],
    api_url: Optional[str] = None,
    repo: Optional[str] = None,
) -> None:
    base = api_url or DASHBOARD_API_URL
    payload = json.dumps({
        "title":      title,
        "body":       body,
        "issue_num":  issue_num,
        "category":   category,
        "repo":       repo,
        "timestamp":  _utcnow(),
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/alerts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=3)


def _alert_email(title: str, body: str) -> None:
    host  = os.environ.get("SMTP_HOST", "")
    port  = os.environ.get("SMTP_PORT", "")
    user  = os.environ.get("SMTP_USER", "")
    pw    = os.environ.get("SMTP_PASS", "")
    to    = os.environ.get("ALERT_EMAIL_TO", "")
    if not all([host, port, user, pw, to]):
        return  # silently skip if any var missing
    msg = email.mime.text.MIMEText(body, "plain")
    msg["Subject"] = f"[Sprint Manager] {title}"
    msg["From"]    = user
    msg["To"]      = to
    with smtplib.SMTP(host, int(port)) as srv:
        srv.starttls()
        srv.login(user, pw)
        srv.sendmail(user, [to], msg.as_string())


def _alert_discord(title: str, body: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return  # silently skip
    content = f"**{title}**\n{body}"
    payload = json.dumps({"content": content[:2000]}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def _alert_ntfy(title: str, body: str, category: Optional[str] = None) -> None:
    topic_url = os.environ.get("NTFY_TOPIC_URL", "")
    if not topic_url:
        return
    priority = "4" if category in ("failure", "needs-rework") else "3"
    payload  = body.encode()
    req      = urllib.request.Request(
        topic_url,
        data=payload,
        headers={
            "Title":    title,
            "Priority": priority,
            "Tags":     category or "sprint",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def _alert_file(title: str, body: str, alerts_dir: Optional[Path] = None) -> None:
    d = alerts_dir if alerts_dir is not None else ALERTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = d / f"{today}.log"
    ts       = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry    = f"[{ts}] {title}\n{body}\n{'─' * 60}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)


# ── hang detection ────────────────────────────────────────────────────────────

@dataclass
class HangDetector:
    issue_num: int
    log_path:  Optional[Path]
    proc:      subprocess.Popen
    max_total_secs: Optional[int] = None  # hard wall-clock cap (None = disabled)

    _start_time:   float = field(default_factory=time.monotonic, init=False)
    _last_size:    int   = field(default=0, init=False)
    _last_change:  float = field(default_factory=time.monotonic, init=False)
    _warned:       bool  = field(default=False, init=False)
    _stop_event:   threading.Event = field(default_factory=threading.Event, init=False)
    _thread:       Optional[threading.Thread] = field(default=None, init=False)
    _killed:       bool  = field(default=False, init=False)

    def start(self) -> None:
        self._start_time  = time.monotonic()
        self._last_change = time.monotonic()
        self._last_size   = self._log_size()
        self._thread      = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    @property
    def killed(self) -> bool:
        return self._killed

    def _log_size(self) -> int:
        if self.log_path and self.log_path.exists():
            return self.log_path.stat().st_size
        return 0

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=HANG_CHECK_SECS)
            if self._stop_event.is_set():
                break

            # Hard wall-clock timeout (used by documenter for ≤5 min budget)
            if self.max_total_secs is not None:
                total_elapsed = time.monotonic() - self._start_time
                if total_elapsed >= self.max_total_secs:
                    structured_log.error("subprocess_timeout", f"subprocess exceeded {self.max_total_secs}s wall-clock limit — KILLING", issue_num=self.issue_num, timeout_secs=self.max_total_secs)
                    try:
                        self.proc.kill()
                    except ProcessLookupError:
                        pass
                    self._killed = True
                    self._stop_event.set()
                    return

            size = self._log_size()
            if size != self._last_size:
                self._last_size   = size
                self._last_change = time.monotonic()
                self._warned      = False
                continue

            idle = time.monotonic() - self._last_change
            if idle >= HANG_KILL_SECS:
                structured_log.error("subprocess_killed", f"no log activity for {idle/60:.0f} min — KILLING subprocess", issue_num=self.issue_num, idle_minutes=round(idle / 60))
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
                self._killed = True
                self._stop_event.set()
            elif idle >= HANG_WARN_SECS and not self._warned:
                structured_log.warn("hang_detected", f"no log activity for {idle/60:.0f} min", issue_num=self.issue_num, idle_minutes=round(idle / 60))
                self._warned = True


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _r(repo_name: Optional[str]) -> str:
    return repo_name or github_client.repo()


def _get_issue_labels(issue_num: int, repo_name: Optional[str] = None) -> set[str]:
    """Re-fetch current labels for an issue via gh CLI."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(issue_num), "--repo", r, "--json", "labels"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        return {lbl["name"] for lbl in data.get("labels", [])}
    except Exception:
        return set()


def _transition_safe(
    issue_num: int,
    target_state: "_TicketState",
    actor: str,
    repo_name: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Call state_machine.transition() best-effort; log warnings on failure."""
    if not _STATE_MACHINE_AVAILABLE or _sm_transition is None:
        structured_log.warn(
            "state_machine_unavailable",
            f"state_machine not available; skipping {target_state} transition for #{issue_num}",
            issue_num=issue_num,
        )
        return
    try:
        _sm_transition(issue_num, target_state, actor=actor, repo=repo_name, note=note)
        structured_log.info(
            "ticket_transition",
            f"transition #{issue_num} → {target_state.value} actor={actor!r}",
            issue_num=issue_num,
            target_state=target_state.value,
            actor=actor,
        )
    except _TransitionError as e:
        structured_log.warn(
            "label_apply_failed",
            f"transition {target_state.value} failed for #{issue_num}: {e}",
            issue_num=issue_num,
            target_state=target_state.value,
            exc=str(e),
        )
    except Exception as e:
        structured_log.warn(
            "label_apply_failed",
            f"unexpected error in transition {target_state.value} for #{issue_num}: {e}",
            issue_num=issue_num,
            target_state=target_state.value,
            exc=str(e),
        )


def _add_blocked_label(
    issue_num: int,
    reason: str,
    repo_name: Optional[str] = None,
    sprint_label: Optional[str] = None,
) -> None:
    safe_add, _ = _guard_sprint_labels(["blocked"], [], sprint_label=sprint_label)
    if not safe_add:
        # "blocked" is outside RUN_MUTABLE_LABELS — suppressed during active run
        print(
            f"  [label-guard] 'blocked' label suppressed for #{issue_num} during active sprint run",
            file=sys.stderr,
        )
        try:
            github_client.add_comment(
                issue_num,
                f"Issue hung (HANG): {reason}. 'blocked' label deferred — applied post-run.",
                repo_name=repo_name,
            )
        except Exception as e:
            structured_log.warn("hang_comment_failed", f"failed to post hang comment: {e}", exc=str(e))
        return
    try:
        github_client.update_labels(issue_num, add=safe_add, repo_name=repo_name)
        github_client.add_comment(
            issue_num,
            f"Issue blocked by sprint manager (HANG): {reason}",
            repo_name=repo_name,
        )
    except Exception as e:
        structured_log.warn("blocked_label_failed", f"failed to update GitHub blocked label: {e}", exc=str(e))


def _find_feature_branch(issue_num: int) -> Optional[str]:
    """Return feature/<N>-* branch name, checking local then remote."""
    ok, out, _ = _try("git", "branch", "--list", f"feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("* ")
    ok, out, _ = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")
    return None


def _is_branch_merged_into(branch: str, target: str) -> bool:
    """Return True if branch has been merged into target (local or remote).

    Checks both local refs and origin/ remote refs for the branch.
    Uses ``git branch --merged`` which lists branches fully reachable from
    the given commit — i.e. whose tip is an ancestor of <target>.
    """
    # Prefer the remote ref for the target so we don't need a local checkout
    target_ref = f"origin/{target}"
    ok, _, _ = _try("git", "rev-parse", "--verify", target_ref)
    if not ok:
        target_ref = target

    # Try local branch ref first
    ok, out, _ = _try("git", "branch", "--merged", target_ref, "--list", branch)
    if ok and out.strip():
        return True

    # Fall back to checking the remote tracking branch
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


# ── quality gates ─────────────────────────────────────────────────────────────

_GATE_FAILURE_CLASS_MAP: dict[str, str] = {
    "pytest":        "test",
    "lint":          "lint",
    "ruff":          "lint",
    "typecheck":     "typecheck",
    "design":        "design",
    "merge-preview": "merge",
}


def _revert_to_sit(issue_num: int, gate_name: str, output: str,
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
            effective_root = repo_root or REPO_ROOT
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
                          repo_name: Optional[str] = None) -> None:
    gate_lines = "\n".join(
        f"- **{r.gate}**: {r.symbol}" for r in results
    )
    comment = (
        f"Quality gates passed. Auto-merged to develop.\n\n"
        f"Gates:\n{gate_lines}\n\nAwaiting human UAT approval."
    )
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        structured_log.warn("success_comment_failed", f"failed to post success comment: {e}", issue_num=issue_num, exc=str(e))


def _gate_pytest(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> GateResult:
    """Gate 1 — run pytest -x inside the tester worktree dashboard.

    gate_scope='changed' (default): only run test files changed relative to
    base_branch. gate_scope='full': run full pytest suite (legacy behaviour).
    """
    if skip:
        print("  [gate:pytest] skipped")
        return GateResult(gate="pytest", passed=True, skipped=True)

    _post_agent_event("gate:pytest")

    # Detect pytest binary
    ok, pytest_path, _ = _try("which", "pytest")
    if not ok:
        # Try inside dashboard venv
        venv_pytest = worktester_dashboard / ".." / "venv" / "bin" / "pytest"
        if venv_pytest.exists():
            pytest_bin = str(venv_pytest.resolve())
        else:
            output = "pytest binary not found on PATH and no venv/bin/pytest found."
            structured_log.error("gate_failed", f"[gate:pytest] FAIL: {output}", gate="pytest", issue_num=issue_num)
            return GateResult(gate="pytest", passed=False, output=output)
    else:
        pytest_bin = pytest_path

    # Determine which test files to run based on gate_scope
    if gate_scope == "full":
        print("  [gate:pytest] running pytest -x (full scope) ...")
        rc, stdout, stderr = _run_timed(pytest_bin, "-x", cwd=worktester_dashboard)
    else:
        # changed scope: only run test files changed relative to base_branch
        changed = _changed_py_files(base_branch, cwd=worktester_dashboard)
        test_files = [f for f in changed if f.startswith("tests/")]
        if not test_files:
            print("  [gate:pytest] no test files changed — skipped")
            return GateResult(gate="pytest", passed=True, output="no test files changed")
        print(f"  [gate:pytest] checking {len(test_files)} file(s): {', '.join(test_files)}")
        rc, stdout, stderr = _run_timed(pytest_bin, "-x", *test_files, cwd=worktester_dashboard)

    combined = stdout + stderr
    if rc == 0:
        print("  [gate:pytest] PASS")
        return GateResult(gate="pytest", passed=True, output=combined)
    else:
        structured_log.error("gate_failed", f"[gate:pytest] FAIL (exit {rc})", gate="pytest", issue_num=issue_num, exit_code=rc)
        _revert_to_sit(issue_num, "pytest", combined, repo_name=repo_name)
        return GateResult(gate="pytest", passed=False, output=combined)


def _gate_lint(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
    gate_frontend_lint: bool = True,
) -> GateResult:
    """Gate: run ruff (Python) and eslint/biome + prettier (JS/TS).

    gate_scope='changed' (default): only lint files changed relative to
    base_branch. gate_scope='full': run against whole codebase (legacy behaviour).
    Skips gracefully when tools are not installed or no relevant files changed.
    gate_frontend_lint: when False, skips the frontend (JS/TS) lint portion.
    """
    if skip:
        print("  [gate:lint] skipped")
        return GateResult(gate="lint", passed=True, skipped=True)

    _post_agent_event("gate:lint")
    combined = ""
    any_ran = False

    # ── Python lint via ruff ───────────────────────────────────────────────────
    ok_ruff, ruff_path, _ = _try("which", "ruff")
    if not ok_ruff:
        venv_ruff = worktester_dashboard / ".." / "venv" / "bin" / "ruff"
        ruff_bin = str(venv_ruff.resolve()) if venv_ruff.exists() else None
    else:
        ruff_bin = ruff_path

    if ruff_bin:
        if gate_scope == "full":
            print("  [gate:lint] running ruff check . (full scope) ...")
            rc, stdout, stderr = _run_timed(ruff_bin, "check", ".", cwd=worktester_dashboard)
            combined += stdout + stderr
            any_ran = True
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:lint] ruff FAIL (exit {rc})",
                                     gate="lint", issue_num=issue_num, exit_code=rc)
                _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
                return GateResult(gate="lint", passed=False, output=combined)
            print("  [gate:lint] ruff PASS")
        else:
            py_files = _changed_py_files(base_branch, cwd=worktester_dashboard)
            if py_files:
                print(f"  [gate:lint] ruff checking {len(py_files)} file(s): {', '.join(py_files)}")
                rc, stdout, stderr = _run_timed(ruff_bin, "check", *py_files,
                                                cwd=worktester_dashboard)
                combined += stdout + stderr
                any_ran = True
                if rc != 0:
                    structured_log.error("gate_failed", f"[gate:lint] ruff FAIL (exit {rc})",
                                         gate="lint", issue_num=issue_num, exit_code=rc)
                    _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
                    return GateResult(gate="lint", passed=False, output=combined)
                print("  [gate:lint] ruff PASS")
            else:
                print("  [gate:lint] no Python files changed — ruff skipped")
    else:
        structured_log.warn("lint_tool_missing", "[gate:lint] ruff not found; skipping Python lint",
                            issue_num=issue_num)

    # ── Frontend lint via eslint/biome + prettier ──────────────────────────────
    if not gate_frontend_lint:
        print("  [gate:lint] frontend lint disabled (COMMANDER_GATE_FRONTEND_LINT=0)")
    else:
        if gate_scope == "full":
            js_ts_files_for_fe: list[str] = ["."]
        else:
            js_ts_files_for_fe = _changed_js_ts_files(base_branch, cwd=worktester_dashboard)

        if js_ts_files_for_fe:
            fe_passed, fe_output = _run_frontend_lint(
                issue_num, worktester_dashboard, js_ts_files_for_fe, gate_scope
            )
            combined += fe_output
            if fe_output.strip():
                any_ran = True
            if not fe_passed:
                _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
                return GateResult(gate="lint", passed=False, output=combined)
        else:
            print("  [gate:lint] no JS/TS files changed — frontend lint skipped")

    if not any_ran:
        print("  [gate:lint] no lintable files changed — skipped")
        return GateResult(gate="lint", passed=True, output="no lintable files changed")

    print("  [gate:lint] PASS")
    return GateResult(gate="lint", passed=True, output=combined)


def _gate_merge_preview(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    skip: bool,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
) -> GateResult:
    """Gate 3 -- simulate merge in worktester root without committing."""
    if skip:
        print("  [gate:merge-preview] skipped")
        return GateResult(gate="merge-preview", passed=True, skipped=True)

    _post_agent_event("gate:merge-preview")
    print(f"  [gate:merge-preview] simulating merge of {feature_branch} into {target_branch} ...")

    merge_ok = False
    combined = ""

    try:
        # Fetch + update target branch
        _run("git", "fetch", "origin", cwd=worktester_root, check=False)

        # Ensure target branch exists locally
        ok, _, _ = _try("git", "show-ref", "--verify", "--quiet",
                         f"refs/heads/{target_branch}", cwd=worktester_root)
        if ok:
            _run("git", "checkout", target_branch, cwd=worktester_root, check=False)
            _run("git", "pull", "origin", target_branch, cwd=worktester_root, check=False)
        else:
            _run("git", "checkout", "--track", f"origin/{target_branch}",
                 cwd=worktester_root, check=False)

        # Attempt dry-run merge
        rc, stdout, stderr = _run_timed(
            "git", "merge", "--no-commit", "--no-ff", feature_branch,
            cwd=worktester_root,
        )
        combined = stdout + stderr
        merge_ok = (rc == 0)

        if merge_ok:
            print("  [gate:merge-preview] PASS -- no conflicts")
        else:
            structured_log.error("gate_failed", f"[gate:merge-preview] FAIL: conflicts detected merging into {target_branch}", gate="merge-preview", issue_num=issue_num, target_branch=target_branch)
    finally:
        # Always abort to leave working tree clean
        _run("git", "merge", "--abort", cwd=worktester_root, check=False)

    if not merge_ok:
        _revert_to_sit(issue_num, "merge-preview", combined, repo_name=repo_name)
        return GateResult(gate="merge-preview", passed=False, output=combined)

    return GateResult(gate="merge-preview", passed=True, output=combined)


def _gate_typecheck(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> GateResult:
    """Gate: run mypy (Python) and/or tsc --noEmit (TypeScript) on changed files.

    Skips gracefully when the tool is not installed or no relevant files changed.
    """
    if skip:
        print("  [gate:typecheck] skipped")
        return GateResult(gate="typecheck", passed=True, skipped=True)

    _post_agent_event("gate:typecheck")
    results_passed = True
    combined = ""

    # ── Python typecheck via mypy ──────────────────────────────────────────────
    if gate_scope == "full":
        py_files: list[str] = ["apps/dashboard"]
    else:
        py_files = _changed_py_files(base_branch, cwd=worktester_dashboard)

    if py_files:
        ok_mypy, mypy_path, _ = _try("which", "mypy")
        if not ok_mypy:
            venv_mypy = worktester_dashboard / ".." / "venv" / "bin" / "mypy"
            mypy_bin = str(venv_mypy.resolve()) if venv_mypy.exists() else None
        else:
            mypy_bin = mypy_path

        if mypy_bin:
            targets = ["."] if gate_scope == "full" else py_files
            print(f"  [gate:typecheck] running mypy on {len(targets)} target(s) ...")
            rc, stdout, stderr = _run_timed(mypy_bin, "--ignore-missing-imports", *targets,
                                            cwd=worktester_dashboard)
            combined += stdout + stderr
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:typecheck] mypy FAIL (exit {rc})",
                                     gate="typecheck", issue_num=issue_num, exit_code=rc)
                results_passed = False
            else:
                print("  [gate:typecheck] mypy PASS")
        else:
            structured_log.warn("typecheck_tool_missing",
                                "[gate:typecheck] mypy not found; skipping Python typecheck",
                                issue_num=issue_num)

    # ── TypeScript typecheck via tsc --noEmit ──────────────────────────────────
    if gate_scope == "full":
        ts_files: list[str] = []
        ok_ts, ts_out, _ = _try("find", ".", "-name", "tsconfig.json", "-maxdepth", "3",
                                 cwd=worktester_dashboard)
        if ok_ts and ts_out.strip():
            ts_files = ["_has_tsconfig_"]  # sentinel — just triggers tsc check
    else:
        ts_files = _changed_js_ts_files(base_branch, cwd=worktester_dashboard)

    if ts_files:
        ok_tsc, tsc_path, _ = _try("which", "tsc")
        if ok_tsc:
            print("  [gate:typecheck] running tsc --noEmit ...")
            rc, stdout, stderr = _run_timed(tsc_path, "--noEmit", cwd=worktester_dashboard)
            combined += stdout + stderr
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:typecheck] tsc FAIL (exit {rc})",
                                     gate="typecheck", issue_num=issue_num, exit_code=rc)
                results_passed = False
            else:
                print("  [gate:typecheck] tsc PASS")
        else:
            structured_log.warn("typecheck_tool_missing",
                                "[gate:typecheck] tsc not found; skipping TS typecheck",
                                issue_num=issue_num)

    if not py_files and not ts_files:
        print("  [gate:typecheck] no typed files changed — skipped")
        return GateResult(gate="typecheck", passed=True, output="no typed files changed")

    if not results_passed:
        _revert_to_sit(issue_num, "typecheck", combined, repo_name=repo_name)
        return GateResult(gate="typecheck", passed=False, output=combined)

    if not combined:
        print("  [gate:typecheck] no typecheck tools found — skipped")
        return GateResult(gate="typecheck", passed=True, skipped=True,
                          output="no typecheck tools found")

    return GateResult(gate="typecheck", passed=True, output=combined)


def _run_frontend_lint(
    issue_num: int,
    worktester_dashboard: Path,
    js_ts_files: list[str],
    gate_scope: str,
) -> tuple[bool, str]:
    """Run eslint/biome + prettier --check on JS/TS files.

    Returns (passed: bool, combined_output: str).
    Skips gracefully when tools are missing.
    """
    combined = ""
    passed = True

    # eslint or biome (prefer biome if configured)
    ok_biome, biome_path, _ = _try("which", "biome")
    ok_eslint, eslint_path, _ = _try("which", "eslint")
    ok_npx, npx_path, _ = _try("which", "npx")

    linter_bin: Optional[str] = None
    linter_args: list[str] = []
    if ok_biome:
        linter_bin = biome_path
        linter_args = ["check"] + (["--apply=false"] if gate_scope != "full" else ["--apply=false", "."])
    elif ok_eslint:
        linter_bin = eslint_path
        linter_args = ["--max-warnings=0"]
    elif ok_npx:
        # try biome via npx — skip if not found in project
        rc_biome, _, _ = _run_timed(
            npx_path, "--no", "biome", "--version", cwd=worktester_dashboard
        )
        if rc_biome == 0:
            linter_bin = npx_path
            linter_args = ["--no", "biome", "check", "--apply=false"]

    if linter_bin:
        targets = ["."] if gate_scope == "full" else js_ts_files
        print(f"  [gate:lint-fe] running frontend linter on {len(targets)} target(s) ...")
        rc, stdout, stderr = _run_timed(linter_bin, *linter_args, *targets,
                                        cwd=worktester_dashboard)
        combined += stdout + stderr
        if rc != 0:
            structured_log.error("gate_failed", f"[gate:lint-fe] FAIL (exit {rc})",
                                 gate="lint", issue_num=issue_num, exit_code=rc)
            passed = False
        else:
            print("  [gate:lint-fe] PASS")
    else:
        structured_log.warn("lint_tool_missing",
                            "[gate:lint-fe] no eslint/biome found; skipping JS/TS lint",
                            issue_num=issue_num)

    # prettier --check
    if passed:
        ok_prettier, prettier_bin, _ = _try("which", "prettier")
        if not ok_prettier and ok_npx:
            rc_pcheck, _, _ = _run_timed(
                npx_path, "--no", "prettier", "--version", cwd=worktester_dashboard
            )
            if rc_pcheck == 0:
                prettier_bin = npx_path
                prettier_prefix = ["--no", "prettier"]
            else:
                prettier_bin = None
                prettier_prefix = []
        else:
            prettier_prefix = []

        if ok_prettier or (not ok_prettier and ok_npx and prettier_bin):
            targets = ["."] if gate_scope == "full" else js_ts_files
            cmd_args = prettier_prefix + ["--check"] + targets
            print(f"  [gate:lint-fe] running prettier --check on {len(targets)} target(s) ...")
            rc, stdout, stderr = _run_timed(prettier_bin, *cmd_args,
                                            cwd=worktester_dashboard)
            combined += stdout + stderr
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:lint-fe] prettier FAIL (exit {rc})",
                                     gate="lint", issue_num=issue_num, exit_code=rc)
                passed = False
            else:
                print("  [gate:lint-fe] prettier PASS")

    return passed, combined


def _gate_design(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
) -> GateResult:
    """Gate: run impeccable detect on the frontend for UI anti-patterns.

    Uses the deterministic pattern-matching detector (no LLM).
    Exit code 0 = no issues found; non-zero = issues detected.
    Skips gracefully when impeccable is not installed or no HTML/CSS/JSX present.
    """
    if skip:
        print("  [gate:design] skipped")
        return GateResult(gate="design", passed=True, skipped=True)

    _post_agent_event("gate:design")

    ok_npx, npx_path, _ = _try("which", "npx")
    if not ok_npx:
        structured_log.warn("design_tool_missing",
                            "[gate:design] npx not found; skipping design gate",
                            issue_num=issue_num)
        return GateResult(gate="design", passed=True, skipped=True,
                          output="npx not found — design gate skipped")

    # Detect if there is any frontend to scan
    fe_exts = (".html", ".css", ".jsx", ".tsx")
    has_frontend = any(
        True for ext in fe_exts
        for _ in worktester_dashboard.rglob(f"*{ext}")
    ) if worktester_dashboard.exists() else False

    if not has_frontend:
        print("  [gate:design] no frontend files found — skipped")
        return GateResult(gate="design", passed=True, skipped=True,
                          output="no frontend files — design gate skipped")

    # Determine scan target
    static_dir = worktester_dashboard / "apps" / "dashboard" / "static"
    if not static_dir.exists():
        static_dir = worktester_dashboard / "static"
    if not static_dir.exists():
        static_dir = worktester_dashboard

    print(f"  [gate:design] running impeccable detect {static_dir.name}/ ...")
    rc, stdout, stderr = _run_timed(
        npx_path, "--yes", "impeccable", "detect", str(static_dir), "--json",
        cwd=worktester_dashboard,
    )
    combined = stdout + stderr

    if rc == 0:
        print("  [gate:design] PASS — no design anti-patterns detected")
        return GateResult(gate="design", passed=True, output=combined)
    else:
        # impeccable exits non-zero when issues are found
        structured_log.error("gate_failed", f"[gate:design] FAIL (exit {rc})",
                             gate="design", issue_num=issue_num, exit_code=rc)
        _revert_to_sit(issue_num, "design", combined, repo_name=repo_name)
        return GateResult(gate="design", passed=False, output=combined)


def _run_quality_gates(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    worktester_dashboard: Path,
    skip_all: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    gate_typecheck: bool = True,
    gate_design: bool = True,
    gate_frontend_lint: bool = True,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> list[GateResult]:
    """Run quality gates sequentially. Returns list of GateResult.

    Order (cheap/deterministic first): typecheck → lint → design → pytest → merge-preview.
    Stops early on first failure (remaining gates are not run).
    If skip_all is True, all gates are skipped.

    base_branch: branch to diff against when gate_scope='changed' (default: 'develop').
    gate_scope: 'changed' (default) scopes gates to changed files only;
                'full' restores legacy full-codebase behaviour.
    """
    results: list[GateResult] = []

    # Gate 1 -- typecheck (cheap, deterministic)
    r_tc = _gate_typecheck(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_typecheck),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )
    results.append(r_tc)
    if not r_tc.passed:
        return results

    # Gate 2 -- lint (Python ruff + frontend eslint/biome/prettier)
    r_lint = _gate_lint(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_lint),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
        gate_frontend_lint=gate_frontend_lint,
    )
    results.append(r_lint)
    if not r_lint.passed:
        return results

    # Gate 3 -- design (impeccable UI anti-pattern detector, no LLM)
    r_design = _gate_design(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_design),
        repo_name=repo_name,
    )
    results.append(r_design)
    if not r_design.passed:
        return results

    # Gate 4 -- pytest
    r_pytest = _gate_pytest(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_pytest),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )
    results.append(r_pytest)
    if not r_pytest.passed:
        return results

    # Gate 5 -- merge-preview (most expensive; run last)
    r_merge = _gate_merge_preview(
        issue_num,
        feature_branch,
        worktester_root,
        skip=(skip_all or not gate_merge_preview),
        target_branch=target_branch,
        repo_name=repo_name,
    )
    results.append(r_merge)

    return results


def _create_sprint_branch(sprint_branch: str) -> None:
    """Create sprint/<label> off develop and push to origin (idempotent)."""
    # Check if branch already exists on remote
    ok, _, _ = _try("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{sprint_branch}")
    if ok:
        print(f"  Sprint branch {sprint_branch!r} already exists on origin — skipping creation.")
        return

    # Check if branch already exists locally
    ok, _, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{sprint_branch}")
    if ok:
        print(f"  Sprint branch {sprint_branch!r} already exists locally — pushing to origin.")
        _run("git", "push", "-u", "origin", sprint_branch)
        return

    print(f"  Creating sprint branch {sprint_branch!r} off develop…")
    # Fetch latest develop
    _run("git", "fetch", "origin")
    # Get develop SHA from remote to avoid checking out develop (which may be in a worktree)
    ok, develop_sha, _ = _try("git", "rev-parse", "origin/develop")
    if not ok or not develop_sha:
        # fallback: try local develop
        ok, develop_sha, _ = _try("git", "rev-parse", "develop")
    if not ok or not develop_sha:
        structured_log.warn("sprint_branch_sha_resolve_failed", "could not resolve develop SHA — using HEAD for sprint branch")
        develop_sha = "HEAD"
    _run("git", "branch", sprint_branch, develop_sha)
    _run("git", "push", "-u", "origin", sprint_branch)
    print(f"  Sprint branch {sprint_branch!r} created and pushed.")


def _call_finish_feature(
    issue_num: int,
    worktester_root: Optional[Path] = None,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    sprint_label: Optional[str] = None,
) -> None:
    """Call finish_feature.py as a subprocess from the worktester root."""
    if cfg is not None:
        finish_script = cfg.finish_feature_script
        wt_root = worktester_root or cfg.worktree_tester
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

    print(f"  Calling finish_feature.py --issue {issue_num} --target-branch {target_branch} ...")
    result = subprocess.run(cmd, cwd=str(wt_root), capture_output=True, text=True, env=sub_env)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        structured_log.error("subprocess_nonzero_exit", f"finish_feature.py exited {result.returncode}", issue_num=issue_num, subprocess="finish_feature.py", exit_code=result.returncode, subprocess_stderr=result.stderr.rstrip() if result.stderr else "")
    else:
        print("  finish_feature.py completed successfully")
    return result.returncode == 0


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
        print("  [documentor] document_issue.py not found — skipping", file=sys.stderr)
        return

    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    print(
        f"  [documentor] running for sprint {sprint_label} "
        f"({len(issue_nums)} ticket(s): {issue_nums}) ..."
    )
    for issue_num in issue_nums:
        cmd = [sys.executable, str(document_script), "--issue", str(issue_num), "--mode", "both"]
        if eff_repo:
            cmd += ["--repo", eff_repo]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.stdout:
                for line in result.stdout.splitlines():
                    print(f"  {line}")
            if result.returncode != 0:
                print(f"  [documentor] issue #{issue_num} exited {result.returncode} (non-fatal)", file=sys.stderr)
                if result.stderr:
                    print(f"  [documentor] stderr: {result.stderr.strip()[:400]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            structured_log.warn("documentor_timeout", "[documentor] timed out after 300s", issue_num=issue_num)
        except Exception as e:
            structured_log.error("documentor_error", f"[documentor] error: {e}", issue_num=issue_num, exc=str(e))
    print(f"  [documentor] completed for sprint {sprint_label}")


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
        print(
            f"  Tester for issue #{issue_num} exited {tester_exit_code} — status: failed",
            flush=True,
        )
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
        branch_is_merged = _is_branch_merged_into(found_branch, target_branch)
        print(
            f"  Issue #{issue_num}: feature branch '{found_branch}' merged into "
            f"'{target_branch}': {branch_is_merged}"
        )
        if not branch_is_merged:
            # Tester exited 0 but skipped finish_feature.py — attempt auto-merge.
            print(f"  Issue #{issue_num}: branch not merged — attempting auto-merge via finish_feature.py ...")
            merge_ok = _call_finish_feature(
                issue_num, wt_root, target_branch, eff_repo, cfg, sprint_label
            )
            if merge_ok:
                # finish_feature.py exited 0 — trust the merge succeeded.
                # Re-verifying via git can return False due to ref-staleness in the
                # sprint-manager worktree, causing a false TESTER_REJECTED failure
                # even though the merge and push completed successfully (issue #659).
                branch_is_merged = True
                # finish_feature.py also deletes the branch, so clear found_branch so
                # already_merged_by_tester resolves True and prevents a double-merge call.
                found_branch = None
                print(f"  Issue #{issue_num}: auto-merge succeeded (finish_feature.py exited 0)")
            if not branch_is_merged:
                warning_body = (
                    f"**Tester exited 0 but feature branch not merged.**\n\n"
                    f"Tester subprocess finished successfully (exit code 0), but "
                    f"`{found_branch}` has not been merged into `{target_branch}`. "
                    f"Auto-merge via finish_feature.py {'also failed' if not merge_ok else 'ran but branch still not detected as merged'}. "
                    f"Re-run the tester to proceed."
                )
                try:
                    github_client.add_comment(issue_num, warning_body, repo_name=eff_repo)
                except Exception as exc:
                    structured_log.warn("missing_merge_comment_failed", f"failed to post missing-merge comment: {exc}", issue_num=issue_num, exc=str(exc))
                if alert_modes:
                    dispatch_alerts(
                        alert_modes,
                        title=f"Issue #{issue_num} skipped: tester exited 0 but branch not merged",
                        body=warning_body[:500],
                        issue_num=issue_num,
                        category=FailureCategory.TESTER_REJECTED,
                        cfg=cfg,
                        repo=eff_repo,
                    )
                return (False,
                        f"Issue #{issue_num}: tester exited 0 but feature branch not merged",
                        FailureCategory.TESTER_REJECTED)
    else:
        # Branch not found locally or remotely — check git log for merge commit.
        branch_is_merged = _was_feature_merged_via_log(issue_num, target_branch)
        if branch_is_merged:
            print(
                f"  Issue #{issue_num}: feature branch not found but merge commit "
                f"found in '{target_branch}' history — treating as merged"
            )
        else:
            print(
                f"  Issue #{issue_num}: feature branch not found and no merge "
                f"commit found in '{target_branch}' — treating as genuine tester skip"
            )
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

    # Merge confirmed. Determine if tester already merged (branch deleted) or if
    # sprint_manager needs to call finish_feature.py after gates pass.
    already_merged_by_tester = (found_branch is None)
    if already_merged_by_tester:
        print(
            f"  Issue #{issue_num}: feature branch deleted — "
            f"tester already merged via finish_feature.py; skipping re-merge"
        )
        # Placeholder so pytest/lint gates can still run; merge-preview will be skipped
        feature_branch = f"feature/{issue_num}-unknown"
    else:
        feature_branch = found_branch

    print(f"\nTester finished for issue #{issue_num} -- running quality gates...")

    if skip_gates:
        print("  --skip-gates active -- skipping all quality gates, proceeding to merge")
        if not already_merged_by_tester:
            _call_finish_feature(issue_num, wt_root, target_branch=target_branch, repo_name=eff_repo, cfg=cfg, sprint_label=sprint_label)
        _post_agent_event("gate:merging", api_url=api_url)
        all_skipped = [
            GateResult(gate="typecheck",     passed=True, skipped=True),
            GateResult(gate="lint",          passed=True, skipped=True),
            GateResult(gate="design",        passed=True, skipped=True),
            GateResult(gate="pytest",        passed=True, skipped=True),
            GateResult(gate="merge-preview", passed=True, skipped=True),
        ]
        _transition_safe(issue_num, _TicketState.UAT, actor="sprint_manager", repo_name=eff_repo)
        _post_success_comment(issue_num, all_skipped, repo_name=eff_repo)
        _delete_failure_sidecar(issue_num)
        if already_merged_by_tester:
            return True, f"Issue #{issue_num}: merge already done by tester, UAT applied, comment posted", None
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
        target_branch=target_branch,
        repo_name=eff_repo,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )

    # Check if all gates passed
    all_passed = all(r.passed for r in results)

    if all_passed:
        _post_agent_event("gate:merging", api_url=api_url)
        if already_merged_by_tester:
            print(f"  All gates passed -- tester already merged via finish_feature.py, skipping re-merge")
        else:
            print(f"  All gates passed -- calling finish_feature.py for issue #{issue_num}")
        if not already_merged_by_tester:
            _call_finish_feature(issue_num, wt_root, target_branch=target_branch, repo_name=eff_repo, cfg=cfg, sprint_label=sprint_label)
        _transition_safe(issue_num, _TicketState.UAT, actor="sprint_manager", repo_name=eff_repo)
        _post_success_comment(issue_num, results, repo_name=eff_repo)
        _delete_failure_sidecar(issue_num)
        if already_merged_by_tester:
            return True, f"Issue #{issue_num}: all gates passed, merge already done by tester, UAT applied", None
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
        print(f"  [retry] failure sidecar not found at {sc_path} — using generic prompt")
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
) -> Optional[Path]:
    """Write a JSON failure sidecar for any failure class.

    Works independently of _FAILURE_PARSING_AVAILABLE — uses a direct path
    construction so it never depends on the optional post_test_report import.

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
            "run_id":           os.environ.get("COMMANDER_RUN_ID"),
            "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        sc_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"  [failure] Wrote sidecar ({failure_class}): {sc_path}", flush=True)
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
            print(f"  [failure] Deleted sidecar on success: {sc_path}", flush=True)
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
    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", "claude-haiku-4-5-20251001",
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


def _agent_identity_env(role: str, issue_num: Optional[int]) -> dict[str, str]:
    """Env vars that tag a dispatched agent's hooks with role + issue number so
    activity-log rows can render '<role> <action> #<issue>' (issue #719).

    issue_num of 0/None (e.g. the reviewer's sprint-level sentinel) emits no
    CLAUDE_AGENT_ISSUE, so no spurious '#0' link is produced.
    """
    env = {"CLAUDE_AGENT_ROLE": role}
    if issue_num:
        env["CLAUDE_AGENT_ISSUE"] = str(issue_num)
    return env


def _dispatch_coder(
    issue_num: int,
    alert_modes: list[str],
    sprint_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    chosen_port: Optional[int] = None,
    rate_limit_events: Optional[list] = None,
    on_running: Optional[object] = None,
    sprint_label: Optional[str] = None,
    prior_failures: Optional[list] = None,
) -> tuple[bool, Optional[str]]:
    """Dispatch a coder agent for the issue.  Returns (ok, failure_category).

    When sprint_branch is not 'develop', sets COMMANDER_MERGE_TARGET in the
    subprocess environment so the coder agent creates the feature branch off
    the sprint branch instead of develop (AC2, AC3).

    Retries up to _RATE_LIMIT_MAX_RETRIES times on 429/rate-limit errors with
    exponential backoff.  Appends events to rate_limit_events when provided.

    on_running: optional zero-argument callable invoked immediately after the
    subprocess is spawned (before proc.wait) to signal coder_running status.

    prior_failures: accumulated failure records from the fix-loop (issue #618).
    When provided, an accumulated context suffix is built from these records
    instead of reading the single-failure sidecar.
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    api_url  = cfg.api_url if cfg else None
    cwd_path = cfg.worktree_coder if cfg else WORKTESTER_DASHBOARD

    guard_err = _design_docs_guard(cwd_path)
    if guard_err:
        structured_log.error(
            "design_docs_missing",
            f"[coder] design docs guard failed for issue #{issue_num}: {guard_err}",
            issue_num=issue_num,
        )
        record_failure(issue_num, "design_docs_missing", detail=guard_err)
        return False, "design_docs_missing"

    print(f"  Dispatching coder for issue #{issue_num} ...", flush=True)
    try:
        structured_log.event(
            "coder.dispatch",
            run_id=os.environ.get("COMMANDER_RUN_ID"),
            issue_num=issue_num,
            sprint_label=sprint_label,
            agent_role="coder",
        )
    except Exception:
        pass
    _post_agent_event(f"coder:issue-{issue_num}", api_url=api_url)

    log_path = _issue_log_path(issue_num, cfg=cfg)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Build prompt
    if cfg and cfg.coder_prompt_template:
        issue_url = f"https://github.com/{_r(eff_repo)}/issues/{issue_num}"
        prompt = cfg.coder_prompt_template.format(issue_url=issue_url)
    else:
        # Detect design context files for coder orientation
        _design_hints = []
        for _doc in ("PRODUCT.md", "DESIGN.md"):
            if (cwd_path / _doc).exists():
                _design_hints.append(_doc)
        _design_prefix = (
            f" If the issue touches frontend/UI, read {' and '.join(_design_hints)} first"
            " to understand design conventions and anti-patterns to avoid."
            if _design_hints else ""
        )

        prompt = (
            f"Read the issue at https://github.com/{_r(eff_repo)}/issues/{issue_num}"
            " and implement it following the project's branching workflow."
            " Use the BA/coder/tester workflow defined in CLAUDE.md."
            " TDD WORKFLOW: before writing implementation code, read the"
            " '## Acceptance Criteria' (or '## Acceptance') section of the issue"
            " and translate each criterion into a pytest test in the tests/ directory."
            " Write the tests first, then implement until all tests pass."
            " You must NOT delete, skip, or weaken any test to make it pass —"
            " every test must be anchored to a specific acceptance criterion and must"
            " pass because the implementation satisfies it, not because the test was"
            " softened."
            f"{_design_prefix}"
        )

    # TDD guard: if custom template omits TDD instruction, append it.
    if "TDD" not in prompt and "acceptance criteria" not in prompt.lower() and "write the tests first" not in prompt.lower():
        prompt += (
            " TDD WORKFLOW: translate each Acceptance Criterion into a pytest test"
            " before writing implementation code. Tests must stay anchored to their"
            " criterion — do not delete or weaken them to achieve a passing run."
        )

    # AC-3 (issue #311): always append the no-merge constraint so the coder
    # never merges to the target branch regardless of prompt template.
    if "must not merge" not in prompt and "finish_feature" not in prompt:
        prompt += (
            " MERGE BOUNDARY (issue #311): your responsibility ends at pushing the"
            " feature branch. You must NOT merge to the target branch (develop or any"
            " sprint branch) by any means — no `git merge`, no PR merge, no"
            " finish_feature.py. Merging is exclusively the tester's job via"
            " `scripts/finish_feature.py` after tests pass."
        )
    # Label boundary (issue #509): coder must never touch GitHub labels.
    if "DO NOT modify any GitHub label" not in prompt:
        prompt += (
            " DO NOT modify any GitHub label on this issue or any other issue."
            " Label transitions are managed by sprint_manager."
            " Do not run update_ticket.py, gh issue edit --add-label, or any other"
            " label-mutation command."
        )

    # Impeccable design context (issue #713): inject into every coder dispatch —
    # custom templates included — so frontend work loads design rules via context.mjs.
    if "context.mjs" not in prompt:
        prompt += _impeccable_context_instruction()

    # Inject failure context: accumulated history (fix-loop, issue #618) or sidecar fallback
    if prior_failures:
        _ctx_lines = [
            f"\n\nThis is fix attempt {len(prior_failures) + 1}. Prior attempts failed:"
        ]
        for _h in prior_failures:
            _cat = _h.get("category", "?")
            _detail = _h.get("reason") or _h.get("summary") or ""
            _ctx_lines.append(f"  - Attempt {_h.get('attempt', 0) + 1}: {_cat}: {_detail}")
        failure_suffix = "\n".join(_ctx_lines)
    else:
        failure_suffix = _build_failure_suffix(issue_num)
    if failure_suffix:
        prompt = prompt + failure_suffix

    # Give the headless run the same coder persona an interactive /coder session
    # would (subagents/slash-commands don't load in `claude -p`). Keep `-p PROMPT`
    # last so the later `cmd[-1] += sprint_hint` append still targets the prompt.
    coder_model = cfg.coder_model if cfg is not None else "claude-sonnet-4-6"
    cmd = [
        "claude",
        "--model", coder_model,
        "--dangerously-skip-permissions",
    ]
    coder_persona = _load_agent_persona("coder", cwd_path)
    if coder_persona:
        cmd += ["--append-system-prompt", coder_persona]
    cmd += ["-p", prompt]

    # Build subprocess environment: inherit current env, set COMMANDER_MERGE_TARGET
    # when in sprint mode (AC2), COMMANDER_APP_PORT if a port was chosen (issue #62),
    # COMMANDER_PROJECT so log output is tagged by project (issue #122), and
    # COMMANDER_SPRINT_RUNNING so child scripts enforce RUN_MUTABLE_LABELS (issue #506).
    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    sub_env.update(_agent_identity_env("coder", issue_num))  # tag hooks/telemetry as the docs prescribe
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo
    if sprint_label:
        sub_env["COMMANDER_SPRINT_RUNNING"] = sprint_label
    if sprint_branch not in ("develop",):
        sub_env["COMMANDER_MERGE_TARGET"] = sprint_branch
        # Always append sprint-mode instructions regardless of whether a custom
        # coder_prompt_template is configured (issue #72 regression fix).
        sprint_hint = (
            f" IMPORTANT: The env var COMMANDER_MERGE_TARGET is set to {sprint_branch!r}."
            f" Create the feature branch off {sprint_branch!r} by passing"
            f" --base-branch {sprint_branch!r} to start_feature.py."
            f" This is SPRINT MODE: do NOT open a PR after pushing —"
            f" the sprint manager will create the single PR at sprint end."
        )
        cmd[-1] = cmd[-1] + sprint_hint
    if chosen_port is not None:
        sub_env["COMMANDER_APP_PORT"] = str(chosen_port)
        print(f"  [port] COMMANDER_APP_PORT={chosen_port} injected into coder env")

    # Pre-touch so the dispatch-log endpoint always has a file to read.
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        open_mode = "w" if attempt == 0 else "a"
        try:
            with log_path.open(open_mode) as log_f:
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
                print("  [coder] claude CLI not found -- stub success")
                if on_running is not None:
                    try:
                        on_running()
                    except Exception:
                        pass
                return True, None
            # Production: log the error and return a real failure so the stall
            # warning shows "claude CLI not found" instead of silently succeeding.
            err_msg = (
                f"[coder] ERROR: claude CLI not found for issue #{issue_num}.\n"
                f"PATH={sub_env.get('PATH', '<empty>')}\n"
                "Sprint cannot proceed. Install claude CLI or set COMMANDER_ALLOW_STUB_SUCCESS=1 for testing.\n"
            )
            structured_log.error("claude_cli_not_found", f"claude CLI not found for issue #{issue_num}", issue_num=issue_num, subprocess="coder", path=sub_env.get("PATH", ""))
            try:
                with log_path.open("a") as lf:
                    lf.write(err_msg)
            except OSError:
                pass
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{issue_num}: claude CLI not found",
                body=f"_dispatch_coder failed to spawn 'claude' subprocess: file not found. PATH={sub_env.get('PATH', '<empty>')}. Sprint cannot proceed.",
                issue_num=issue_num,
                category=FailureCategory.CRASH,
                cfg=cfg,
                repo=eff_repo,
            )
            return False, FailureCategory.CRASH

        if on_running is not None:
            try:
                on_running()
            except Exception:
                pass

        detector = HangDetector(issue_num=issue_num, log_path=log_path, proc=proc)
        detector.start()
        rc = proc.wait()
        detector.stop()

        # Exit code 0 means success unconditionally — check before detector.killed
        # to guard against the same race as _dispatch_tester (see issue #659).
        if rc == 0:
            return True, None

        if detector.killed:
            reason = f"No log activity for {HANG_KILL_SECS//60} minutes"
            _add_blocked_label(issue_num, reason, repo_name=eff_repo, sprint_label=sprint_label)
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{issue_num}: HANG detected",
                body=f"The coder subprocess produced no output for {HANG_KILL_SECS//60} minutes and was killed.",
                issue_num=issue_num,
                category=FailureCategory.HANG,
                cfg=cfg,
                repo=eff_repo,
            )
            record_failure(
                issue_num,
                "hang",
                detail=_build_crash_detail(log_path, signal="SIGKILL"),
                summary=f"Issue #{issue_num}: coder hung for {HANG_KILL_SECS//60} minutes and was killed",
            )
            return False, FailureCategory.HANG

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
            print(f"  Rate limit hit, retrying in {delay} seconds (attempt {retry_num}/{_RATE_LIMIT_MAX_RETRIES})", flush=True)
            if rate_limit_events is not None:
                rate_limit_events.append({
                    "issue_num": issue_num,
                    "role": "coder",
                    "attempt": retry_num,
                    "delay_secs": delay,
                    "timestamp": _utcnow(),
                })
            time.sleep(delay)
            continue

        if is_rl:
            print(f"  Subscription rate limit exhausted for coder issue #{issue_num} after {_RATE_LIMIT_MAX_RETRIES} retries", flush=True)
            if rate_limit_events is not None:
                rate_limit_events.append({
                    "issue_num": issue_num,
                    "role": "coder",
                    "attempt": _RATE_LIMIT_MAX_RETRIES,
                    "delay_secs": 0,
                    "exhausted": True,
                    "timestamp": _utcnow(),
                })
            return False, FailureCategory.RETRY_EXHAUSTED

        record_failure(
            issue_num,
            "crash",
            detail=_build_crash_detail(log_path, exit_code=rc),
            summary=f"Issue #{issue_num}: coder exited with code {rc}",
        )
        return False, FailureCategory.CRASH

    # Should not be reached, but satisfy the type checker
    record_failure(
        issue_num,
        "crash",
        detail=_build_crash_detail(log_path, exit_code=-1),
        summary=f"Issue #{issue_num}: coder dispatch loop exhausted unexpectedly",
    )
    return False, FailureCategory.CRASH


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
) -> tuple[int, Optional[str]]:
    """Dispatch a tester agent.  Returns (exit_code, failure_category_if_hang).

    When sprint_branch is not 'develop', sets COMMANDER_MERGE_TARGET in the
    subprocess environment so the tester agent merges the feature branch into
    the sprint branch instead of develop (AC2, AC4).

    Retries up to _RATE_LIMIT_MAX_RETRIES times on 429/rate-limit errors with
    exponential backoff.  Appends events to rate_limit_events when provided.

    on_running: optional zero-argument callable invoked immediately after the
    subprocess is spawned (before proc.wait) to signal tester_running status.
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    api_url  = cfg.api_url if cfg else None
    cwd_path = cfg.worktree_tester_app if cfg else WORKTESTER_DASHBOARD

    print(f"  Dispatching tester for issue #{issue_num} ...", flush=True)
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
            f" you MUST immediately run `python3 dashboard/scripts/finish_feature.py --issue {issue_num}`"
            " from the repo root without asking. The script reads COMMANDER_MERGE_TARGET from its"
            " own env to pick the merge target — do not override with --target-branch."
            " finish_feature.py merges the branch; do NOT separately apply any label — the"
            " UAT label is applied automatically by sprint_manager after merge confirmation."
            " NEVER apply the UAT-approved label or close the issue — UAT-approved is set ONLY by the human"
            " via the dashboard Approve button or scripts/approve_ticket.py."
            " Do NOT output language like 'let me know if you want me to...' —"
            " complete the full workflow autonomously by running finish_feature.py and then stop."
            # AC-1 / AC-2 (issue #311): explicit prohibition of direct merge paths
            " MERGE PATH ENFORCEMENT (issue #311): `scripts/finish_feature.py` is the ONLY"
            " sanctioned merge path. You are FORBIDDEN from running `git merge`, opening"
            " or merging a PR directly, or pushing commits to the target branch by any"
            " means other than finish_feature.py. Merging by any other path constitutes a"
            " workflow failure — halt immediately and report the violation rather than proceeding."
        )
    # Label boundary (issue #509): tester must never touch GitHub labels.
    if "DO NOT modify any GitHub label" not in prompt:
        prompt += (
            " DO NOT modify any GitHub label on this issue or any other issue."
            " Label transitions are managed by sprint_manager."
            " Do not run update_ticket.py, gh issue edit --add-label, or any other"
            " label-mutation command."
        )
    # Impeccable design context (issue #713): inject into every tester dispatch so
    # the tester verifies UI tickets against the same design rules via context.mjs.
    if "context.mjs" not in prompt:
        prompt += _impeccable_context_instruction()
    # Same persona fix as the coder: load the tester subagent for the headless run.
    tester_model = cfg.tester_model if cfg is not None else "claude-sonnet-4-6"
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
        print(f"  [port] COMMANDER_APP_PORT={chosen_port} injected into tester env")

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
        print(f"  [issue-test-repo] GITHUB_ISSUE_TEST_REPO={issue_test_repo!r} injected into tester env")
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
        print("  [issue-test-repo] GITHUB_ISSUE_TEST_REPO not set — tester will skip live issue/label tests")

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
    print(f"  [agent-browser] available={browser_available} injected into tester env")

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
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
                print("  [tester] claude CLI not found -- stub success")
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

        detector = HangDetector(issue_num=issue_num, log_path=log_path, proc=proc)
        detector.start()
        rc = proc.wait()
        detector.stop()

        # Exit code 0 means success unconditionally — check before detector.killed
        # to guard against a race where the hang detector fires after the process
        # already exited cleanly (proc.kill raises ProcessLookupError but still
        # sets _killed=True, causing a false HANG failure on exit 0).
        if rc == 0:
            print(f"  Tester for issue #{issue_num} exited 0 — status: passed", flush=True)
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
            print(f"  Rate limit hit, retrying in {delay} seconds (attempt {retry_num}/{_RATE_LIMIT_MAX_RETRIES})", flush=True)
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
            print(f"  Subscription rate limit exhausted for tester issue #{issue_num} after {_RATE_LIMIT_MAX_RETRIES} retries", flush=True)
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


# ── sprint summary report (AC-1, AC-2, AC-3) ─────────────────────────────────

LEARNINGS_STUB = (
    "_TODO: replace this stub with your retrospective notes._\n\n"
    "What went well? What should we do differently next sprint?"
)


def _follow_up_action(category: Optional[str]) -> str:
    mapping = {
        FailureCategory.HANG:            "Investigate subprocess logs; check for infinite loops or network waits. Retry manually.",
        FailureCategory.CRASH:           "Examine the coder/tester log for the exception. Fix the underlying issue and retry.",
        FailureCategory.GATE_FAIL:       "Review the gate output in the GitHub comment. Fix failing tests or linting errors.",
        FailureCategory.TESTER_REJECTED: "The tester did not advance the issue to UAT. Review the tester log and re-run tester.",
        FailureCategory.RETRY_EXHAUSTED: "Max retries reached. Manually investigate and fix the issue.",
        FailureCategory.CODER_NO_WORK:   "Coder produced no feature branch. Review the AC and re-run the coder.",
        FailureCategory.PYTEST_FAIL:     "Pytest gate failed. Review the GitHub comment, fix the failing tests, and re-run.",
        FailureCategory.LINT_FAIL:       "Lint gate failed. Fix the ruff errors noted in the GitHub comment and re-run.",
        FailureCategory.MERGE_CONFLICT:  "Merge-preview gate detected conflicts. Resolve conflicts against develop and re-run.",
    }
    return mapping.get(category or "", "Review the issue manually.")


def _build_screenshots_section(
    state: SprintState,
    sprints_dir: Path,
    repo_name: Optional[str],
) -> list[str]:
    """Build the per-ticket Screenshots section lines for the sprint summary (#712).

    Scans ``<sprints_dir>/sprint-<N>/screenshots/issue-<N>/`` for each ticket and,
    for any with captured browser screenshots, emits a sub-section with the count
    and inline images (or links when a URL manifest is absent). Returns an empty
    list when no ticket has screenshots, so the section is omitted entirely.
    """
    n = state.sprint_number if state.sprint_number is not None else state.sprint_label
    blocks: list[str] = []
    for issue in state.issues:
        try:
            url_map = _load_screenshot_url_map(sprints_dir, n, issue.number)
            shots = agent_browser_runner.collect_issue_screenshots(
                sprints_dir, n, issue.number, url_map=url_map
            )
        except Exception:
            shots = []
        if not shots:
            continue
        section = agent_browser_runner.build_screenshot_section(
            shots, heading=f"Issue #{issue.number} — {issue.title}", heading_level=3
        )
        if section:
            blocks.append(section)
    if not blocks:
        return []
    lines = ["## Screenshots", ""]
    for block in blocks:
        lines.append(block)
        lines.append("")
    return lines


def _load_screenshot_url_map(sprints_dir: Path, sprint_num, issue_num) -> Optional[dict]:
    """Read an optional {filename: raw_url} manifest written at upload time (#712)."""
    manifest = (
        agent_browser_runner.sprint_screenshot_dir(sprints_dir, sprint_num, issue_num)
        / "urls.json"
    )
    if not manifest.exists():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None


def generate_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
    sprint_branch: Optional[str] = None,
    sprints_dir: Optional[Path] = None,
) -> str:
    """Generate a richly-formatted executive summary markdown string.

    When ``sprints_dir`` is provided (issue #712), each ticket's captured UAT
    browser screenshots are listed in a Screenshots section; tickets with no
    browser steps contribute nothing (no regression for legacy runs).
    """
    n = state.sprint_number if state.sprint_number is not None else state.sprint_label

    start_ts = _to_bangkok(state.start_timestamp) if state.start_timestamp else _bangkok_now()
    end_ts   = _bangkok_now()

    h, rem   = divmod(int(elapsed_secs), 3600)
    m_int, s = divmod(rem, 60)
    duration_str = f"{h}h {m_int}m {s}s"

    completed = [i for i in state.issues if i.status == "done"]
    skipped   = [i for i in state.issues if i.status == "skipped"]
    pending   = [i for i in state.issues if i.status == "pending"]

    # Prefer DB-backed rollup for per-ticket metrics; fall back to in-memory state.
    _db_rollup: Optional[dict] = None
    if _SPRINT_REPO_AVAILABLE and _sprint_repo is not None:
        try:
            _db_rollup = _sprint_repo.get_sprint_rollup(state.sprint_label)
        except Exception:
            _db_rollup = None

    if _db_rollup is not None and _db_rollup["sum_tokens"] > 0:
        total_tokens = _db_rollup["sum_tokens"]
    else:
        total_tokens = state.total_tokens_in + state.total_tokens_out

    # cost_estimate: all agents (coder, tester, preflight) run via Claude Code CLI
    # which is subscription-funded — no raw API charges.
    cost_estimate_usd = 0.0

    if _db_rollup is not None and _db_rollup["avg_elapsed_seconds"] is not None:
        avg_ticket_secs = _db_rollup["avg_elapsed_seconds"]
    else:
        avg_ticket_secs = (elapsed_secs / len(completed)) if completed else 0
    avg_h, avg_r     = divmod(int(avg_ticket_secs), 3600)
    avg_m, avg_s     = divmod(avg_r, 60)
    avg_ticket_str   = f"{avg_h}h {avg_m}m {avg_s}s" if completed else "--"

    tester_rejections = sum(
        1 for i in state.issues
        if i.category == FailureCategory.TESTER_REJECTED
    )
    merge_conflicts = sum(
        1 for i in state.issues
        if i.category == FailureCategory.GATE_FAIL
        and "conflict" in (i.skip_reason or "").lower()
    )
    gate_total    = len([i for i in state.issues if i.status in ("done", "skipped")])
    gate_passed   = len(completed)
    gate_pass_rate = round(gate_passed / gate_total * 100, 1) if gate_total else 0.0

    r = _r(repo_name)
    sprint_filter_url = f"https://github.com/{r}/issues?q=label%3A{state.sprint_label}"

    lines: list[str] = []

    # -- Header section --
    attempted = len(completed) + len(skipped)
    lines += [
        f"## Sprint {n} -- {end_reason}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Sprint number | {n} |",
        f"| Start | {start_ts} |",
        f"| End | {end_ts} |",
        f"| Duration | {duration_str} |",
        f"| End reason | {end_reason} |",
        f"| Attempted | {attempted} |",
        f"| Completed | {len(completed)} |",
        f"| Skipped | {len(skipped)} |",
        f"| Failed | {len(skipped)} |",
        "",
    ]

    # -- Pending UAT Review --
    lines += [
        "## Pending UAT Review",
        "",
        "| Issue # | Title | Time taken | Outcome | Size |",
        "|---|---|---|---|---|",
    ]
    if completed:
        for issue in completed:
            lines.append(f"| #{issue.number} | {issue.title} | -- | merged — awaiting UAT review | -- |")
    else:
        lines.append("| -- | No issues merged this sprint | -- | -- | -- |")
    lines.append("")

    # -- What Didn't Ship --
    lines += [
        "## What Didn't Ship",
        "",
        "| Issue # | Title | Failure category | Reason |",
        "|---|---|---|---|",
    ]
    if skipped:
        for issue in skipped:
            cat    = issue.category or "unknown"
            reason = (issue.skip_reason or "no reason recorded").replace("|", "/")
            lines.append(f"| #{issue.number} | {issue.title} | {cat} | {reason} |")
    else:
        lines.append("| -- | All issues shipped | -- | -- |")
    lines.append("")

    # -- Suggested follow-up actions --
    if skipped:
        lines += ["## Suggested Follow-up Actions", ""]
        for issue in skipped:
            action = _follow_up_action(issue.category)
            lines.append(f"- **#{issue.number} {issue.title}** ({issue.category or 'unknown'}): {action}")
        lines.append("")

    # -- Stats --
    cost_str = "$0.00 (all agents subscription-funded via Claude Code)"
    lines += [
        "## Stats",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total Tokens | {total_tokens} |",
        f"| Avg ticket time | {avg_ticket_str} |",
        f"| Quality-gate pass rate | {gate_pass_rate}% |",
        f"| Tester rejections | {tester_rejections} |",
        f"| Merge conflicts | {merge_conflicts} |",
        f"| Cost estimate | {cost_str} |",
        "",
    ]

    # -- Rate Limit Events --
    if state.rate_limit_events:
        lines += ["## Rate Limit Events", ""]
        lines += [
            "| Issue # | Role | Attempt | Delay (s) | Exhausted | Timestamp |",
            "|---|---|---|---|---|---|",
        ]
        for ev in state.rate_limit_events:
            exhausted = "Yes" if ev.get("exhausted") else "No"
            lines.append(
                f"| #{ev.get('issue_num', '?')} "
                f"| {ev.get('role', '?')} "
                f"| {ev.get('attempt', '?')}/{_RATE_LIMIT_MAX_RETRIES} "
                f"| {ev.get('delay_secs', '?')} "
                f"| {exhausted} "
                f"| {ev.get('timestamp', '?')} |"
            )
        lines.append("")

    # -- Screenshots (issue #712) --
    if sprints_dir is not None:
        screenshots_block = _build_screenshots_section(state, sprints_dir, repo_name)
        if screenshots_block:
            lines += screenshots_block

    # -- Carried Over --
    lines += ["## Carried Over", ""]
    carried_items: list[str] = []
    for issue in pending:
        carried_items.append(f"- #{issue.number} {issue.title} -- candidate for next sprint")
    for issue in (open_issues or []):
        num   = issue.get("number", "?")
        title = issue.get("title", "")
        carried_items.append(f"- #{num} {title} -- candidate for next sprint")
    if carried_items:
        lines.extend(carried_items)
    else:
        lines.append("No issues carried over.")
    lines.append("")

    # -- Key Learnings --
    lines += [
        "## Key Learnings",
        "",
        LEARNINGS_STUB,
        "",
    ]

    # -- Links --
    all_links: list[str] = [
        f"- [Sprint {n} issues on GitHub]({sprint_filter_url})",
    ]
    for issue in state.issues:
        link = f"https://github.com/{r}/issues/{issue.number}"
        all_links.append(f"- [Issue #{issue.number} -- {issue.title[:50]}]({link})")

    lines += ["## Links", ""]
    if len(all_links) > 3:
        lines.append("<details>")
        lines.append(f"<summary>{len(all_links)} links -- click to expand</summary>")
        lines.append("")
        lines.extend(all_links)
        lines.append("")
        lines.append("</details>")
    else:
        lines.extend(all_links)
    lines.append("")

    # -- Next Step (sprint branch PR instructions) --
    effective_sprint_branch = sprint_branch or f"sprint/{state.sprint_label}"
    r = _r(repo_name)
    lines += [
        "## Next Step",
        "",
        f"The sprint branch `{effective_sprint_branch}` is ready for review.",
        "When UAT is complete, open a PR to promote it to `develop`:",
        "",
        f"```bash",
        f"gh pr create --base develop --head {effective_sprint_branch} --repo {r}",
        f"```",
        "",
    ]

    # -- Footer --
    lines.append(f"_Generated by sprint-manager v1.0 on {_bangkok_now()}_")

    return "\n".join(lines)


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


def _is_stale_summary(
    body: str,
    state_reason: Optional[str],
    state_file_mtime: Optional[float] = None,
    issue_created_at: Optional[str] = None,
) -> tuple[bool, str]:
    """Return (is_stale, reason) for an existing summary issue body.

    Staleness criteria (any one is sufficient):
    1. Issue was closed with state_reason "not_planned".
    2. Body looks like a stub/failed run (stopped + all TESTER_REJECTED + nothing shipped).
    3. The sprint state file on disk is newer than the GitHub issue (AC-2: state-file
       timestamp check) — i.e. a newer sprint run has already completed locally but its
       results were never reflected in the GitHub issue.
    """
    if state_reason == "not_planned":
        return True, "closed as not_planned"

    has_stopped    = "| End reason | stopped |" in body
    has_zero_dur   = bool(re.search(r"\| Duration \| 0h 0m \d+s \|", body))
    has_rejected   = "TESTER_REJECTED" in body
    no_shipped     = "No issues shipped" in body

    if has_stopped and has_zero_dur and has_rejected and no_shipped:
        return True, "stub-mode run (stopped, zero duration, all TESTER_REJECTED, nothing shipped)"

    if has_stopped and has_rejected and no_shipped:
        return True, "failed-run summary (stopped, all TESTER_REJECTED, nothing shipped)"

    # State-file timestamp check: if the local state file is newer than when the issue
    # was created, the issue was produced by an older sprint run.
    if state_file_mtime is not None and issue_created_at:
        try:
            issue_ts = datetime.fromisoformat(
                issue_created_at.rstrip("Z")
            ).replace(tzinfo=timezone.utc).timestamp()
            if state_file_mtime > issue_ts:
                return True, "state file is newer than summary issue (newer sprint run completed locally)"
        except (ValueError, OSError):
            pass  # malformed timestamp or missing file — skip this check

    return False, ""


def create_summary_github_issue(
    content: str,
    sprint_number: Optional[int],
    sprint_label: str,
    repo_name: Optional[str] = None,
    force_summary: bool = False,
    state_file_path: Optional[Path] = None,
) -> tuple[Optional[int], Optional[str]]:
    """AC-2: Create a GitHub issue with the summary markdown as the body.

    AC-1/AC-6: Before creating, searches GitHub (open + closed) for an issue
    with the exact title.  If one already exists and is stale (or force_summary
    is set), updates it in place.  Otherwise skips creation (AC-2).
    If none exists, creates the issue (AC-3).

    state_file_path: optional path to the sprint state JSON file; when provided
    its mtime is compared to the existing issue's createdAt to detect staleness
    (AC-2: state-file timestamp check).
    """
    n      = sprint_number if sprint_number is not None else sprint_label
    title  = f"Sprint {n} Executive Summary"
    labels = ["docs", f"sprint-{n}", "sprint-summary"]

    # AC-1 / AC-6: deduplication check — search both open and closed states
    try:
        existing = github_client.search_issues_by_title(title, repo_name=repo_name)
    except Exception as e:
        structured_log.warn("dedup_search_failed", f"deduplication search failed: {e}", exc=str(e))
        existing = []

    if existing:
        found       = existing[0]
        existing_num = found.get("number")
        existing_url = found.get("url", "")
        existing_state = found.get("state", "")

        # Fetch full issue to get body and stateReason for staleness check
        full_issue: dict = {}
        try:
            full_issue = github_client.get_issue(existing_num, repo_name=repo_name)
        except Exception as e:
            structured_log.warn("summary_issue_fetch_failed", f"could not fetch existing summary issue body: {e}", exc=str(e))

        # Compute state-file mtime for the timestamp staleness check (best-effort)
        state_file_mtime: Optional[float] = None
        if state_file_path is not None:
            try:
                state_file_mtime = state_file_path.stat().st_mtime
            except OSError:
                pass

        is_stale, stale_reason = _is_stale_summary(
            body             = full_issue.get("body", ""),
            state_reason     = full_issue.get("stateReason"),
            state_file_mtime = state_file_mtime,
            issue_created_at = full_issue.get("createdAt"),
        )

        if force_summary or is_stale:
            action = "--force-summary" if force_summary else f"stale ({stale_reason})"
            print(f"  [summary] Existing issue #{existing_num} is {action} — updating in place.")
            try:
                github_client.update_issue_body(existing_num, content, repo_name=repo_name)
            except Exception as e:
                structured_log.warn("summary_issue_update_failed", f"failed to update summary issue body: {e}", exc=str(e))
            if existing_state == "closed":
                try:
                    github_client.reopen_issue(existing_num, repo_name=repo_name)
                    print(f"  [summary] Reopened issue #{existing_num}.")
                except Exception as e:
                    structured_log.warn("summary_issue_reopen_failed", f"failed to reopen summary issue: {e}", exc=str(e))
            comment = (
                f"Summary updated after fresh sprint run on {_utcnow()}. "
                f"Previous content was from a failed run."
            )
            try:
                github_client.add_comment(existing_num, comment, repo_name=repo_name)
            except Exception as e:
                structured_log.warn("summary_comment_failed", f"failed to add update comment: {e}", exc=str(e))
            return existing_num, existing_url

        # Valid existing summary — skip creation
        print(
            f"  [summary] Issue already exists: #{existing_num} {existing_url}"
            f" (state={existing_state}) — skipping creation."
        )
        return existing_num, existing_url

    # AC-3: no duplicate found — create as normal
    # Ensure sprint-summary label exists with muted indigo color
    try:
        r = _r(repo_name)
        subprocess.run(
            ["gh", "label", "create", "sprint-summary",
             "--color", "6D28D9",
             "--description", "Sprint executive summary issues",
             "--repo", r, "--force"],
            capture_output=True, text=True, check=False,
        )
    except Exception:
        pass
    _ensure_github_labels(labels, repo_name=repo_name)

    try:
        issue_num, url = github_client.create_issue(
            title=title, body=content, labels=labels, repo_name=repo_name
        )
        print(f"  Summary GitHub issue created: {url}")
        return issue_num, url
    except Exception as e:
        structured_log.warn("summary_issue_create_failed", f"failed to create summary GitHub issue: {e}", exc=str(e))
        return None, None


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
    n = sprint_number if sprint_number is not None else sprint_label
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
                print(f"  [cancel] Closed summary issue #{num} — sprint was cancelled.")
            except Exception as exc:
                structured_log.warn(
                    "cancel_summary_close_failed",
                    f"could not close summary issue #{num}: {exc}",
                    issue_num=num,
                    exc=str(exc),
                )


def _prompt_learnings(
    content: str,
    path: Path,
    sprint_number: Optional[int],
    sprint_label: str,
    summary_issue_num: Optional[int],
    repo_name: Optional[str] = None,
) -> str:
    """AC-3: Interactive learnings prompt."""
    n = sprint_number if sprint_number is not None else sprint_label
    if not sys.stdout.isatty():
        return content  # non-interactive: leave stub in place

    try:
        answer = input(f"\nSprint {n} done. Want to add learnings to the summary? (y/n) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return content

    if answer != "y":
        return content

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tmp_path = Path(tf.name)
        tf.write(LEARNINGS_STUB + "\n")

    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.run([editor, str(tmp_path)], check=False)
        new_learnings = tmp_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        structured_log.warn("editor_failed", f"editor failed: {e}", exc=str(e))
        new_learnings = ""
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    if not new_learnings or new_learnings == LEARNINGS_STUB.strip():
        print("  No learnings entered; keeping placeholder.")
        return content

    updated = content.replace(LEARNINGS_STUB, new_learnings)

    path.write_text(updated, encoding="utf-8")
    print(f"  Key Learnings updated in {path}")

    if summary_issue_num is not None:
        r = _r(repo_name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                         encoding="utf-8") as tf2:
            tmp2 = Path(tf2.name)
            tf2.write(updated)
        try:
            subprocess.run(
                ["gh", "issue", "edit", str(summary_issue_num),
                 "--repo", r, "--body-file", str(tmp2)],
                capture_output=True, text=True, check=False,
            )
            print(f"  GitHub issue #{summary_issue_num} body updated with learnings.")
        except Exception as e:
            structured_log.warn("github_update_failed", f"failed to update GitHub issue body: {e}", exc=str(e))
        finally:
            try:
                tmp2.unlink()
            except Exception:
                pass

    return updated


def write_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    alert_modes: Optional[list[str]] = None,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
    sprint_branch: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    dry_run: bool = False,
    force_summary: bool = False,
) -> Optional[Path]:
    """Write summary file, create GitHub issue, prompt for learnings (AC-1/2/3).

    AC-5: When dry_run=True the function writes the local summary file and
    prints a dry-run notice, but does NOT create or search GitHub issues.

    Returns None when end_reason is "cancelled" (issue #365 AC-3/AC-5).
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)

    # Guard: skip local file and GitHub issue for cancelled sprints (issue #365 AC-3, AC-5).
    if end_reason == "cancelled":
        print("  [cancel] Sprint was cancelled — skipping summary file and GitHub issue.")
        return None

    content = generate_sprint_summary(
        state,
        elapsed_secs,
        end_reason=end_reason,
        open_issues=open_issues,
        repo_name=eff_repo,
        sprint_branch=sprint_branch,
        sprints_dir=(cfg.sprints_dir if cfg is not None else SPRINTS_DIR),
    )
    path = _summary_path(state.sprint_number, state.sprint_label, cfg=cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  Sprint summary written to {path}")

    # Dispatch via all configured alert channels (issue #24)
    if alert_modes:
        title = f"Sprint {state.sprint_label} summary"
        dispatch_alerts(alert_modes, title=title, body=content[:2000], cfg=cfg, repo=eff_repo)

    # AC-5: skip GitHub issue creation entirely for dry runs
    if dry_run:
        print("  [dry-run] would create summary GitHub issue")
        return path

    # AC-2: Create GitHub issue (best-effort); deduplication handled inside
    state_path = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
    try:
        summary_issue_num, summary_issue_url = create_summary_github_issue(
            content         = content,
            sprint_number   = state.sprint_number,
            sprint_label    = state.sprint_label,
            repo_name       = eff_repo,
            force_summary   = force_summary,
            state_file_path = state_path,
        )
    except Exception as exc:
        structured_log.warn("summary_issue_create_failed", f"create_summary_github_issue raised: {exc}", exc=str(exc))
        summary_issue_num, summary_issue_url = None, None

    # Store summary_issue_url in state JSON
    if summary_issue_url:
        try:
            if state_path.exists():
                state_dict = json.loads(state_path.read_text())
            else:
                state_dict = state.to_dict()
            state_dict["summary_issue_url"] = summary_issue_url
            state_path.write_text(json.dumps(state_dict, indent=2))
        except Exception as e:
            structured_log.warn("state_file_update_failed", f"could not update state file with summary_issue_url: {e}", exc=str(e))

    # AC-3: Interactive learnings prompt
    _prompt_learnings(
        content=content,
        path=path,
        sprint_number=state.sprint_number,
        sprint_label=state.sprint_label,
        summary_issue_num=summary_issue_num,
        repo_name=eff_repo,
    )

    return path


# ── sprint branch PR creation (AC6, AC7) ─────────────────────────────────────

def _create_sprint_pr(
    sprint_branch: str,
    sprint_label: str,
    sprint_number: Optional[int],
    state: "SprintState",
    repo_name: Optional[str] = None,
) -> Optional[str]:
    """Create a PR from sprint_branch → develop at the end of a sprint.

    Returns the PR URL string, or None if creation failed (best-effort).
    """
    r = _r(repo_name)
    n = sprint_number if sprint_number is not None else sprint_label
    shipped = [i for i in state.issues if i.status == "done"]

    ticket_lines = "\n".join(
        f"- #{i.number} {i.title}" for i in shipped
    ) or "No tickets shipped."

    skipped = [i for i in state.issues if i.status == "skipped"]
    skipped_lines = "\n".join(
        f"- #{i.number} {i.title} ({i.category or 'unknown'})" for i in skipped
    ) or "None."

    body = (
        f"## Sprint {n} — auto-generated PR\n\n"
        f"This PR promotes `{sprint_branch}` into `develop` after all sprint issues "
        f"have been processed.\n\n"
        f"### Shipped ({len(shipped)} tickets)\n\n"
        f"{ticket_lines}\n\n"
        f"### Skipped / Failed ({len(skipped)} tickets)\n\n"
        f"{skipped_lines}\n\n"
        f"### Stats\n\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"| Total tokens in | {state.total_tokens_in} |\n"
        f"| Total tokens out | {state.total_tokens_out} |\n"
        f"| Wall clock | {state.wall_clock_secs:.0f}s |\n\n"
        f"_Review and merge when UAT is complete._"
    )

    title = f"Sprint {n} — {len(shipped)} ticket(s) shipped"

    print(f"  Creating PR: {sprint_branch} → develop ...")
    try:
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--repo", r,
                "--base", "develop",
                "--head", sprint_branch,
                "--title", title,
                "--body", body,
            ],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            pr_url = result.stdout.strip()
            print(f"  Sprint PR created: {pr_url}")
        else:
            stderr = result.stderr.strip()
            # If a PR already exists for this branch, gh will print its URL in stderr
            if "already exists" in stderr or "already have" in stderr.lower():
                # Extract URL from stderr (gh prints "a pull request for branch ... already exists: <url>")
                m = re.search(r"https://github\.com/\S+", stderr)
                if m:
                    pr_url = m.group(0)
                    print(f"  Sprint PR already exists: {pr_url}")
                else:
                    structured_log.error("sprint_pr_create_failed", f"failed to create sprint PR: {stderr}", subprocess_stderr=stderr)
                    return None
            else:
                structured_log.error("sprint_pr_create_failed", f"failed to create sprint PR: {stderr}", subprocess_stderr=stderr)
                return None

        # Auto-merge the sprint PR into develop (sprint run is complete, no manual review needed)
        merge_result = subprocess.run(
            ["gh", "pr", "merge", pr_url, "--repo", r, "--merge", "--delete-branch"],
            capture_output=True, text=True, check=False,
        )
        if merge_result.returncode == 0:
            print(f"  Sprint PR auto-merged: {pr_url}")
        else:
            print(f"  Sprint PR created but auto-merge failed (merge manually): {merge_result.stderr.strip()}")
        return pr_url
    except Exception as e:
        structured_log.error("sprint_pr_create_failed", f"exception creating sprint PR: {e}", exc=str(e))
        return None


# ── Reviewer dispatch (issue #159 / #160) ────────────────────────────────────
#
# Sprint.yaml override example (add under the `agents:` key):
#
#   agents:
#     reviewer_prompt_template: |
#       You are the code reviewer for sprint {sprint_label}.
#       Repo: {repo_name}. Diff: {base_sha}..{head_sha}.
#       Sprint summary issue: #{summary_issue_num}.
#       ... (your custom instructions here) ...
#
# All six placeholders are available in every template (custom or default):
#   {sprint_label}       e.g. "sprint-12"
#   {sprint_branch}      e.g. "sprint/sprint-12"
#   {base_sha}           base commit SHA of the sprint branch
#   {head_sha}           head commit SHA of the sprint branch
#   {summary_issue_num}  GitHub issue number of the sprint summary
#   {sprint_filter_url}  GitHub issues URL filtered by sprint label
#   {repo_name}          e.g. "zealchaiwut/commander"

DEFAULT_REVIEWER_PROMPT = """\
You are the **Code Reviewer** agent for sprint {sprint_label}.

## Context

- Repo: {repo_name}
- Sprint branch: {sprint_branch} (already merged to develop)
- Diff range: {base_sha}..{head_sha}
- Sprint summary issue: #{summary_issue_num}
- Sprint tickets: {sprint_filter_url}

## Your Mandate (Read-Only Review)

You perform a structured code review of everything merged this sprint. \
You do NOT modify code, make commits, push branches, close tickets, \
apply labels to merged tickets, run tests, or run the application. \
You post EXACTLY ONE comment on the sprint summary issue. \
You do NOT review tickets that failed or were not merged.

## Step 1 — Collect the diff

Run these two commands and read their output carefully:

```
git diff {base_sha}..{head_sha}
git diff {base_sha}..{head_sha} --stat
```

## Step 2 — Identify merged tickets

Run:

```
gh issue view {summary_issue_num} --json body
```

Parse the sprint summary body to extract the list of merged ticket numbers \
(look for checkboxes or a "Merged" / "Done" section). \
For each merged ticket N, run:

```
gh issue view N --json number,title,body,labels
```

Skip any ticket that:
- Has the label `needs-rework`
- Is NOT in a merged/done state (failed or skipped tickets are not reviewed)

## Step 3 — Per-Ticket Review

For each merged ticket, evaluate the following. \
Every finding MUST include a `file:line` reference from the actual diff — \
vague findings with no file reference are not allowed.

### 3a. Acceptance Criteria Coverage

Every AC checkbox in the ticket body should have corresponding code changes \
in the diff. Flag any AC item that appears unimplemented or only partially \
implemented.

### 3b. Scope Creep

Files changed in the diff that go beyond what the ticket asked for. \
Flag files that have no plausible connection to the ticket's stated goal.

### 3c. Out-of-Scope Violations

If the ticket has an "Out of Scope" section listing things NOT to do, \
check whether the diff does any of those things anyway.

### 3d. Code Quality Smells

Check for:
- Hardcoded secrets or credentials
- Raw SQL string concatenation (SQL injection risk)
- Bare `except:` with no exception type
- Missing input validation on new API endpoints
- Missing error handling on external calls (HTTP, subprocess, file I/O)
- Dead code (unreachable branches, unused imports, commented-out blocks)
- Hardcoded values that belong in config
- New unjustified dependencies added to requirements.txt or package.json

### 3e. Spec-vs-Code Drift

Does the code actually implement what the AC says, beyond what the tester \
already verified? Look for subtle divergences: wrong defaults, inverted \
conditions, missing edge-case handling the AC implied.

## Step 4 — Sprint-Level Review

After reviewing all individual tickets, check across the full diff for:

- **Migration order**: if multiple Alembic migrations are present, are their \
  down_revision chains chronologically valid?
- **Schema conflicts**: column names referenced consistently across tickets \
  (no ticket renames a column another ticket still uses under the old name)?
- **Endpoint consistency**: new routes follow the naming conventions of \
  existing routes?
- **Dependency additions**: every new entry in requirements.txt / package.json \
  is justified by at least one ticket?
- **Cross-file quiet refactoring**: large code movements or renames that no \
  individual ticket required?

## Step 5 — Classify Each Finding

Assign one of three severity labels:

| Label | Meaning |
|-------|---------|
| **BLOCKER** | Real harm if shipped — data loss, security hole, broken contract, \
crash on the happy path. Do NOT create a follow-up ticket for blockers. |
| **SUGGESTION** | Worth fixing but not blocking — better error handling, \
cleaner abstraction, missing test coverage for an edge case. |
| **NIT** | Minor polish — naming, formatting, a missing docstring. |

Be conservative with BLOCKER. When in doubt, downgrade to SUGGESTION.

## Step 6 — Post ONE Comment on Issue #{summary_issue_num}

Use this exact structure. Write the body to a temp file, then post it with:

```
gh issue comment {summary_issue_num} --body-file /tmp/reviewer-report.md
```

If a reviewer comment from a previous run already exists on this issue, \
EDIT that comment instead of posting a new one (use `gh api` PATCH). \
Do NOT post more than one reviewer comment on the sprint summary issue.

### Comment Structure

```
## Code Reviewer Report — Sprint {sprint_label}

**Diff range:** {base_sha}..{head_sha}
**Tickets reviewed:** N  |  **Findings:** B blockers · S suggestions · N nits

---

### Per-Ticket Review

#### Ticket #N — <title>

**AC coverage:** [all covered | AC item "..." not found in diff]
**Scope creep:** [none | file:line — reason]
**Out-of-scope violations:** [none | description]

BLOCKER · `file.py:42` — Raw SQL concatenation: `query = "SELECT * FROM users WHERE id=" + user_id`
SUGGESTION · `file.py:88` — Missing error handling on `requests.get(url)` call; wrap in try/except and return HTTP 502 on failure.
NIT · `file.py:15` — Unused import `os`.

---

### Sprint-Level Findings

[findings here, or "None identified."]

---

### Follow-up Tickets Opened

[List of #N — title, or "None."]

---

**Recommendation:** Ready for human UAT  ← (or "Blockers present — resolve before UAT")

_Generated by reviewer on <ISO timestamp>_
```

## Step 7 — Create Follow-up Tickets for SUGGESTION and NIT

For every SUGGESTION and NIT finding, create one follow-up ticket:

```
gh issue create \
  --title "[follow-up] <short description>" \
  --body "..." \
  --label "enhancement,follow-up,code-review,<area>"
```

Where `<area>` is `backend`, `frontend`, or `database` — inferred from the \
file path of the finding.

**Body format:**

```
Context: sprint {sprint_label} review of #<original-ticket-num> — \
see #{summary_issue_num} for full report.

**Original ticket:** #<N>
**Severity:** SUGGESTION | NIT
**File:** `file.py:42`
**Issue:** <description of the problem>
**Suggested fix:** <concrete suggestion, or "N/A">
```

**Label rules:**
- Always apply: `enhancement`, `follow-up`, `code-review`
- Infer area label from file path: `backend` for .py server files, \
  `frontend` for .html/.js/.css, `database` for migrations/models
- DO NOT apply any `sprint-N` label
- DO NOT create follow-up tickets for BLOCKER findings

## Step 8 — Output ONE JSON Line to stdout

After posting the comment and creating tickets, output exactly one JSON line:

```
{{"comment_url": "https://github.com/...", "blockers": N, "suggestions": N, "nits": N, "follow_up_tickets": [123, 124]}}
```

Then exit cleanly.

## Concrete Examples of Good vs Bad Findings

### BLOCKER — Good finding

> BLOCKER · `apps/dashboard/main.py:234` — `user_id` from query string is \
> concatenated directly into SQL: `query = "SELECT * FROM orders WHERE user=" + user_id`. \
> Classic SQL injection; any unauthenticated caller can dump the database.

### BLOCKER — Bad finding (vague, no file reference)

> BLOCKER — The code might have SQL injection somewhere.

### SUGGESTION — Good finding

> SUGGESTION · `services/sprint_manager/sprint_manager.py:1802` — \
> `subprocess.run(cmd)` has no timeout. If the subprocess hangs, \
> the sprint loop blocks indefinitely. Add `timeout=300`.

### SUGGESTION — Bad finding (not tied to diff)

> SUGGESTION — The codebase should use async everywhere for better performance.

### NIT — Good finding

> NIT · `apps/dashboard/static/index.html:88` — `var` used instead of `const` \
> for `let result = fetch(...)`. Modern JS prefers `const` or `let`.

### NIT — Bad finding (invented requirement)

> NIT — All functions should have docstrings. (This was not in the AC.)

## Prohibited Actions (Do NOT Do These)

- Modify any source file
- Run `git commit`, `git push`, or any branch operation
- Apply labels to the merged sprint tickets (only to new follow-up tickets)
- Close any issue
- Run the application, run tests, or invoke any server
- Post more than one comment on the sprint summary issue
- Review tickets that failed, were skipped, or were not merged
- Invent requirements not present in the AC or issue body
- Include file:line references that do not appear in `git diff --name-only {base_sha}..{head_sha}`
"""


# ── Documenter agent (issue #165) ─────────────────────────────────────────────

DEFAULT_DOCUMENTER_PROMPT = """\
You are the **Documenter** agent for sprint {sprint_label}.

## Context

- Sprint branch: {sprint_branch}
- Diff range: {base_sha}..{head_sha}
- Sprint tickets: {sprint_filter_url}
- Sprint summary issue: #{summary_issue_num}

## Your Mandate

Read the sprint diff and update project documentation to reflect what shipped
this sprint. Commit any doc changes directly to the sprint branch.

Documentation files to consider updating (only if relevant changes shipped):
- README.md — new features, changed commands, updated usage examples
- CHANGELOG.md — one-line entry per merged ticket (format: "- #N: <title>")
- SCHEMA.md — any new or changed DB tables/columns/endpoints

## Step 1 — Review the diff

Run:
```
git diff {base_sha}..{head_sha} --stat
git diff {base_sha}..{head_sha}
```

## Step 2 — Identify what shipped

For each ticket in the sprint, read:
```
gh issue view <N> --json number,title,body,labels
```

Only document tickets that are in a merged/done state.

## Step 3 — Update docs

For each doc file that needs updating:
1. Read the current file
2. Make targeted, accurate additions
3. Do not remove existing content unless it is factually incorrect after this sprint

## Step 4 — Commit

If you made any doc changes, stage and commit them:
```
git add <doc files changed>
git commit -m "docs: auto-update from sprint-{sprint_label} diff"
```

Then print a line in this exact format so sprint_manager can parse it:
```
Documenter complete: <comma-separated list of files touched, or 'none'>
```

If no doc changes were needed (nothing to document), print:
```
Documenter complete: none
```

## Prohibited Actions

- Do NOT modify source code (.py, .js, .html, .sql, etc.)
- Do NOT create new tickets or close existing ones
- Do NOT push to remote — sprint_manager handles git push
- Do NOT modify files outside the project root
"""


def _dispatch_documenter(
    state: "SprintState",
    sprint_branch: str,
    base_sha: str,
    head_sha: str,
    cfg: Optional["SprintConfig"],
    repo_name: Optional[str],
    timeout_secs: int = 300,
) -> None:
    """Dispatch the documenter agent after write_sprint_summary() and before _create_sprint_pr().

    Updates state.documenter_status, state.documenter_files_touched, and
    state.documenter_commit_sha in-place. Raises RuntimeError on failure so the
    sprint pipeline fails loudly (AC6).
    """
    # Skip: nothing merged this sprint
    merged = [i for i in state.issues if i.status == "done"]
    if not merged:
        print("  [documenter] skipped: nothing merged this sprint")
        state.documenter_status = "skipped"
        return

    # Determine prompt
    if cfg and cfg.documenter_prompt_template:
        prompt_template = cfg.documenter_prompt_template
    else:
        prompt_template = DEFAULT_DOCUMENTER_PROMPT

    eff_repo = repo_name or (cfg.repo_name if cfg else None)

    sprint_filter_url = (
        f"https://github.com/{eff_repo}/issues"
        f"?q=label%3A{state.sprint_label}"
        if eff_repo else ""
    )

    # Read summary_issue_num from state JSON
    summary_issue_num: Optional[int] = None
    # Resolve state path to pick up summary_issue_url
    state_path_doc = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
    if state_path_doc.exists():
        try:
            sd = json.loads(state_path_doc.read_text())
            surl = sd.get("summary_issue_url", "")
            m_issue = re.search(r"/issues/(\d+)$", surl)
            if m_issue:
                summary_issue_num = int(m_issue.group(1))
        except Exception:
            pass

    try:
        prompt = prompt_template.format(
            sprint_label      = state.sprint_label,
            sprint_branch     = sprint_branch,
            base_sha          = base_sha,
            head_sha          = head_sha,
            sprint_filter_url = sprint_filter_url,
            summary_issue_num = summary_issue_num or 0,
        )
    except KeyError as e:
        structured_log.error("documenter_template_error", f"[documenter] prompt template has unknown placeholder {e}", placeholder=str(e))
        state.documenter_status = "failed"
        raise RuntimeError(
            f"[documenter] prompt template has unknown placeholder {e}; "
            "check documenter_prompt_template in sprint.yaml or DEFAULT_DOCUMENTER_PROMPT"
        ) from e

    cwd_path = cfg.worktree_tester if cfg else Path.cwd()
    logs_dir = cfg.logs_dir if cfg else Path.cwd()
    log_path = logs_dir / f"sprint-{state.sprint_label}-documenter.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure tester clone has the sprint branch up to date before committing docs
    print(f"  [documenter] Checking out {sprint_branch} in tester clone ...", flush=True)
    try:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "checkout", sprint_branch],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "pull"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
    except Exception as e:
        structured_log.warn("documenter_git_prep_failed", f"[documenter] git prep failed: {e}", exc=str(e))

    documentor_model = cfg.documentor_model if cfg is not None else "claude-sonnet-4-6"
    cmd = [
        "claude",
        "--model", documentor_model,
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    sub_env["COMMANDER_MERGE_TARGET"] = sprint_branch
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo

    print("  [documenter] Dispatching documenter ...", flush=True)
    try:
        with log_path.open("w") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=sub_env,
            )
    except FileNotFoundError:
        state.documenter_status = "failed"
        raise RuntimeError("[documenter] claude CLI not found — cannot run documenter (AC6)")

    # 5-minute wall-clock cap (AC7) via max_total_secs
    detector = HangDetector(issue_num=0, log_path=log_path, proc=proc, max_total_secs=timeout_secs)
    detector.start()
    rc = proc.wait()
    detector.stop()

    if detector.killed:
        state.documenter_status = "failed"
        raise RuntimeError(
            f"[documenter] documenter exceeded {timeout_secs}s wall-clock limit and was killed"
            f" — check {log_path} for details (AC6, AC7)"
        )

    if rc != 0:
        state.documenter_status = "failed"
        raise RuntimeError(
            f"[documenter] documenter exited with code {rc}"
            f" — check {log_path} for details (AC6)"
        )

    # Parse exit line: "Documenter complete: <files or 'none'>"
    files_touched: list[str] = []
    try:
        log_text = log_path.read_text(errors="replace")
        for line in reversed(log_text.splitlines()):
            m = re.search(r"Documenter complete:\s*(.+)", line, re.IGNORECASE)
            if m:
                raw_files = m.group(1).strip()
                if raw_files.lower() != "none":
                    files_touched = [f.strip() for f in raw_files.split(",") if f.strip()]
                break
    except Exception as e:
        structured_log.warn("documenter_log_parse_error", f"[documenter] could not parse documenter log: {e}", exc=str(e))

    # Record commit SHA if a doc commit was made
    doc_commit_sha: Optional[str] = None
    if files_touched:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(cwd_path), capture_output=True, text=True, check=False,
            )
            if r.returncode == 0:
                doc_commit_sha = r.stdout.strip()
        except Exception:
            pass

        # Push the doc commit to remote so it's included in the sprint PR (AC3)
        print(f"  [documenter] Pushing doc commit to {sprint_branch} ...", flush=True)
        push_r = subprocess.run(
            ["git", "push", "origin", sprint_branch],
            cwd=str(cwd_path), capture_output=True, text=True, check=False,
        )
        if push_r.returncode != 0:
            state.documenter_status = "failed"
            raise RuntimeError(
                f"[documenter] git push failed (rc={push_r.returncode}): {push_r.stderr.strip()} (AC3, AC6)"
            )

    state.documenter_status        = "succeeded"
    state.documenter_files_touched = files_touched
    state.documenter_commit_sha    = doc_commit_sha
    print(
        f"  [documenter] Done: {len(files_touched)} file(s) touched"
        + (f" (commit {doc_commit_sha[:8]})" if doc_commit_sha else ""),
        flush=True,
    )


def _dispatch_reviewer(
    state: "SprintState",
    summary_issue_num: Optional[int],
    sprint_branch: str,
    base_sha: str,
    head_sha: str,
    cfg: Optional["SprintConfig"],
    repo_name: Optional[str],
) -> None:
    """Dispatch the reviewer agent after sprint PR creation.

    Updates state.reviewer_status, state.reviewer_comment_url, and
    state.reviewer_findings in-place.

    Raises RuntimeError when the prompt template contains an unknown placeholder
    (AC-3: unknown placeholders must surface as a loud ERROR, not a silent skip).
    All other failures are advisory (logged and set state.reviewer_status="failed").
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)

    # Skip conditions
    merged = [i for i in state.issues if i.status == "done"]
    if not merged:
        print("  [reviewer] skipped: nothing merged this sprint")
        state.reviewer_status = "skipped"
        return

    if summary_issue_num is None:
        print("  [reviewer] skipped: no sprint summary issue available")
        state.reviewer_status = "skipped"
        return

    # Determine prompt
    if cfg and cfg.reviewer_prompt_template:
        prompt_template = cfg.reviewer_prompt_template
    else:
        prompt_template = DEFAULT_REVIEWER_PROMPT

    sprint_filter_url = (
        f"https://github.com/{eff_repo}/issues"
        f"?q=label%3A{state.sprint_label}"
        if eff_repo else ""
    )

    try:
        prompt = prompt_template.format(
            sprint_label      = state.sprint_label,
            sprint_branch     = sprint_branch,
            base_sha          = base_sha,
            head_sha          = head_sha,
            summary_issue_num = summary_issue_num,
            sprint_filter_url = sprint_filter_url,
            repo_name         = eff_repo or "",
        )
    except KeyError as e:
        structured_log.error("reviewer_template_error", f"[reviewer] prompt template has unknown placeholder {e}", placeholder=str(e))
        state.reviewer_status = "failed"
        raise RuntimeError(
            f"[reviewer] prompt template has unknown placeholder {e}; "
            "check reviewer_prompt_template in sprint.yaml or DEFAULT_REVIEWER_PROMPT"
        ) from e

    cwd_path = cfg.worktree_coder if cfg else Path.cwd()
    logs_dir = cfg.logs_dir if cfg else Path.cwd()
    log_path = logs_dir / f"sprint-{state.sprint_label}-reviewer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure coder clone has the sprint branch up to date
    print(f"  [reviewer] Fetching {sprint_branch} in coder clone ...", flush=True)
    try:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "checkout", sprint_branch],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "pull"],
            cwd=str(cwd_path), capture_output=True, check=False,
        )
    except Exception as e:
        structured_log.warn("reviewer_git_prep_failed", f"[reviewer] git prep failed: {e}", exc=str(e))

    reviewer_model = cfg.reviewer_model if cfg is not None else "claude-haiku-4-5"
    cmd = [
        "claude",
        "--model", reviewer_model,
        "--dangerously-skip-permissions",
        "-p", prompt,
    ]

    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    sub_env.update(_agent_identity_env("reviewer", summary_issue_num))  # issue #719
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo
    sub_env["REPO"]                  = eff_repo or ""
    sub_env["SPRINT_LABEL"]          = state.sprint_label
    sub_env["SPRINT_SUMMARY_ISSUE"]  = str(summary_issue_num)
    sub_env["SPRINT_BRANCH"]         = sprint_branch
    sub_env["BASE_REF"]              = base_sha
    sub_env["HEAD_REF"]              = head_sha

    print("  [reviewer] Dispatching reviewer ...", flush=True)
    try:
        with log_path.open("w") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=sub_env,
            )
    except FileNotFoundError:
        structured_log.warn("claude_cli_not_found", "[reviewer] claude CLI not found — skipping", subprocess="reviewer")
        state.reviewer_status = "skipped"
        return

    # Use HangDetector with issue_num=0 as a sentinel for the reviewer
    detector = HangDetector(issue_num=0, log_path=log_path, proc=proc)
    detector.start()
    rc = proc.wait()
    detector.stop()

    if detector.killed:
        structured_log.warn("subprocess_killed", "[reviewer] reviewer hung and was killed", subprocess="reviewer")
        state.reviewer_status = "failed"
        record_failure(
            0,
            "reviewer",
            detail=_build_crash_detail(log_path, signal="SIGKILL"),
            summary="Sprint reviewer hung and was killed",
        )
        return

    if rc != 0:
        structured_log.error("subprocess_nonzero_exit", f"[reviewer] reviewer exited with code {rc}", subprocess="reviewer", exit_code=rc)
        state.reviewer_status = "failed"
        record_failure(
            0,
            "reviewer",
            detail=_build_crash_detail(log_path, exit_code=rc),
            summary=f"Sprint reviewer exited with code {rc}",
        )
        return

    # Parse exit line: "Reviewer complete: B blockers, S suggestions, I nits, F follow-up tickets opened"
    findings: dict = {"blockers": 0, "suggestions": 0, "nits": 0, "follow_up_tickets": []}
    comment_url: Optional[str] = None
    try:
        log_text = log_path.read_text(errors="replace")
        for line in reversed(log_text.splitlines()):
            m = re.search(
                r"Reviewer complete:\s*(\d+)\s*blockers?,\s*(\d+)\s*suggestions?,\s*(\d+)\s*nits?,\s*(\d+)\s*follow-up",
                line, re.IGNORECASE,
            )
            if m:
                findings["blockers"]    = int(m.group(1))
                findings["suggestions"] = int(m.group(2))
                findings["nits"]        = int(m.group(3))
                break
        # Parse actual follow-up issue numbers from issue creation URLs in the log.
        # gh issue create outputs: https://github.com/<repo>/issues/N
        # Exclude comment URLs (which contain #issuecomment-) and the sprint summary issue.
        if eff_repo:
            issue_url_pat = re.compile(
                rf"https://github\.com/{re.escape(eff_repo)}/issues/(\d+)",
                re.IGNORECASE,
            )
            seen_nums: set = set()
            follow_up_nums: list = []
            for url_m2 in issue_url_pat.finditer(log_text):
                # Skip comment URLs: the match end is immediately followed by '#'
                pos = url_m2.end()
                if pos < len(log_text) and log_text[pos] == "#":
                    continue
                num = int(url_m2.group(1))
                if summary_issue_num is not None and num == summary_issue_num:
                    continue
                if num not in seen_nums:
                    seen_nums.add(num)
                    follow_up_nums.append(num)
            findings["follow_up_tickets"] = follow_up_nums
        # Look for comment URL in log
        url_cm = re.search(r"https://github\.com/[^\s]+/issues/\d+#issuecomment-\d+", log_text)
        if url_cm:
            comment_url = url_cm.group(0)
    except Exception as e:
        structured_log.warn("reviewer_log_parse_error", f"[reviewer] could not parse reviewer log: {e}", exc=str(e))

    state.reviewer_status      = "succeeded"
    state.reviewer_comment_url = comment_url
    state.reviewer_findings    = findings
    print(
        f"  [reviewer] Done: "
        f"{findings['blockers']} blockers, "
        f"{findings['suggestions']} suggestions, "
        f"{findings['nits']} nits, "
        f"{len(findings['follow_up_tickets'])} follow-up tickets",
        flush=True,
    )


# ── Follow-up ticket enrichment (BA + estimator) ─────────────────────────────

_ESTIMATE_ISSUE_SCRIPT_SM = REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py"

_BA_REWRITE_PROMPT = """\
You are a Business Analyst. Issue #{issue_num} in repository {repo} was created by an \
automated code reviewer as a follow-up ticket. Its body may be minimal or unstructured.

Your task:
1. Read the issue with: gh issue view {issue_num} --repo {repo} --json title,body
2. Rewrite the body to the standard Commander format with ALL of these sections:
   ## What & Why
   ## Acceptance Criteria
   (at least 2 checkbox items: - [ ] ...)
   ## UAT Test Steps
   ## Out of Scope
3. Update the issue body on GitHub: gh issue edit {issue_num} --repo {repo} --body-file <tmpfile>
   (write the new body to a temp file first, then pass via --body-file)

Do not change the title, labels, milestone, or any field other than the body.
Do not ask clarifying questions. Produce the structured body directly from the existing content.
"""

_BA_DISPATCH_TIMEOUT = int(os.environ.get("COMMANDER_BA_REWRITE_TIMEOUT", "300"))
_ESTIMATOR_DISPATCH_TIMEOUT = int(os.environ.get("COMMANDER_ESTIMATOR_TIMEOUT", "300"))


def _dispatch_ba_for_followup(
    issue_num: int,
    eff_repo: str,
    cfg: Optional[object],
    state: Optional[object],
) -> bool:
    """Invoke the BA agent to rewrite a follow-up ticket body to standard format.

    Returns True on success, False on failure.  Failures are printed but not raised
    so callers can continue processing other tickets.
    """
    cwd_path = cfg.worktree_coder if cfg else Path.cwd()
    logs_dir = cfg.logs_dir if cfg else Path.cwd()
    log_path = logs_dir / f"ba-rewrite-{issue_num}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    prompt = _BA_REWRITE_PROMPT.format(issue_num=issue_num, repo=eff_repo)

    cmd = [
        "claude",
        "--model", "claude-sonnet-4-6",
        "--dangerously-skip-permissions",
    ]
    ba_persona = _load_agent_persona("ba", cwd_path)
    if ba_persona:
        cmd += ["--append-system-prompt", ba_persona]
    cmd += ["-p", prompt]

    sub_env = os.environ.copy()
    sub_env.pop("ANTHROPIC_API_KEY", None)
    sub_env["CLAUDE_AGENT_ROLE"] = "ba"
    if eff_repo:
        sub_env["COMMANDER_PROJECT"] = eff_repo

    print(f"  [ba-rewrite] Rewriting body for follow-up #{issue_num} ...", flush=True)
    try:
        with log_path.open("w") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=sub_env,
            )
    except FileNotFoundError:
        print(f"  [ba-rewrite] WARNING: claude CLI not found — skipping BA rewrite for #{issue_num}", flush=True)
        return False

    try:
        rc = proc.wait(timeout=_BA_DISPATCH_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        print(f"  [ba-rewrite] WARNING: BA rewrite for #{issue_num} timed out after {_BA_DISPATCH_TIMEOUT}s", flush=True)
        return False

    if rc != 0:
        print(f"  [ba-rewrite] WARNING: BA rewrite for #{issue_num} exited with code {rc}", flush=True)
        return False

    print(f"  [ba-rewrite] Done for #{issue_num}", flush=True)
    return True


def _dispatch_estimator_for_followup(
    issue_num: int,
    eff_repo: str,
    cfg: Optional[object],
) -> bool:
    """Run estimate_issue.py on a follow-up ticket to apply size label and write estimate file.

    Returns True on success, False on failure.
    """
    if not _ESTIMATE_ISSUE_SCRIPT_SM.exists():
        print(f"  [estimator] WARNING: estimate_issue.py not found — skipping #{issue_num}", flush=True)
        return False

    cmd = [
        sys.executable,
        str(_ESTIMATE_ISSUE_SCRIPT_SM),
        "--issue", str(issue_num),
        "--repo", eff_repo,
        "--save-comment",
        "--save-label",
    ]

    print(f"  [estimator] Estimating follow-up #{issue_num} ...", flush=True)
    est_env = os.environ.copy()
    est_env.update(_agent_identity_env("estimator", issue_num))  # issue #719
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_ESTIMATOR_DISPATCH_TIMEOUT,
            env=est_env,
        )
    except subprocess.TimeoutExpired:
        print(f"  [estimator] WARNING: estimation for #{issue_num} timed out after {_ESTIMATOR_DISPATCH_TIMEOUT}s", flush=True)
        return False

    if result.returncode != 0:
        print(f"  [estimator] WARNING: estimation for #{issue_num} exited with code {result.returncode}", flush=True)
        return False

    print(f"  [estimator] Done for #{issue_num}", flush=True)
    return True


def _enrich_followup_tickets(
    follow_up_tickets: list,
    eff_repo: str,
    cfg: Optional[object],
    state: Optional[object],
) -> None:
    """Run BA agent + estimator on each reviewer follow-up ticket.

    Processes tickets sequentially: BA then estimator per ticket.
    A failure on one ticket is logged and does not abort the remaining tickets.
    """
    if not follow_up_tickets:
        return

    print(
        f"  [enrichment] Enriching {len(follow_up_tickets)} follow-up ticket(s): "
        + ", ".join(f"#{n}" for n in follow_up_tickets),
        flush=True,
    )

    for issue_num in follow_up_tickets:
        try:
            _dispatch_ba_for_followup(issue_num, eff_repo, cfg, state)
        except Exception as exc:
            print(
                f"  [enrichment] WARNING: BA rewrite failed for #{issue_num}: {exc}",
                flush=True,
            )

        try:
            _dispatch_estimator_for_followup(issue_num, eff_repo, cfg)
        except Exception as exc:
            print(
                f"  [enrichment] WARNING: estimator failed for #{issue_num}: {exc}",
                flush=True,
            )

    print(f"  [enrichment] Done enriching {len(follow_up_tickets)} follow-up ticket(s).", flush=True)


# ── Issue Estimator integration ───────────────────────────────────────────────

SERIOUS_RISK_FLAGS = {"touches-db-schema", "security-sensitive", "breaks-tests"}


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


def _build_sprint_dag_layers(issues: list["IssueState"]) -> Optional[list[list[int]]]:
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
    issues: list["IssueState"],
    plan_order: Optional[list[int]],
    dag_layers: Optional[list[list[int]]],
) -> list[list["IssueState"]]:
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
        levels: list[list["IssueState"]] = []
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


def _warn_file_conflicts(issues: list["IssueState"]) -> None:
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


def list_backlog_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return open issues with the given label, in backlog, sorted by number."""
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
        # Only return issues in backlog state; skip sprint-summary documentation tickets
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
            if _classify(labels_set) == "backlog":
                result.append(issue)
        return sorted(result, key=lambda i: i["number"])
    except Exception as e:
        structured_log.warn("list_issues_failed", f"could not list issues: {e}", label=label, exc=str(e))
        return []


# ── Neon dual-write helpers ────────────────────────────────────────────────────

def _neon_update(fn_name: str, *args, **kwargs) -> None:
    """Call a sprint_repo function; print loudly on failure but don't abort sprint."""
    if not _SPRINT_REPO_AVAILABLE or _sprint_repo is None:
        return
    try:
        getattr(_sprint_repo, fn_name)(*args, **kwargs)
    except Exception as _e:
        structured_log.error("neon_update_failed", f"[neon] {fn_name} failed: {_e}", neon_fn=fn_name, exc=str(_e))


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
    """Create sprint + tickets in Neon and write the JSON mirror. Called once on fresh start."""
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

    if not _SPRINT_REPO_AVAILABLE or _sprint_repo is None:
        return

    # Create (or reuse) sprint row in Neon — this must succeed or we abort.
    try:
        _sprint_repo.get_or_create_sprint(label=label, goal=goal, project=project)
        _sprint_repo.update_sprint_status(label, "running")
    except Exception as _e:
        structured_log.error("neon_sprint_init_failed", f"[neon] sprint init failed for {label!r}: {_e}", sprint_label=label, exc=str(_e))
        return

    # Add all tickets (idempotent — skip if already present).
    for position, issue_state in enumerate(issues):
        try:
            _sprint_repo.add_ticket(sprint_label=label, issue_number=issue_state.number, position=position)
        except Exception as _e:
            if "UNIQUE" in str(_e).upper() or "unique" in str(_e).lower():
                pass  # Already added (resume path)
            else:
                structured_log.warn("neon_add_ticket_failed", f"[neon] add_ticket #{issue_state.number} failed: {_e}", issue_num=issue_state.number, exc=str(_e))

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
    """Update a ticket's Neon status + patch the sprint JSON mirror."""
    _neon_update("update_ticket_status", sprint_label, issue_number, neon_status, total_tokens)

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
    """Update sprint Neon status + patch the sprint JSON mirror."""
    _neon_update("update_sprint_status", sprint_label, neon_status)

    json_path = _neon_sprint_json_path(sprint_label, sprints_dir)
    data = _neon_sprint_json_read(json_path)
    if data:
        data["status"] = neon_status
        _neon_sprint_json_write(json_path, data)


# ── sprint loop ────────────────────────────────────────────────────────────────

def run_sprint(
    label: str,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    gate_typecheck: bool = True,
    gate_design: bool = True,
    gate_frontend_lint: bool = True,
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
) -> tuple[SprintSummary, SprintState]:
    """Main sprint loop -- processes backlog issues sequentially.

    Returns (SprintSummary, SprintState).
    Supports resume/retry_failed from persisted state.

    target_branch: branch to merge feature branches into. Defaults to
    'develop'. Pass a sprint branch name to enable sprint-branch merge mode.

    preflight_approved: optional list of issue numbers approved by the pre-flight
    review. When provided, only issues in this list are dispatched; others are
    skipped with reason 'preflight-skipped'.

    gate_scope: 'changed' (default) scopes pytest/lint gates to files changed
    relative to the base branch; 'full' restores legacy full-codebase behaviour.
    """
    if alert_modes is None:
        alert_modes = [AlertMode.DASHBOARD_BANNER]

    # Effective repo: explicit arg > config > github_client
    eff_repo   = repo_name or (cfg.repo_name if cfg else None)
    api_url    = cfg.api_url if cfg else None

    summary    = SprintSummary()
    sprint_num = _sprint_number(label)
    state_path = _state_path(sprint_num, label, cfg=cfg)

    # Mint run_id before any agent work; propagates to subprocesses via COMMANDER_RUN_ID
    _sprint_num_str = str(sprint_num) if sprint_num is not None else label.replace("sprint-", "")
    _run_id = mint_run_id("sprint", _sprint_num_str)
    os.environ["COMMANDER_RUN_ID"] = _run_id
    structured_log.set_context(run_id=_run_id, source="sprint", sprint_label=label)

    # AC-2: write PID file and register cleanup handlers
    if not dry_run:
        _setup_pid_file(sprint_num)

    # Log config info when running against a second repo
    if cfg and cfg.repo_name:
        print(f"\n=== SprintConfig ===")
        print(f"  repo:         {cfg.repo_name}")
        print(f"  coder-wt:     {cfg.worktree_coder}")
        print(f"  tester-wt:    {cfg.worktree_tester}")
        print(f"  tester-app:   {cfg.worktree_tester_app}")
        print(f"  logs-dir:     {cfg.logs_dir}")
        print(f"  sprints-dir:  {cfg.sprints_dir}")
        print(f"  api-url:      {cfg.api_url}")

    # Determine the sprint branch name and effective merge target.
    # When target_branch is not explicitly passed, default to sprint/<label>
    # so per-ticket feature branches merge into the sprint branch, not develop.
    # Passing --target-branch develop explicitly is still supported as a
    # deliberate override (AC-5 of issue #269).
    sprint_branch = f"sprint/{label}"
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
        print(f"  Loading existing sprint state from {state_path}")
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
            print("No issues to dispatch for this re-run (all skipped).")
            state = SprintState(
                sprint_label    = label,
                sprint_number   = sprint_num,
                project         = eff_repo or "",
                start_timestamp = _utcnow(),
            )
            return summary, state

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
            print("No backlog issues found for this label.")
            state = SprintState(
                sprint_label  = label,
                sprint_number = sprint_num,
                project       = eff_repo or "",
                start_timestamp = _utcnow(),
            )
            return summary, state

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

    print(f"\n=== Sprint Manager: label={label} ===")
    print(f"Target branch: {target_branch}")
    print(f"Found {len(state.issues)} issue(s): {[i.number for i in state.issues]}")
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

    # AC-1: Create sprint branch off develop (idempotent)
    if target_branch == sprint_branch:
        if dry_run:
            print(f"  [dry-run] would create sprint branch {sprint_branch!r} off develop")
        else:
            _create_sprint_branch(sprint_branch)
    else:
        print(f"  Using custom target branch {target_branch!r} — sprint branch creation skipped.")

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
        print("\n  [estimator] Running sprint estimator ...")
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
            print(
                f"  [estimator] Estimator succeeded: "
                f"{len(est_result.estimates)} tickets, "
                f"{est_result.total_minutes} total minutes"
            )
        except ImportError:
            structured_log.warn("estimator_module_missing", "[estimator] sprint_estimator module not found — skipping")
            state.estimator_status = "failed"
            state.save(state_path)
        except Exception as e:
            structured_log.warn("estimator_sprint_failed", f"[estimator] estimator failed: {e}", exc=str(e))
            state.estimator_status = "failed"
            state.save(state_path)
    elif skip_estimator:
        print("  [estimator] --skip-estimator active — skipping estimation")
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

    # Tag each IssueState with its 1-based execution level (issue #613: seg bar / level-sep UI)
    for _lvl_idx, _lvl_issues in enumerate(_dispatch_levels):
        for _lvl_iss in _lvl_issues:
            _lvl_iss.dispatch_level = _lvl_idx + 1

    start_time = time.monotonic()

    total_issues = len(state.issues)
    _emit_sprint_lifecycle_event(
        type="sprint_started",
        target=f"sprint-{sprint_num}" if sprint_num is not None else label,
        actor="system",
        detail={"ticket_count": total_issues, "levels": len(_dispatch_levels)},
        project=eff_repo or label,
        action_id=_run_id,
    )
    # Flat iteration preserving level boundaries for level_start / level_complete events.
    _flat_dispatch: list[tuple[int, IssueState]] = [
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
            print(f"\n=== Dispatch level {_cur_level_idx}: tickets {_level_nums_by_idx[_cur_level_idx]} ===")
        idx = _flat_pos + 1
        num   = issue_state.number
        title = issue_state.title
        progress = f"[{idx}/{total_issues}]"

        # Resume: skip already-done/skipped
        if resume and issue_state.status in ("done", "skipped"):
            print(f"\n--- {progress} Issue #{num}: {title} --- [SKIP: already {issue_state.status}]")
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
            print(f"\n--- {progress} Issue #{num}: {title} --- [SKIP: already done]")
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

        print(f"\n--- {progress} Issue #{num}: {title} ---")
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
            print(f"  [estimate] size={_size} ({_time_str}), confidence={_conf}")
            _risk = _est.get("risk_flags", [])
            if _risk:
                print(f"  [estimate] risk flags: {', '.join(_risk)}")
                _serious = [f for f in _risk if f in SERIOUS_RISK_FLAGS]
                if _serious:
                    structured_log.warn("estimate_serious_risk", f"serious risk flags: {', '.join(_serious)}", issue_num=num, risk_flags=_serious)

        # Preflight filter: skip issues not approved by pre-flight review
        if preflight_approved is not None and num not in preflight_approved:
            print("  [preflight] skipped by pre-flight review")
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
            _neon_ticket_status(label, num, "skipped", _eff_sprints_dir)
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url, project=eff_repo)
            continue

        if dry_run:
            print("  [dry-run] would dispatch coder + tester")
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

        for _fix_attempt in range(_fix_rounds):

            if _skip_coder:
                print(f"  [rerun] SIT ticket: dispatching tester directly for #{num}")
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
                try:
                    coder_ok, coder_category = _dispatch_coder(
                        num, alert_modes, sprint_branch=target_branch, repo_name=eff_repo, cfg=cfg,
                        chosen_port=chosen_port, rate_limit_events=state.rate_limit_events,
                        on_running=_on_coder_running, sprint_label=label,
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
                _coder_elapsed = time.monotonic() - _coder_t0
                _coder_m, _coder_s = divmod(int(_coder_elapsed), 60)
                print(f"  Total time used on coder dispatch: {_coder_m}m {_coder_s}s")
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
                            print(
                                f"  [fix-loop] consecutive identical coder failure ({_sig}): "
                                f"aborting early", flush=True,
                            )
                            _loop_aborted = True
                            break
                        _last_failure_sig = _sig
                        print(
                            f"  [fix-loop] coder logic failure ({_sig}), "
                            f"attempt {_fix_attempt + 1}/{_fix_rounds}: will retry",
                            flush=True,
                        )
                        continue

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
                    _neon_ticket_status(label, num, "failed", _eff_sprints_dir)
                    state.save(state_path)
                    _post_sprint_status(state, api_url=api_url, project=eff_repo)
                    _infra_exit = True
                    break

                # -- Detect CODER_NO_WORK: coder exited 0 but created no feature branch --
                if _find_feature_branch(num) is None:
                    category = FailureCategory.CODER_NO_WORK
                    reason   = f"Coder exited 0 but no feature/{num}-* branch was created"
                    print(f"  {reason}")
                    # Logic failure: record, check dup, retry
                    record_failure(num, category, detail=reason)
                    _sig = category
                    _fix_history.append({
                        "attempt": _fix_attempt, "category": category, "reason": reason,
                    })
                    if _sig == _last_failure_sig:
                        print(
                            f"  [fix-loop] consecutive identical CODER_NO_WORK: aborting early",
                            flush=True,
                        )
                        _loop_aborted = True
                        break
                    _last_failure_sig = _sig
                    print(
                        f"  [fix-loop] CODER_NO_WORK, "
                        f"attempt {_fix_attempt + 1}/{_fix_rounds}: will retry",
                        flush=True,
                    )
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
            try:
                tester_rc, hang_category = _dispatch_tester(
                    num, alert_modes, sprint_branch=target_branch, repo_name=eff_repo, cfg=cfg,
                    chosen_port=chosen_port, rate_limit_events=state.rate_limit_events,
                    on_running=_on_tester_running, sprint_label=label,
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
            _tester_m, _tester_s = divmod(int(_tester_elapsed), 60)
            print(f"  Total time used on tester dispatch: {_tester_m}m {_tester_s}s")
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
                target_branch      = target_branch,
                repo_name          = eff_repo,
                cfg                = cfg,
                base_branch        = target_branch or "develop",
                gate_scope         = gate_scope,
                documentor_enabled = cfg.documentor_enabled if cfg else False,
                alert_modes        = alert_modes,
                sprint_label       = label,
            )
            print(f"  {summary_line}")
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
                    print(
                        f"  [fix-loop] consecutive identical gate failure ({category}): "
                        f"aborting early", flush=True,
                    )
                    _loop_aborted = True
                    break
                _last_failure_sig = _sig
                print(
                    f"  [fix-loop] gate logic failure ({category}), "
                    f"attempt {_fix_attempt + 1}/{_fix_rounds}: will retry coder",
                    flush=True,
                )
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
            print(f"  [fix-loop] {reason} — tagging needs-rework", flush=True)
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
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{num} skipped: {category}",
                body=reason,
                issue_num=num,
                category=category,
                cfg=cfg,
                repo=eff_repo,
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

    # Estimator control (issue #166, #696) — default skips; --no-skip-estimator opts in
    p.add_argument(
        "--skip-estimator",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=argparse.SUPPRESS,
    )

    # Rerun manifest (issue #332) — written by the server rerun endpoint
    p.add_argument(
        "--rerun-manifest",
        default=None,
        metavar="PATH",
        help=argparse.SUPPRESS,
    )

    args = p.parse_args()

    # ── Config resolution (AC-4 + AC-5 + AC-6) ───────────────────────────────
    cfg: Optional[SprintConfig] = None

    if args.config:
        # Explicit --config flag (AC-4)
        config_path = Path(args.config).expanduser().resolve()
        if not config_path.exists():
            p.error(f"Config file not found: {config_path}")
        print(f"  Using config: {config_path}")
        cfg = load_config(config_path)
    else:
        # Auto-discovery: walk up from CWD (AC-5)
        discovered = discover_config()
        if discovered:
            print(f"  Auto-discovered config: {discovered}")
            cfg = load_config(discovered)
        else:
            # Backward-compatible default (AC-6)
            cfg = None

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
        # Best-effort state write before exit (issue #507)
        _plan_json_set_state_sm(
            args.label, "cancelled", cfg=cfg,
            ended_at=datetime.now(timezone.utc).isoformat(),
        )
        raise SystemExit(130)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # Write state=running now that PID lock is confirmed (issue #507)
    if not args.dry_run:
        _plan_json_set_state_sm(
            args.label, "running", cfg=cfg,
            started_at=datetime.now(timezone.utc).isoformat(),
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
        print(f"[preflight] Approved {len(preflight_approved)} issue(s) for this run.")

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
        )

        # Derive sprint_branch for summary (mirrors run_sprint logic)
        sprint_branch = f"sprint/{args.label}"
        effective_target = args.target_branch or sprint_branch

        # AC-1/2/3: write extended summary, create GitHub issue, prompt for learnings
        if state.issues:
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
            )
        else:
            summary_path = None

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

    # Clean exit: write state=completed (issue #507)
    if not args.dry_run:
        _plan_json_set_state_sm(
            args.label, "completed", cfg=cfg,
            ended_at=datetime.now(timezone.utc).isoformat(),
            end_reason="natural",
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
        try:
            _dispatch_documenter(
                state         = state,
                sprint_branch = effective_target,
                base_sha      = doc_base_sha,
                head_sha      = doc_head_sha,
                cfg           = cfg,
                repo_name     = eff_repo,
            )
        except RuntimeError as e_doc:
            # AC6: documenter errors must fail the pipeline loudly, not silently skip
            structured_log.error("documenter_failed", f"documenter failed: {e_doc}", exc=str(e_doc))
            print(f"\n[ERROR] Documenter failed: {e_doc}", flush=True)
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

    # AC6, AC7: auto-create PR sprint/sprint-N → develop at sprint end,
    # but only when we were running in sprint-branch mode (not manual 'develop' override)
    sprint_pr_url: Optional[str] = None
    if (
        state.issues
        and not args.dry_run
        and effective_target != "develop"
        and effective_target == sprint_branch  # only for auto sprint branches, not custom --target-branch
    ):
        sprint_pr_url = _create_sprint_pr(
            sprint_branch  = effective_target,
            sprint_label   = args.label,
            sprint_number  = _sprint_number(args.label),
            state          = state,
            repo_name      = eff_repo,
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

        try:
            _dispatch_reviewer(
                state             = state,
                summary_issue_num = summary_issue_num_for_reviewer,
                sprint_branch     = effective_target,
                base_sha          = base_sha,
                head_sha          = head_sha,
                cfg               = cfg,
                repo_name         = eff_repo,
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

    print("\n=== Sprint Summary ===")
    print(f"Processed: {', '.join(summary.processed) or 'none'}")
    print(f"Merged:    {', '.join(summary.merged) or 'none'}")
    if summary.gate_failures:
        print("Gate failures:")
        for line in summary.gate_failures:
            print(f"  {line}")
    if summary.skipped:
        print(f"Skipped:   {', '.join(summary.skipped)}")
    if summary_path:
        print(f"Summary:   {summary_path}")
    if sprint_pr_url:
        print(f"Sprint PR: {sprint_pr_url}")


if __name__ == "__main__":
    main()
