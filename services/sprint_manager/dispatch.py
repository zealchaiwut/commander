"""Coder dispatch functions for the sprint manager.

Contains: _dispatch_coder, _load_agent_persona, _agent_identity_env —
extracted from sprint_manager.py (issue #1285).

sprint_manager.py re-imports all symbols so existing call sites remain
unmodified.
"""
from __future__ import annotations

import os
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
    _resolve_coder_model,
    _resolve_cline_model,
    _effective_coder_backend,
)
from services.sprint_manager.label_transitions import _add_blocked_label  # noqa: E402
from services.sprint_manager.timekeeping import _utcnow  # noqa: E402

# Same formula as sprint_manager.REPO_ROOT — this file lives at the same level.
REPO_ROOT = _REPO_ROOT
WORKTESTER_ROOT = Path(os.environ.get(
    "WORKTESTER_ROOT",
    str(Path.home() / "dev" / "commander" / "tester"),
))

# Mirror sprint_manager.py constants so _dispatch_coder behaviour is unchanged.
HANG_KILL_SECS = 60 * 60          # 60 minutes
_RATE_LIMIT_MAX_RETRIES = 3
_RATE_LIMIT_BACKOFF_DELAYS = [30, 60, 120]   # seconds per attempt


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


def _dispatch_doctor(*args, **kwargs):
    """Proxy to sprint_manager._dispatch_doctor."""
    _f = _lookup_in_sm("_dispatch_doctor", _dispatch_doctor)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


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

    guard_err = _design_docs_guard(cwd_path)
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
    sys.stdout.flush()

    # Build subprocess environment first so ANTHROPIC_API_KEY handling is backend-aware.
    sub_env = os.environ.copy()
    sub_env.update(_agent_identity_env("coder", issue_num))  # tag hooks/telemetry as the docs prescribe
    sub_env["CLAUDE_MODEL"] = dispatch_model  # hook records model_name on token_usage rows

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
        if _paths_block and coder_persona:
            full_prompt = _paths_block + "\n\n" + coder_persona + "\n\n" + prompt
        elif _paths_block:
            full_prompt = _paths_block + "\n\n" + prompt
        elif coder_persona:
            full_prompt = coder_persona + "\n\n" + prompt
        else:
            full_prompt = prompt
        cmd = ["cline", "-y", "-m", dispatch_model, full_prompt]
        # Do NOT pop ANTHROPIC_API_KEY — Cline uses it for the metered API.
    else:
        # Claude Code (existing default behavior, byte-for-byte unchanged).
        cmd = [
            "claude",
            "--model", dispatch_model,
            "--dangerously-skip-permissions",
        ]
        coder_persona = _load_agent_persona("coder", cwd_path)
        if coder_persona:
            cmd += ["--append-system-prompt", coder_persona]
        _p_prompt = (_paths_block + "\n\n" + prompt) if _paths_block else prompt
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
                        on_running()
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
                on_running()
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
