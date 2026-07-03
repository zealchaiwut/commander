"""Coder dispatch functions for the sprint manager.

Contains: _dispatch_coder, _load_agent_persona, _agent_identity_env —
extracted from sprint_manager.py (issue #1285).

sprint_manager.py re-imports all symbols so existing call sites remain
unmodified.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

# Ensure repo root and dashboard dir are on sys.path so sibling service
# imports work regardless of invocation path.
_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.logging import log as structured_log  # noqa: E402
from services.sprint_manager.worktree import _git_worktree_root  # noqa: E402
from services.sprint_manager.model_routing import (  # noqa: E402
    ICA_FORCED_MODEL,
    _resolve_coder_model,
    _resolve_cline_model,
    _effective_coder_backend,
    apply_ica_agent_env,
    get_effective_llm_provider,
    get_role_profile,
)
from services.sprint_manager.failures import FailureCategory  # noqa: E402
from services.sprint_manager.label_transitions import _add_blocked_label  # noqa: E402
from services.sprint_manager.timekeeping import _utcnow  # noqa: E402
from services.sprint_manager import agent_browser_runner  # noqa: E402

# Same formula as sprint_manager.REPO_ROOT — this file lives at the same level.
REPO_ROOT = _REPO_ROOT
WORKTESTER_ROOT = Path(os.environ.get(
    "WORKTESTER_ROOT",
    str(Path.home() / "dev" / "commander" / "tester"),
))
WORKTESTER_DASHBOARD = WORKTESTER_ROOT / "apps" / "dashboard"

# Mirror sprint_manager.py constants so _dispatch_coder behaviour is unchanged.
HANG_KILL_SECS = 60 * 60          # 60 minutes
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BACKOFF_DELAYS = [30, 60, 120]   # seconds per attempt

# Doctor auth probe cache (mirrors sprint_manager.py; moved here with the doctor functions).
_DOCTOR_AUTH_LAST_PROBE: float = 0.0
_DOCTOR_CLINE_AUTH_LAST_PROBE: float = 0.0
_DOCTOR_AUTH_PROBE_TTL: float = 5 * 60  # 5 minutes
DOCTOR_MIN_DISK_BYTES: int = 1 * 1024 * 1024 * 1024  # 1 GB minimum free space


# ── sys.modules proxy helper ──────────────────────────────────────────────────
# Deferred lookups via sys.modules avoid a circular import (dispatch.py is
# imported BY sprint_manager.py) while also ensuring that test monkeypatches
# applied to sprint_manager attributes are respected. Without this, a test
# that does `patch.object(sm, "X", mock)` would not affect calls to X from
# within this module, because the name X is bound at import time in dispatch's
# namespace, not through the sprint_manager module's __dict__.

def _lookup_in_sm(attr: str, local_fn):
    """Return the sprint_manager attribute if it differs from local_fn.

    Checks both "sprint_manager" and "services.sprint_manager.sprint_manager"
    keys so that monkeypatches applied via either import path are found.
    Returns None when no patch is active so the caller uses its own fallback.
    """
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None:
            _f = getattr(_sm, attr, None)
            if _f is not None and _f is not local_fn:
                return _f
    return None


# ── Proxy functions ───────────────────────────────────────────────────────────
# Functions imported from other sprint_manager sub-modules are re-exported
# through sprint_manager.py with the same name. Tests patch them on the SM
# module via `patch.object(sm, "X", mock)`. To honour those patches, each
# proxy below calls _lookup_in_sm at call time, falling back to the real
# implementation when no patch is active.

def _worktree_hygiene(*args, **kwargs):
    """Proxy to sprint_manager._worktree_hygiene (worktree.py)."""
    _f = _lookup_in_sm("_worktree_hygiene", _worktree_hygiene)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.worktree import _worktree_hygiene as _real
    return _real(*args, **kwargs)


def _crg_update_worktree(*args, **kwargs):
    """Proxy to sprint_manager._crg_update_worktree (worktree.py)."""
    _f = _lookup_in_sm("_crg_update_worktree", _crg_update_worktree)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.worktree import _crg_update_worktree as _real
    return _real(*args, **kwargs)


def _post_agent_event(*args, **kwargs):
    """Proxy to sprint_manager._post_agent_event (events.py)."""
    _f = _lookup_in_sm("_post_agent_event", _post_agent_event)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.events import _post_agent_event as _real
    return _real(*args, **kwargs)


def dispatch_alerts(*args, **kwargs):
    """Proxy to sprint_manager.dispatch_alerts (alerts.py)."""
    _f = _lookup_in_sm("dispatch_alerts", dispatch_alerts)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.alerts import dispatch_alerts as _real
    return _real(*args, **kwargs)


class HangDetector:
    """Proxy class that delegates to sprint_manager.HangDetector (alerts.py) at instantiation.

    Using __new__ lets tests patch sm.HangDetector with a mock while leaving
    the real class accessible as the fallback. When no patch is active,
    instantiation goes to the real alerts.HangDetector.
    """

    def __new__(cls, *args, **kwargs):
        _real_cls = _lookup_in_sm("HangDetector", HangDetector)
        if _real_cls is not None:
            return _real_cls(*args, **kwargs)
        from services.sprint_manager.alerts import HangDetector as _RealHangDetector
        return _RealHangDetector(*args, **kwargs)


def _get_issue_labels(*args, **kwargs):
    """Proxy to sprint_manager._get_issue_labels (label_transitions.py)."""
    _f = _lookup_in_sm("_get_issue_labels", _get_issue_labels)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.label_transitions import _get_issue_labels as _real  # noqa: PLC0415
    return _real(*args, **kwargs)


def _classify_risk_tier(*args, **kwargs):
    """Proxy to sprint_manager._classify_risk_tier."""
    _f = _lookup_in_sm("_classify_risk_tier", _classify_risk_tier)
    if _f is not None:
        return _f(*args, **kwargs)
    return "LOW"


def _check_risk_disagreement(*args, **kwargs):
    """Proxy to sprint_manager._check_risk_disagreement."""
    _f = _lookup_in_sm("_check_risk_disagreement", _check_risk_disagreement)
    if _f is not None:
        return _f(*args, **kwargs)


def _resolve_uat_env_for_tester(*args, **kwargs):
    """Proxy to sprint_manager._resolve_uat_env_for_tester (worktree.py)."""
    _f = _lookup_in_sm("_resolve_uat_env_for_tester", _resolve_uat_env_for_tester)
    if _f is not None:
        return _f(*args, **kwargs)
    from services.sprint_manager.worktree import _resolve_uat_env_for_tester as _real  # noqa: PLC0415
    return _real(*args, **kwargs)


# ── Extracted doctor functions (issue #1286) ──────────────────────────────────


def _doctor_probe_auth_impl(backend: str = "claude-code") -> Optional[str]:
    """Real implementation of _doctor_probe_auth."""
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


def _doctor_probe_auth(backend: str = "claude-code") -> Optional[str]:
    """Probe coder CLI auth. Returns None on success, error string on failure.

    backend selects which CLI to probe: 'cline' for Cline headless, anything
    else probes the 'claude' CLI (existing behaviour). Result is cached per
    backend for _DOCTOR_AUTH_PROBE_TTL seconds.
    """
    _f = _lookup_in_sm("_doctor_probe_auth", _doctor_probe_auth)
    if _f is not None:
        return _f(backend)
    return _doctor_probe_auth_impl(backend)


def _dispatch_doctor_impl(
    cfg: Optional["SprintConfig"],
    alert_modes: list,
    issue_num: Optional[int] = None,
    eff_repo: Optional[str] = None,
) -> Optional[str]:
    """Real implementation of _dispatch_doctor."""
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


def _dispatch_doctor(
    cfg: Optional["SprintConfig"],
    alert_modes: list,
    issue_num: Optional[int] = None,
    eff_repo: Optional[str] = None,
) -> Optional[str]:
    """Pre-dispatch environment health check (issue #789).

    Checks: CLI present, auth alive (cached), worktree exists, disk space.
    Returns None when healthy. On any failure fires a dispatch-blocked alert
    and returns the error string so the caller can halt without spawning a worker.
    """
    _f = _lookup_in_sm("_dispatch_doctor", _dispatch_doctor)
    if _f is not None:
        return _f(cfg, alert_modes, issue_num=issue_num, eff_repo=eff_repo)
    return _dispatch_doctor_impl(cfg, alert_modes, issue_num=issue_num, eff_repo=eff_repo)


def _design_docs_guard(*args, **kwargs):
    """Proxy to sprint_manager._design_docs_guard."""
    _f = _lookup_in_sm("_design_docs_guard", _design_docs_guard)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


def _db_update_worktree_shas_sm(*args, **kwargs):
    """Proxy to sprint_manager._db_update_worktree_shas_sm."""
    _f = _lookup_in_sm("_db_update_worktree_shas_sm", _db_update_worktree_shas_sm)
    if _f is not None:
        return _f(*args, **kwargs)


def _issue_log_path(*args, **kwargs):
    """Proxy to sprint_manager._issue_log_path."""
    _f = _lookup_in_sm("_issue_log_path", _issue_log_path)
    if _f is not None:
        return _f(*args, **kwargs)
    raise RuntimeError("_issue_log_path: sprint_manager not loaded")


def _load_estimate(*args, **kwargs):
    """Proxy to sprint_manager._load_estimate."""
    _f = _lookup_in_sm("_load_estimate", _load_estimate)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


def _build_estimate_paths_block(*args, **kwargs):
    """Proxy to sprint_manager._build_estimate_paths_block."""
    _f = _lookup_in_sm("_build_estimate_paths_block", _build_estimate_paths_block)
    if _f is not None:
        return _f(*args, **kwargs)
    return ""


def _fetch_dispatch_issue_body(eff_repo: Optional[str], issue_num: int) -> Optional[str]:
    """Fetch the issue body once for dispatch, to pass into _build_design_block.

    Returns the body string on success. On failure (non-zero gh exit or an
    exception), returns the sentinel "" (empty string, NOT None) and logs the
    failure at WARN level — so _build_design_block skips its own fallback gh
    fetch instead of issuing a second subprocess round-trip per ticket
    (issue #1573). Returns None only when there is no repo to query, preserving
    the legacy path where _build_design_block performs the fetch itself.
    """
    if not eff_repo:
        return None
    try:
        _gh_result = subprocess.run(
            ["gh", "api", f"repos/{eff_repo}/issues/{issue_num}"],
            capture_output=True, text=True, timeout=30,
        )
        if _gh_result.returncode == 0:
            return json.loads(_gh_result.stdout).get("body", "") or ""
        structured_log.warn(
            "coder_dispatch_issue_body_fetch_failed",
            f"[coder] gh fetch of issue body failed for issue #{issue_num}"
            f" (exit {_gh_result.returncode}); passing sentinel to skip re-fetch",
            issue_num=issue_num,
            exit_code=_gh_result.returncode,
            stderr=(_gh_result.stderr or "").strip()[:500],
        )
        return ""
    except Exception as _exc:
        structured_log.warn(
            "coder_dispatch_issue_body_fetch_failed",
            f"[coder] gh fetch of issue body errored for issue #{issue_num}"
            f" ({_exc}); passing sentinel to skip re-fetch",
            issue_num=issue_num,
            error=repr(_exc),
        )
        return ""


def _build_design_block(*args, **kwargs):
    """Proxy to sprint_manager._build_design_block (issue #1488)."""
    _f = _lookup_in_sm("_build_design_block", _build_design_block)
    if _f is not None:
        return _f(*args, **kwargs)
    return ""


def _build_failure_suffix(*args, **kwargs):
    """Proxy to sprint_manager._build_failure_suffix."""
    _f = _lookup_in_sm("_build_failure_suffix", _build_failure_suffix)
    if _f is not None:
        return _f(*args, **kwargs)
    return ""


def _bump_estimate_size(*args, **kwargs):
    """Proxy to sprint_manager._bump_estimate_size."""
    _f = _lookup_in_sm("_bump_estimate_size", _bump_estimate_size)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


def _r(*args, **kwargs):
    """Proxy to sprint_manager._r."""
    _f = _lookup_in_sm("_r", _r)
    if _f is not None:
        return _f(*args, **kwargs)
    return args[0] or ""


def _impeccable_context_instruction(*args, **kwargs):
    """Proxy to sprint_manager._impeccable_context_instruction."""
    _f = _lookup_in_sm("_impeccable_context_instruction", _impeccable_context_instruction)
    if _f is not None:
        return _f(*args, **kwargs)
    return ""


def record_failure(*args, **kwargs):
    """Proxy to sprint_manager.record_failure."""
    _f = _lookup_in_sm("record_failure", record_failure)
    if _f is not None:
        return _f(*args, **kwargs)


def _build_crash_detail(*args, **kwargs):
    """Proxy to sprint_manager._build_crash_detail."""
    _f = _lookup_in_sm("_build_crash_detail", _build_crash_detail)
    if _f is not None:
        return _f(*args, **kwargs)
    return ""


def _plan_has_parent(*args, **kwargs):
    """Proxy to sprint_manager._plan_has_parent."""
    _f = _lookup_in_sm("_plan_has_parent", _plan_has_parent)
    if _f is not None:
        return _f(*args, **kwargs)
    return False


def _is_rate_limit_error(*args, **kwargs):
    """Proxy to sprint_manager._is_rate_limit_error."""
    _f = _lookup_in_sm("_is_rate_limit_error", _is_rate_limit_error)
    if _f is not None:
        return _f(*args, **kwargs)
    return False, None


# ── The three extracted functions ─────────────────────────────────────────────


def _load_agent_persona_impl(role: str, base_dir: "Path | None" = None) -> str:
    """Real implementation of _load_agent_persona (called by the proxy below)."""
    candidates: list[Path] = []
    if base_dir is not None:
        base = Path(base_dir)
        candidates.append(base / ".claude" / "agents" / f"{role}.md")
        wt_root = _git_worktree_root(base)
        if wt_root is not None:
            candidates.append(wt_root / ".claude" / "agents" / f"{role}.md")
    candidates.append(REPO_ROOT / ".claude" / "agents" / f"{role}.md")
    seen: set[Path] = set()
    for path in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
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


def _load_agent_persona(role: str, base_dir: "Path | None" = None) -> str:
    """Return the .claude/agents/<role>.md persona (minus YAML frontmatter).

    A headless ``claude -p`` run does NOT auto-load project subagents and cannot
    use the interactive ``/coder`` // ``/tester`` slash commands, so without this
    the dispatched agent runs with only a terse inline prompt — markedly weaker
    than a manual session that delegates to the rich subagent persona. We read
    the same .md the interactive agent uses and pass it via --append-system-prompt.

    Looks in ``base_dir`` (the clone the agent runs in), then that worktree's git
    root (personas live at repo root, not under apps/dashboard), then REPO_ROOT.
    Returns "" if not found, so callers degrade gracefully to the old behaviour.
    """
    _f = _lookup_in_sm("_load_agent_persona", _load_agent_persona)
    if _f is not None:
        return _f(role, base_dir)
    return _load_agent_persona_impl(role, base_dir)


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
    hang_continuation: Optional[dict] = None,
    attempt_kind: Optional[str] = None,
    coder_backend_override: Optional[str] = None,
    worktree_override: Optional[Path] = None,
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

    worktree_override: when set, use this path as the working directory instead
    of cfg.worktree_coder.  Used by the worktree pool (issue #1411) to assign
    an isolated worktree slot to each concurrent coder dispatch.
    """
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    api_url  = cfg.api_url if cfg else None
    if worktree_override is not None:
        cwd_path = worktree_override
    elif cfg:
        cwd_path = cfg.worktree_coder
    else:
        # No sprint.yaml: cwd is the coder clone when dispatched from the dashboard.
        cwd_path = Path.cwd()
        if not (cwd_path / "PRODUCT.md").exists() and WORKTESTER_ROOT.exists():
            cwd_path = WORKTESTER_ROOT

    # Pre-dispatch doctor: check environment health before doing any work.
    doctor_err = _dispatch_doctor(cfg, alert_modes, issue_num=issue_num, eff_repo=eff_repo)
    if doctor_err:
        structured_log.error(
            "dispatch_blocked",
            f"[coder] pre-dispatch doctor failed for issue #{issue_num}: {doctor_err}",
            issue_num=issue_num,
        )
        record_failure(issue_num, "dispatch-blocked", detail=doctor_err)
        return False, "dispatch-blocked"

    # Check design docs in the STABLE coder clone, not the ephemeral worktree
    # slot. The slot is reset to its (docs-committed) base branch by worktree
    # hygiene AFTER this guard, so checking it pre-hygiene saw a transient state:
    # once a sibling ticket left the pooled slot dirty and `git clean -fdx` ran on
    # release, the next ticket's guard found no PRODUCT.md/DESIGN.md and
    # false-failed `design_docs_missing` (observed: #1460/#1461 in sprint-94.1
    # after #1462 dirtied slot-0). The docs are committed on every branch, so the
    # coder clone is the reliable place to assert their presence.
    _docs_check_path = cfg.worktree_coder if cfg is not None else cwd_path
    guard_err = _design_docs_guard(_docs_check_path)
    if guard_err:
        structured_log.error(
            "design_docs_missing",
            f"[coder] design docs guard failed for issue #{issue_num}: {guard_err}",
            issue_num=issue_num,
        )
        record_failure(issue_num, "design_docs_missing", detail=guard_err)
        return False, "design_docs_missing"

    # Worktree hygiene (issue #788): fetch, stash dirty state, reset to base, validate branch.
    _is_rerun_child = bool(sprint_label and _plan_has_parent(sprint_label, cfg))
    _is_retry = bool(prior_failures) or _is_rerun_child
    _wt_sha, _base_sha, _hygiene_err = _worktree_hygiene(
        worktree=cwd_path,
        ticket_id=issue_num,
        merge_target=sprint_branch,
        is_retry=_is_retry,
        recover_on_rebase_conflict=True,
    )
    if sprint_label:
        _db_update_worktree_shas_sm(issue_num, sprint_label, "coder", _wt_sha, _base_sha)
    if _hygiene_err:
        structured_log.error(
            "worktree_hygiene_failed",
            f"[coder] worktree hygiene blocked dispatch for issue #{issue_num}: {_hygiene_err}",
            issue_num=issue_num,
            hygiene_error=_hygiene_err,
        )
        return False, _hygiene_err

    _crg_update_worktree(cwd_path, role="coder")

    sys.stdout.write(str(f"  Dispatching coder for issue #{issue_num} ...") + "\n")
    sys.stdout.flush()
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

    # Load estimate early for paths injection (issue #1402) and model routing below.
    _coder_estimate = _load_estimate(issue_num)
    _paths_block = _build_estimate_paths_block(_coder_estimate)

    # Fetch issue body once here and pass it to _build_design_block so it can
    # skip the redundant per-dispatch gh api subprocess call (issue #1541).
    # On fetch failure the helper returns the "" sentinel (not None) and logs the
    # failure, so _build_design_block does not issue a second gh call (issue #1573).
    _fetched_issue_body: Optional[str] = _fetch_dispatch_issue_body(eff_repo, issue_num)

    _design_block = _build_design_block(issue_num, eff_repo, cwd_path, issue_body=_fetched_issue_body)

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
            " finish_feature.py. Merging is exclusively sprint_manager's job via"
            " `scripts/finish_feature.py` after quality gates pass."
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

    # Inject hang-continuation context (issue #787): appended BEFORE the
    # normal failure suffix so the agent sees the idle-kill context first.
    if hang_continuation:
        _hc_ts   = hang_continuation.get("timestamp", _utcnow())
        _hc_tail = hang_continuation.get("log_tail", [])
        _tail_str = "\n".join(_hc_tail[-20:]) if _hc_tail else "(no output captured)"
        prompt += (
            f"\n\nprior attempt idle-killed at {_hc_ts}; "
            f"last output: {_tail_str}; "
            "continue, do not restart"
        )

    # Inject failure context: accumulated history (fix-loop, issue #618) or sidecar fallback
    if prior_failures:
        _ctx_lines = [
            f"\n\nThis is fix attempt {len(prior_failures) + 1}. Prior attempts failed:"
        ]
        for _h in prior_failures:
            _cat = _h.get("category", "?")
            _detail = _h.get("reason") or _h.get("summary") or ""
            _ctx_lines.append(f"  - Attempt {_h.get('attempt', 0) + 1}: {_cat}: {_detail}")
        _ctx_lines.append(
            "Fix-round focus: target ONLY what the gate flagged above. Re-use the"
            " existing feature branch and its prior work — fix the specific"
            " failure and do NOT re-implement acceptance criteria that already"
            " pass or rewrite passing tests. A fix-round is a surgical fix, not a"
            " re-implementation."
        )
        failure_suffix = "\n".join(_ctx_lines)
    else:
        failure_suffix = _build_failure_suffix(issue_num)
    if failure_suffix:
        prompt = prompt + failure_suffix

    # Re-estimate after failure: a ticket that failed once is usually bigger than
    # first sized — bump its cached size one tier on a fix-round so the
    # budget/forecast and model-routing below reflect reality (resume-from-failure
    # TODO). No-op for unestimated tickets.
    if prior_failures:
        _bumped = _bump_estimate_size(issue_num)
        if _bumped:
            sys.stdout.write(str(f"  [re-estimate] #{issue_num}: size bumped to {_bumped} after prior failure") + "\n")
            sys.stdout.flush()

    # Resolve backend and model. Keep the prompt as the last element of cmd so
    # `cmd[-1] += sprint_hint` (below) works for both backends.
    # coder_backend_override takes precedence when supplied by the caller (issue #920:
    # the fix-loop pre-computes the backend and escalates from cline to claude-code on failure).
    coder_model, coder_routing_reason = _resolve_coder_model(issue_num, cfg, estimate=_coder_estimate)
    coder_backend = coder_backend_override if coder_backend_override is not None else _effective_coder_backend(sprint_label, cfg, prior_failures)
    if coder_backend == "cline":
        dispatch_model, dispatch_routing_reason = _resolve_cline_model(cfg, coder_model)
    else:
        dispatch_model, dispatch_routing_reason = coder_model, coder_routing_reason
    sys.stdout.write(str(
        f"  [size-routing] issue #{issue_num}: model={dispatch_model}, reason={dispatch_routing_reason}, backend={coder_backend}"
    ) + "\n")
    if _paths_block:
        sys.stdout.write(f"  [estimate-paths] #{issue_num}: injecting paths into coder prompt\n{_paths_block}\n")
    else:
        sys.stdout.write(f"  [estimate-paths] #{issue_num}: no estimate file — prompt unchanged\n")
    if _design_block:
        sys.stdout.write(f"  [design-context] #{issue_num}: injecting design block ({len(_design_block)} chars)\n")
    else:
        sys.stdout.write(f"  [design-context] #{issue_num}: no design block injected\n")
    sys.stdout.flush()

    # Build subprocess environment first so ANTHROPIC_API_KEY handling is backend-aware.
    sub_env = os.environ.copy()
    sub_env.update(_agent_identity_env("coder", issue_num))  # tag hooks/telemetry as the docs prescribe
    sub_env["CLAUDE_MODEL"] = dispatch_model  # hook records model_name on token_usage rows
    _coder_profile = get_role_profile("coder", cfg)
    if _coder_profile is not None:
        sub_env["CCPROXY_PROFILE"] = _coder_profile

    if coder_backend == "cline":
        # Cline headless backend (issue #917).
        # -y skips tool-approval prompts (analogous to --dangerously-skip-permissions).
        # Cline has no --append-system-prompt; prepend persona to the prompt string instead.
        # Metered API path: keep ANTHROPIC_API_KEY so Cline can authenticate.
        if dispatch_routing_reason == "cline:fallback-coder_model":
            structured_log.warn(
                "cline_model_missing",
                "[coder] agent_config.cline.model not set — using coder_model for Cline -m "
                "(Claude Code model ids often 404 in Cline; set agent_config.cline.model)",
                issue_num=issue_num,
                coder_model=coder_model,
            )
        if not (cwd_path / ".clinerules").exists():
            structured_log.warn(
                "clinerules_missing",
                f"[coder] .clinerules not found in {cwd_path} — Cline won't load Commander workflow invariants (issue #916 must merge first)",
                issue_num=issue_num,
                worktree=str(cwd_path),
            )
        coder_persona = _load_agent_persona("coder", cwd_path)
        _cline_base = (_design_block + "\n\n" + prompt) if _design_block else prompt
        if _paths_block and coder_persona:
            full_prompt = _paths_block + "\n\n" + coder_persona + "\n\n" + _cline_base
        elif _paths_block:
            full_prompt = _paths_block + "\n\n" + _cline_base
        elif coder_persona:
            full_prompt = coder_persona + "\n\n" + _cline_base
        else:
            full_prompt = _cline_base
        cmd = ["cline", "-y", "-m", dispatch_model, full_prompt]
        # Do NOT pop ANTHROPIC_API_KEY — Cline uses it for the metered API.
        # ICA routing is claude-code only: cline authenticates with the metered
        # key directly and has no custom-headers channel to the proxy.
        if get_effective_llm_provider(sprint_label, cfg, eff_repo) == "ica":
            structured_log.warn(
                "ica_cline_unrouted",
                "[coder] llm_provider=ica but backend=cline — cline dispatches "
                "go direct to the metered Anthropic API, not through ICA",
                issue_num=issue_num,
            )
    else:
        # Per-run ICA routing: point this agent at claude-proxy (issue #1667
        # follow-up). Claude-code branch only — cline has its own metered path.
        # ICA serves only claude-sonnet, so the dispatch model is pinned to it.
        if get_effective_llm_provider(sprint_label, cfg, eff_repo) == "ica":
            apply_ica_agent_env(sub_env, _coder_profile or "ica")
            if dispatch_model != ICA_FORCED_MODEL:
                sys.stdout.write(
                    f"  [ica-model] #{issue_num}: {dispatch_model} → {ICA_FORCED_MODEL} "
                    f"(ICA serves claude-sonnet only)\n"
                )
                dispatch_model = ICA_FORCED_MODEL
                dispatch_routing_reason = "ica:forced-sonnet"
                sub_env["CLAUDE_MODEL"] = dispatch_model
        cmd = [
            "claude",
            "--model", dispatch_model,
            "--dangerously-skip-permissions",
        ]
        coder_persona = _load_agent_persona("coder", cwd_path)
        if coder_persona:
            cmd += ["--append-system-prompt", coder_persona]
        _cc_base = (_design_block + "\n\n" + prompt) if _design_block else prompt
        _p_prompt = (_paths_block + "\n\n" + _cc_base) if _paths_block else _cc_base
        cmd += ["-p", _p_prompt]
        # Claude Code uses subscription auth; strip API key to avoid metered billing.
        sub_env.pop("ANTHROPIC_API_KEY", None)

    # Build remaining subprocess environment keys.
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
        sys.stdout.write(str(f"  [port] COMMANDER_APP_PORT={chosen_port} injected into coder env") + "\n")

    # Pre-touch so the dispatch-log endpoint always has a file to read.
    if not log_path.exists():
        log_path.write_text("", encoding="utf-8")

    for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
        open_mode = "w" if attempt == 0 else "a"
        _dispatch_t0 = time.monotonic()
        structured_log.info(
            "dispatch_start", f"coder dispatch #{issue_num} (attempt {attempt + 1})",
            issue_num=issue_num, agent_role="coder", sprint_label=sprint_label,
            attempt=attempt + 1, model=dispatch_model, cmd=cmd[:4],
        )
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
            _cli_name = "cline" if coder_backend == "cline" else "claude"
            if _allow_stub:
                sys.stdout.write(str(f"  [coder] {_cli_name} CLI not found -- stub success") + "\n")
                if on_running is not None:
                    try:
                        on_running(None)
                    except Exception:
                        pass
                return True, None
            # Production: log the error and return a real failure so the stall
            # warning shows which CLI was not found instead of silently succeeding.
            err_msg = (
                f"[coder] ERROR: {_cli_name} CLI not found for issue #{issue_num}.\n"
                f"PATH={sub_env.get('PATH', '<empty>')}\n"
                f"Sprint cannot proceed. Install {_cli_name} CLI or set COMMANDER_ALLOW_STUB_SUCCESS=1 for testing.\n"
            )
            structured_log.error(f"{_cli_name}_cli_not_found", f"{_cli_name} CLI not found for issue #{issue_num}", issue_num=issue_num, subprocess="coder", path=sub_env.get("PATH", ""))
            try:
                with log_path.open("a") as lf:
                    lf.write(err_msg)
            except OSError:
                pass
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{issue_num}: {_cli_name} CLI not found",
                body=f"_dispatch_coder failed to spawn '{_cli_name}' subprocess: file not found. PATH={sub_env.get('PATH', '<empty>')}. Sprint cannot proceed.",
                issue_num=issue_num,
                category=FailureCategory.CRASH,
                cfg=cfg,
                repo=eff_repo,
            )
            return False, FailureCategory.CRASH

        if on_running is not None:
            try:
                on_running(proc.pid)
            except Exception:
                pass

        detector = HangDetector(issue_num=issue_num, log_path=log_path, proc=proc,
                                 agent_role="coder", attempt=attempt + 1)
        detector.start()
        rc = proc.wait()
        detector.stop()

        _dispatch_secs = round(time.monotonic() - _dispatch_t0, 1)
        if rc == 0:
            structured_log.info(
                "dispatch_finished", f"coder #{issue_num} finished",
                issue_num=issue_num, agent_role="coder", sprint_label=sprint_label,
                attempt=attempt + 1, exit_code=0, duration_s=_dispatch_secs,
            )
        else:
            _stderr_tail = ""
            try:
                _stderr_tail = log_path.read_text(encoding="utf-8", errors="replace")[-500:]
            except Exception:
                pass
            structured_log.error(
                "dispatch_failed", f"coder #{issue_num} exited {rc}",
                issue_num=issue_num, agent_role="coder", sprint_label=sprint_label,
                attempt=attempt + 1, exit_code=rc, duration_s=_dispatch_secs,
                stderr_tail=_stderr_tail,
            )

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
            # Extract the last N lines of the log as a structured log_tail field (issue #787).
            _log_tail: list[str] = []
            try:
                _log_text = log_path.read_text(encoding="utf-8", errors="replace")
                _log_tail = _log_text.splitlines()[-50:]
            except Exception:
                pass
            record_failure(
                issue_num,
                "hang",
                detail=_build_crash_detail(log_path, signal="SIGKILL"),
                summary=f"Issue #{issue_num}: coder hung for {HANG_KILL_SECS//60} minutes and was killed",
                log_tail=_log_tail,
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
            sys.stdout.write(str(f"  Rate limit hit, retrying in {delay} seconds (attempt {retry_num}/{_RATE_LIMIT_MAX_RETRIES})") + "\n")
            sys.stdout.flush()
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
            sys.stdout.write(str(f"  Subscription rate limit exhausted for coder issue #{issue_num} after {_RATE_LIMIT_MAX_RETRIES} retries") + "\n")
            sys.stdout.flush()
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


# ── Extracted tester dispatch function (issue #1286) ─────────────────────────


def _dispatch_tester(
    issue_num: int,
    alert_modes: list,
    sprint_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    chosen_port: Optional[int] = None,
    rate_limit_events: Optional[list] = None,
    on_running: Optional[object] = None,
    sprint_label: Optional[str] = None,
    pre_dispatch_risk: Optional[str] = None,
    prior_failures: Optional[list] = None,
) -> tuple:
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
    _tester_profile = get_role_profile("tester", cfg)
    if _tester_profile is not None:
        sub_env["CCPROXY_PROFILE"] = _tester_profile
    # Per-run ICA routing: point this agent at claude-proxy (issue #1667
    # follow-up), mirroring the coder claude-code branch. ICA serves only
    # claude-sonnet, so the tester model is pinned to it.
    if get_effective_llm_provider(sprint_label, cfg, eff_repo) == "ica":
        apply_ica_agent_env(sub_env, _tester_profile or "ica")
        if tester_model != ICA_FORCED_MODEL:
            sys.stdout.write(
                f"  [ica-model] tester #{issue_num}: {tester_model} → {ICA_FORCED_MODEL} "
                f"(ICA serves claude-sonnet only)\n"
            )
            tester_model = ICA_FORCED_MODEL
            cmd[2] = ICA_FORCED_MODEL
            sub_env["CLAUDE_MODEL"] = ICA_FORCED_MODEL
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
                        on_running(None)
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
                on_running(proc.pid)
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
