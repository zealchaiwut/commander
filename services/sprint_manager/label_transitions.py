"""Label transition helper functions for the sprint manager.

Contains: _get_issue_labels, _current_status_labels, _sweep_stale_status,
_transition_safe, _add_blocked_label, _emit_label_transition_event —
extracted from sprint_manager.py (issue #1282).

sprint_manager.py re-imports all symbols so existing call sites remain
unmodified.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Ensure repo root and dashboard dir are on sys.path so imports below work
# regardless of how this module is loaded.
_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.logging import log as structured_log  # noqa: E402
from services.sprint_manager.events import _emit_sprint_lifecycle_event  # noqa: E402
from services.sprint_manager.serialization import (  # noqa: E402
    label_transition_guard as _label_transition_guard,
)

import github_client  # noqa: E402

try:
    from services.sprint_manager.state_machine import (  # noqa: PLC0415
        transition as _sm_transition,
        TicketState as _TicketState,
        TransitionError as _TransitionError,
        STATE_LABELS as _STATE_LABELS,
        STATUS_LABELS as _STATUS_LABELS,
    )
    _STATE_MACHINE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _sm_transition = None  # type: ignore[assignment]
    _TicketState = None  # type: ignore[assignment]
    _TransitionError = Exception  # type: ignore[assignment,misc]
    _STATE_LABELS = {}  # type: ignore[assignment]
    _STATUS_LABELS = frozenset()  # type: ignore[assignment]
    _STATE_MACHINE_AVAILABLE = False


# ── sys.modules proxy helpers ─────────────────────────────────────────────────
# Tests monkeypatch sprint_manager attributes such as _sm_transition,
# _STATE_MACHINE_AVAILABLE, and github_client. To respect those patches, all
# runtime lookups must resolve through sys.modules at call time rather than
# using the module-level bindings frozen at import time.

def _lookup_in_sm(attr: str, local_fn):
    """Return the sprint_manager attribute if it differs from local_fn.

    Checks both "sprint_manager" and "services.sprint_manager.sprint_manager"
    keys so that monkeypatches applied via either import path are found.
    Returns None when the attribute in sprint_manager matches local_fn (i.e. no
    patch is active) so the caller can fall back to its own implementation.
    """
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None:
            _f = getattr(_sm, attr, None)
            if _f is not None and _f is not local_fn:
                return _f
    return None


_UNSET = object()


def _sm_attr(attr: str, local_default):
    """Read attr from sprint_manager (if loaded), else return local_default.

    Unlike _lookup_in_sm this always prefers sprint_manager's value, including
    None. Used for boolean flags and module-level objects (github_client) where
    tests patch the attribute on sprint_manager and we must see the patched value.
    """
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None and hasattr(_sm, attr):
            return getattr(_sm, attr)
    return local_default


# ── _r helper ─────────────────────────────────────────────────────────────────

def _r(repo_name: Optional[str]) -> str:
    return repo_name or _sm_attr("github_client", github_client).repo()


# ── _guard_sprint_labels proxy ────────────────────────────────────────────────
# Resolved at call time from sprint_manager to avoid circular imports and to
# respect monkeypatches on sprint_manager._guard_sprint_labels in tests.

def _guard_sprint_labels(
    add: list,
    remove: list,
    sprint_label: Optional[str] = None,
):
    _f = _lookup_in_sm("_guard_sprint_labels", _guard_sprint_labels)
    if _f is not None:
        return _f(add, remove, sprint_label=sprint_label)
    # Fallback (should never reach in normal runtime — sprint_manager is always
    # imported before these helpers are called): allow all ops.
    return add, remove


# ── label transition functions ────────────────────────────────────────────────

def _get_issue_labels(issue_num: int, repo_name: Optional[str] = None) -> set[str]:
    """Re-fetch current labels via mirror or REST (not GraphQL, issue #1783).

    Tries the DB mirror first (zero gh subprocess calls); falls back to the
    REST endpoint ``gh api repos/{repo}/issues/{N}`` (counts against REST quota,
    not the scarcer 5000/hr GraphQL budget).
    """
    r = _r(repo_name)
    _gc = _sm_attr("github_client", github_client)
    try:
        mirror_issue = _gc._mirror_issue(r, issue_num)
        if mirror_issue is not None:
            return {
                lbl["name"] for lbl in mirror_issue.get("labels", [])
                if isinstance(lbl, dict) and lbl.get("name")
            }
    except Exception:
        pass
    # REST fallback (not GraphQL)
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{r}/issues/{issue_num}"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        return {lbl["name"] for lbl in data.get("labels", []) if lbl.get("name")}
    except Exception:
        return set()


def _sweep_stale_status(
    status_label: str,
    sprint_label: str,
    repo_name: Optional[str],
    active_issue: Optional[int] = None,
) -> None:
    """Remove a leftover transient status label (``in-progress`` or ``SIT``) from
    sprint tickets that are not being actively worked.

    Only one ticket may legitimately carry ``in-progress`` (the coder's active
    ticket) and one ``SIT`` (the tester's active ticket) at a time. In pipeline
    mode both run concurrently, so a crash between the remove-label and add-label
    calls, or an interrupted prior run, can leave a ghost label on a ticket no
    longer being worked (issue #738 AC5). One REST ``gh api`` issue query finds them; we
    clear all except ``active_issue``. Best-effort and bounded (no per-ticket
    fetches).
    """
    r = _r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "api", f"repos/{r}/issues",
                "-f", "state=open",
                "-f", f"labels={status_label},{sprint_label}",
                "-f", "per_page=100",
                "--jq", ".[].number",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return
        nums = [int(n) for n in (out.stdout or "").split() if n.strip().isdigit()]
    except Exception:
        return
    cleared: list[int] = []
    for n in nums:
        if active_issue is not None and n == active_issue:
            continue
        try:
            subprocess.run(
                ["gh", "issue", "edit", str(n), "--repo", r, "--remove-label", status_label],
                capture_output=True, text=True, timeout=15,
            )
            cleared.append(n)
        except Exception:
            pass
    if cleared:
        sys.stdout.write(str(f"  [sweep] cleared stale {status_label} from {len(cleared)} ticket(s): {cleared}") + "\n")


def _current_status_labels(issue_num: int, repo_name: Optional[str]) -> "frozenset[str] | None":
    """Best-effort fetch of the issue's current status labels (issue #720).

    Returns the subset of STATUS_LABELS currently applied, or None if the
    labels could not be fetched (so the caller can degrade gracefully).
    """
    try:
        gc = _sm_attr("github_client", github_client)
        issue = gc.get_issue(issue_num, repo_name=repo_name)
        names = {lbl.get("name") for lbl in issue.get("labels", []) if lbl.get("name")}
        return frozenset(_STATUS_LABELS & names)
    except Exception:
        return None


def _emit_label_transition_event(
    issue_num: int,
    target_state: "_TicketState",
    actor: str,
    repo_name: Optional[str],
    before: "frozenset[str] | None",
) -> None:
    """Emit a ticket_label_changed activity event for a real label change (issue #720)."""
    desired = frozenset(_STATE_LABELS.get(target_state, frozenset()))
    have_before = before is not None
    before_set = before if have_before else frozenset()

    added = sorted(desired - before_set)
    removed = sorted(before_set - desired) if have_before else []
    from_label = (sorted(before_set)[0] if before_set else None) if have_before else None
    to_label = sorted(desired)[0] if desired else None

    project = repo_name
    if not project:
        try:
            project = _sm_attr("github_client", github_client).repo()
        except Exception:
            project = "dashboard"

    _emit_sprint_lifecycle_event(
        type="ticket_label_changed",
        target=f"#{issue_num}",
        actor=actor,
        detail={
            "from_label": from_label,
            "to_label": to_label,
            "added": added,
            "removed": removed,
        },
        project=project,
    )


def _transition_safe(
    issue_num: int,
    target_state: "_TicketState",
    actor: str,
    repo_name: Optional[str] = None,
    note: Optional[str] = None,
) -> None:
    """Call state_machine.transition() best-effort; log warnings on failure."""
    # Resolve at call time so test monkeypatches on sprint_manager are honoured.
    _available = _sm_attr("_STATE_MACHINE_AVAILABLE", _STATE_MACHINE_AVAILABLE)
    _trans = _sm_attr("_sm_transition", _sm_transition)
    if not _available or _trans is None:
        structured_log.warn(
            "state_machine_unavailable",
            f"state_machine not available; skipping {target_state} transition for #{issue_num}",
            issue_num=issue_num,
        )
        return
    try:
        # issue #738: serialize the read-modify-write so concurrent coder/tester
        # label writes in pipeline mode cannot interleave into ghost/duplicate
        # labels. Uncontended (and reentrant) in serial mode — no behavior change.
        with _label_transition_guard():
            before = _current_status_labels(issue_num, repo_name)
            changed = _trans(issue_num, target_state, actor=actor, repo=repo_name, note=note)
        structured_log.info(
            "ticket_transition",
            f"transition #{issue_num} → {target_state.value} actor={actor!r}",
            issue_num=issue_num,
            target_state=target_state.value,
            actor=actor,
        )
        # issue #720: emit an activity event only when a label actually changed.
        if changed:
            _emit_label_transition_event(issue_num, target_state, actor, repo_name, before)
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
        sys.stderr.write(str(f"  [label-guard] 'blocked' label suppressed for #{issue_num} during active sprint run") + "\n")
        try:
            gc = _sm_attr("github_client", github_client)
            gc.add_comment(
                issue_num,
                f"Issue hung (HANG): {reason}. 'blocked' label deferred — applied post-run.",
                repo_name=repo_name,
            )
        except Exception as e:
            structured_log.warn("hang_comment_failed", f"failed to post hang comment: {e}", exc=str(e))
        return
    # Route the label write through transition() — the single source of truth
    # for status-label mutations (issue #752). Never edit the 'blocked' label
    # via github_client directly.
    _transition_safe(
        issue_num,
        _TicketState.BLOCKED,
        actor="sprint_manager:hang",
        repo_name=repo_name,
        note=reason,
    )
    try:
        gc = _sm_attr("github_client", github_client)
        gc.add_comment(
            issue_num,
            f"Issue blocked by sprint manager (HANG): {reason}",
            repo_name=repo_name,
        )
    except Exception as e:
        structured_log.warn("blocked_comment_failed", f"failed to post blocked comment: {e}", exc=str(e))
