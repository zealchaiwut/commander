"""Event-emission helpers for the sprint manager.

These five functions were extracted from sprint_manager.py (issue #1275) to
isolate the event-emission concern. sprint_manager.py re-imports and re-exports
them so all existing call sites remain unmodified.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

# Replicate the constants that the functions need; they resolve to the same
# values as sprint_manager.py because the repo layout is identical.
REPO_ROOT = Path(__file__).parent.parent.parent
DASHBOARD_API_URL = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")


def _emit_sprint_lifecycle_event(
    type: str,
    target: str,
    actor: str,
    detail: dict,
    project: str,
    action_id: "str | None" = None,
) -> None:
    """Write one lifecycle event to the events table. Silently no-ops on any error."""
    # Lazy import avoids a circular dependency at module load time while still
    # picking up sprint_manager's _db_record_event / _RECORD_EVENT_AVAILABLE so
    # existing monkeypatches on sm._db_record_event continue to work in tests.
    import services.sprint_manager.sprint_manager as _sm  # noqa: PLC0415
    if not _sm._RECORD_EVENT_AVAILABLE or _sm._db_record_event is None:
        return
    try:
        _sm._db_record_event(
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


def _failure_event_detail(
    issue_num: int,
    agent_role: str,
    reason: str,
    category: "str | FailureCategory",
    *,
    cfg: "Optional[SprintConfig]" = None,
    gate: bool = False,
    sprint_label: "str | None" = None,
) -> dict:
    """Build a ticket_failed activity-log payload, enriched from the sidecar when present."""
    # Lazy import avoids circular dependency; picks up the live function so any
    # monkeypatch on sm._find_feature_branch still works.
    import services.sprint_manager.sprint_manager as _sm  # noqa: PLC0415
    branch = _sm._find_feature_branch(issue_num) or ""
    detail: dict = {
        "agent": (agent_role or "agent").upper(),
        "issue_num": issue_num,
        "reason": reason or "",
        "category": str(category),
        "branch": branch,
        "gate": gate,
    }
    if sprint_label:
        detail["sprint_label"] = sprint_label
    try:
        root = cfg.worktree_coder.parent if cfg is not None else REPO_ROOT
        sc_path = root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"
        if sc_path.exists():
            data = json.loads(sc_path.read_text(encoding="utf-8"))
            detail["failure_class"] = data.get("failure_class")
            detail["summary"] = data.get("summary")
            detail["sidecar"] = str(sc_path)
            failures = data.get("failures") or []
            if failures:
                f0 = failures[0]
                detail["error_type"] = f0.get("type")
                detail["location"] = f0.get("location")
                detail["message"] = f0.get("issue") or f0.get("message")
                detail["gate_name"] = data.get("gate")
            elif data.get("detail"):
                detail["message"] = str(data["detail"])[:500]
    except Exception:
        pass
    return detail


def _emit_ticket_failed(
    issue_num: int,
    agent_role: str,
    reason: str,
    category: "str | FailureCategory",
    *,
    project: str,
    action_id: "str | None" = None,
    cfg: "Optional[SprintConfig]" = None,
    gate: bool = False,
    sprint_label: "str | None" = None,
) -> None:
    """Emit a ticket_failed row so the Activity tab can surface failures without log digging."""
    _emit_sprint_lifecycle_event(
        type="ticket_failed",
        target=f"#{issue_num}",
        actor="system",
        detail=_failure_event_detail(
            issue_num, agent_role, reason, category,
            cfg=cfg, gate=gate, sprint_label=sprint_label,
        ),
        project=project,
        action_id=action_id,
    )


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
