"""Post-sprint reconciliation — surface loose ends after a sprint closes (issue #856).

When a sprint finishes (an explicit Finish Sprint, or a run that ends on its
own) critical artifacts can silently fail to materialize: the executive summary
issue may never be posted, the sprint PR may sit unmerged on a conflict, or
stale in-flight status labels may linger on tickets. This module runs a
read-only reconciliation pass that checks for exactly those three loose ends and
records a structured, idempotent result.

The feature **reports only** — it never auto-posts a summary, auto-merges a PR,
or strips labels (out of scope per the ticket).

Design:

* The three checks (:func:`check_summary_issue`, :func:`check_sprint_pr`,
  :func:`check_stale_labels`) are PURE — they take plain dicts and return a
  ``{"name", "ok", "detail", ...}`` result. They are trivially unit-testable
  with no GitHub access.
* :func:`reconcile_sprint` runs all three and computes ``all_clear``.
* :func:`run_reconciliation` is the orchestrator: it persists the result into
  the sprint's ``<label>-state.json`` (so the history card can render it) and
  emits one activity-log event — but only when the result has *changed* since
  the last pass, which makes re-running idempotent (no duplicate events or
  history entries).
* :func:`gather_inputs_via_gh` collects the inputs from GitHub via the ``gh``
  CLI; it is best-effort and isolated from the pure logic.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

try:  # pragma: no cover - import shim for both package and flat layouts
    from services.sprint_manager.state_machine import STATUS_LABELS
except Exception:  # pragma: no cover
    # Fallback to the canonical list if the import path is unavailable.
    STATUS_LABELS = frozenset({"backlog", "in-progress", "SIT", "UAT", "needs-rework", "blocked"})

# After a sprint closes the only acceptable sprint status label on a ticket is
# ``UAT`` — that is the awaiting-human-sign-off terminal state (see CLAUDE.md:
# "UAT is the done state for progress/completion UI"). Every other in-flight
# status label left on a ticket is a loose end the sprint failed to resolve.
STALE_STATUS_LABELS: frozenset[str] = frozenset(STATUS_LABELS) - frozenset({"UAT"})


# ── helpers ───────────────────────────────────────────────────────────────────

def _label_names(obj: dict) -> list[str]:
    """Extract label name strings from an issue dict.

    Tolerates both GitHub's ``[{"name": "x"}]`` shape and a plain ``["x"]``.
    """
    out: list[str] = []
    for lab in (obj.get("labels") or []):
        if isinstance(lab, dict):
            name = lab.get("name")
            if name:
                out.append(name)
        elif isinstance(lab, str):
            out.append(lab)
    return out


def _utcnow_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


# ── individual checks ──────────────────────────────────────────────────────────

def check_summary_issue(sprint_label: str, issues: Optional[list[dict]]) -> dict:
    """AC2: a summary issue exists for the EXACT sprint label.

    A summary issue is identified by the ``sprint-summary`` label (how
    ``create_summary_github_issue`` tags it) or an "Executive Summary" title,
    AND it must carry this sprint's own label (``sprint-<n>``) so a summary for
    a different sprint cannot satisfy the check.
    """
    for iss in (issues or []):
        names = _label_names(iss)
        title = iss.get("title") or ""
        is_summary = "sprint-summary" in names or "executive summary" in title.lower()
        ties_to_sprint = sprint_label in names
        if is_summary and ties_to_sprint:
            return {
                "name": "summary_issue",
                "ok": True,
                "detail": f"Executive summary issue found for {sprint_label}",
            }
    return {
        "name": "summary_issue",
        "ok": False,
        "detail": f"No executive summary issue found for {sprint_label}",
    }


def check_sprint_pr(pr_info: Optional[dict]) -> dict:
    """AC3: the sprint PR merged; if not, capture the reason + conflicting files.

    ``pr_info`` is ``None`` when no sprint PR was opened (e.g. a manual-target
    run) — nothing to reconcile, so the check passes with a neutral note.
    """
    if pr_info is None:
        return {
            "name": "sprint_pr",
            "ok": True,
            "detail": "No sprint PR was opened — nothing to merge",
            "pr_number": None,
            "conflicting_files": [],
        }

    number = pr_info.get("number")
    if pr_info.get("merged"):
        return {
            "name": "sprint_pr",
            "ok": True,
            "detail": f"Sprint PR #{number} merged",
            "pr_number": number,
            "conflicting_files": [],
        }

    reason = pr_info.get("reason") or "unmerged"
    files = list(pr_info.get("conflicting_files") or [])
    detail = f"PR #{number} unmerged — {reason}"
    if files:
        detail += f" in {', '.join(files)}"
    return {
        "name": "sprint_pr",
        "ok": False,
        "detail": detail,
        "pr_number": number,
        "reason": reason,
        "conflicting_files": files,
    }


def check_stale_labels(tickets: Optional[list[dict]]) -> dict:
    """AC4: no stale (in-flight) sprint status labels remain on the tickets."""
    offenders: list[dict] = []
    for t in (tickets or []):
        num = t.get("number")
        if num is None:
            num = t.get("issue", t.get("ticket_id"))
        stale = sorted(set(_label_names(t)) & STALE_STATUS_LABELS)
        if stale:
            offenders.append({"issue": num, "labels": stale})

    if not offenders:
        return {
            "name": "stale_labels",
            "ok": True,
            "detail": "No stale sprint status labels remain",
            "tickets": [],
        }
    parts = "; ".join(f"#{o['issue']} ({', '.join(o['labels'])})" for o in offenders)
    return {
        "name": "stale_labels",
        "ok": False,
        "detail": f"Stale status labels remain: {parts}",
        "tickets": offenders,
    }


# ── aggregation + idempotency ──────────────────────────────────────────────────

def reconcile_sprint(
    sprint_label: str,
    summary_issues: Optional[list[dict]],
    pr_info: Optional[dict],
    tickets: Optional[list[dict]],
) -> dict:
    """Run all three checks and compute the overall all-clear flag (AC7/AC8)."""
    checks = [
        check_summary_issue(sprint_label, summary_issues),
        check_sprint_pr(pr_info),
        check_stale_labels(tickets),
    ]
    return {
        "sprint_label": sprint_label,
        "all_clear": all(c["ok"] for c in checks),
        "checks": checks,
    }


def _fingerprint(result: dict) -> str:
    """Stable hash of the reconciliation outcome, ignoring the timestamp.

    Two passes with the same checks → same fingerprint → idempotent (AC9).
    """
    payload = json.dumps(
        {"sprint_label": result.get("sprint_label"), "checks": result.get("checks")},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_state(state_path: Path) -> dict:
    try:
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def run_reconciliation(
    *,
    sprint_label: str,
    sprint_number: Optional[int] = None,
    project: str = "",
    state_path: Optional[Path] = None,
    summary_issues: Optional[list[dict]] = None,
    pr_info: Optional[dict] = None,
    tickets: Optional[list[dict]] = None,
    emit_event: Optional[Callable] = None,
    now: Optional[Callable[[], str]] = None,
    action_id: Optional[str] = None,
) -> dict:
    """Run the reconciliation pass, persist it, and emit an event idempotently.

    * Persists the result into ``<label>-state.json`` under ``reconciliation``
      so the history card (``GET /api/sprints/history``) can render it (AC5).
    * Emits one ``sprint_reconciled`` activity-log event tied to the sprint
      (AC6) — but ONLY when the outcome has changed since the last pass, so
      re-running Finish Sprint produces no duplicate event or history entry
      (AC9).
    """
    result = reconcile_sprint(sprint_label, summary_issues, pr_info, tickets)
    fingerprint = _fingerprint(result)
    result["fingerprint"] = fingerprint
    result["checked_at"] = (now() if now else _utcnow_iso())

    state_path = Path(state_path) if state_path is not None else None

    # Idempotency: compare against any previously persisted reconciliation.
    prior = None
    if state_path is not None:
        prior = _read_state(state_path).get("reconciliation")
    changed = (prior or {}).get("fingerprint") != fingerprint

    # Preserve the original timestamp when nothing changed (no churn).
    if not changed and prior:
        result["checked_at"] = prior.get("checked_at", result["checked_at"])

    # Persist (single object, overwritten in place — never appended).
    if state_path is not None:
        try:
            state = _read_state(state_path)
            state["reconciliation"] = result
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

    # Emit the activity-log event only on a genuine change (AC6 + AC9).
    if changed and emit_event is not None:
        try:
            emit_event(
                type="sprint_reconciled",
                target=sprint_label,
                actor="system",
                detail={"all_clear": result["all_clear"], "checks": result["checks"]},
                project=project or sprint_label,
                action_id=action_id,
            )
        except Exception:
            pass

    result["event_emitted"] = bool(changed and emit_event is not None)
    return result


# ── GitHub input gathering (best-effort, isolated from pure logic) ─────────────

def _gh_json(*args: str) -> Optional[object]:
    """Run a ``gh`` command expecting JSON on stdout; return parsed or None."""
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _pr_number_from_url(pr_url: Optional[str]) -> Optional[int]:
    if not pr_url:
        return None
    m = re.search(r"/pull/(\d+)", pr_url)
    return int(m.group(1)) if m else None


def gather_inputs_via_gh(
    sprint_label: str,
    repo: Optional[str],
    pr_url: Optional[str],
    ticket_numbers: Optional[list[int]] = None,
    *,
    mirror_reader: Optional[Callable] = None,
) -> dict:
    """Collect reconciliation inputs from GitHub. Best-effort; never raises.

    When *mirror_reader* is supplied (or the built-in ``github_client`` mirror
    is available), the ticket roster and summary issues are read from the local
    issues mirror instead of calling ``gh issue list`` and ``gh issue view`` per
    ticket.  The mirror may be up to 60 s stale — this staleness is acceptable
    for end-of-sprint reconciliation because the label transitions that matter
    (e.g. UAT vs. in-progress) occurred before the sprint finished.  Only the
    PR merge-state check makes a live subprocess call (≤1 total).

    When the mirror is unpopulated (empty or absent) the function falls back to
    the original ``gh issue list`` + per-ticket ``gh issue view`` path and
    produces an identical ``{summary_issues, pr_info, tickets}`` dict.

    Returns ``{"summary_issues", "pr_info", "tickets"}`` ready to feed
    :func:`run_reconciliation`. Any GitHub failure degrades to empty data so the
    reconciliation still runs (and reports the missing artifact).
    """
    summary_issues: list[dict] = []
    pr_info: Optional[dict] = None
    tickets: list[dict] = []

    repo_args = (["--repo", repo] if repo else [])

    # Resolve mirror_reader: use the injected callable, or try a dynamic import.
    _reader = mirror_reader
    if _reader is None and repo:
        try:
            import github_client as _gh_client  # noqa: PLC0415
            _reader = _gh_client._mirror_issues
        except Exception:
            _reader = None

    # Attempt mirror-backed reads (zero gh issue subprocess calls).
    mirror: Optional[list[dict]] = None
    if _reader is not None and repo:
        try:
            mirror = _reader(repo)
        except Exception:
            mirror = None

    if mirror:
        # Mirror is populated — read all issue data from it without subprocess calls.
        tnums = {int(n) for n in (ticket_numbers or [])}
        for iss in mirror:
            labels = iss.get("labels") or []
            label_names = [
                (l.get("name") if isinstance(l, dict) else l)
                for l in labels
            ]
            if sprint_label in label_names:
                summary_issues.append({
                    "number": iss.get("number"),
                    "title": iss.get("title") or "",
                    "labels": labels,
                })
            num = iss.get("number")
            if num is not None and int(num) in tnums:
                tickets.append({"number": num, "labels": labels})
    else:
        # Fallback: original gh-CLI path (N+1 calls for issue data).

        # 1) Summary issue(s) carrying this sprint's label (open or closed).
        issues = _gh_json(
            "issue", "list", *repo_args,
            "--label", sprint_label, "--state", "all",
            "--json", "number,title,labels", "--limit", "100",
        )
        if isinstance(issues, list):
            summary_issues = issues

        # 3) Sprint tickets and their current status labels.
        for num in (ticket_numbers or []):
            iss = _gh_json(
                "issue", "view", str(num), *repo_args, "--json", "number,labels",
            )
            if isinstance(iss, dict):
                tickets.append(iss)

    # 2) Sprint PR merge status + conflicting files when unmerged (always live).
    pr_number = _pr_number_from_url(pr_url)
    if pr_number is not None:
        pr = _gh_json(
            "pr", "view", str(pr_number), *repo_args,
            "--json", "number,state,mergeable,mergeStateStatus,files",
        )
        if isinstance(pr, dict):
            merged = (pr.get("state") == "MERGED")
            info: dict = {"number": pr.get("number", pr_number), "merged": merged}
            if not merged:
                conflicting = pr.get("mergeable") == "CONFLICTING" or \
                    pr.get("mergeStateStatus") == "DIRTY"
                info["reason"] = "merge conflict" if conflicting else "unmerged"
                if conflicting:
                    info["conflicting_files"] = [
                        f.get("path") for f in (pr.get("files") or []) if f.get("path")
                    ]
            pr_info = info

    return {"summary_issues": summary_issues, "pr_info": pr_info, "tickets": tickets}
