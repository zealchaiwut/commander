"""Sprint manager data classes — leaf module with no intra-package dependencies.

Contains IssueState, SprintState, GateResult, and SprintSummary.
All other sprint_manager modules may import from here without creating cycles.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from services.logging import log as structured_log


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    coder_model:          Optional[str] = None  # resolved coder model for this ticket (size-routed, issue #789)
    coder_backend:        Optional[str] = None  # resolved dispatch backend: 'claude-code' or 'cline' (issue #919)
    coder_routing_reason: Optional[str] = None  # routing reason for coder badge tooltip (issue #1403)
    coder_pid:            Optional[int] = None  # OS PID of the coder subprocess (issue #777)
    tester_pid:           Optional[int] = None  # OS PID of the tester subprocess (issue #777)
    coder_provider:       Optional[str] = None  # CCPROXY_PROFILE value, e.g. 'ICA' (issue #1673)

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
            "coder_model":        self.coder_model,
            "coder_backend":      self.coder_backend,
            "coder_routing_reason": self.coder_routing_reason,
            "coder_pid":          self.coder_pid,
            "tester_pid":         self.tester_pid,
            "coder_provider":     self.coder_provider,
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
        iss.coder_model = d.get("coder_model")
        iss.coder_backend = d.get("coder_backend")
        iss.coder_routing_reason = d.get("coder_routing_reason")
        iss.coder_pid = d.get("coder_pid")
        iss.tester_pid = d.get("tester_pid")
        iss.coder_provider = d.get("coder_provider")
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
    # Concurrent pipeline mode flag (issue #739) — surfaced to the dashboard so
    # the board can render dual active-agent cards + waiting-level state.
    pipeline_mode:           bool                = False
    # Post-sprint reconciliation result (issue #856) — {all_clear, checks[], ...}
    reconciliation:          Optional[dict]      = None
    summary_issue_url:         Optional[str]       = None
    # Concurrent scheduler slot config (issue #1412).  Persisted at sprint start
    # so a resumed or inspected sprint shows correct lane capacity and occupancy.
    max_coder_slots:         int                 = 1
    max_tester_slots:        int                 = 1
    active_coder_slots:      int                 = 0

    def __post_init__(self) -> None:
        # Not a dataclass field — excluded from to_dict/from_dict and serialization.
        # Issue #776: serialize save() calls across concurrent pipeline threads.
        self._save_lock: threading.Lock = threading.Lock()

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
            "pipeline_mode":             self.pipeline_mode,
            "reconciliation":            self.reconciliation,
            "summary_issue_url":           self.summary_issue_url,
            "max_coder_slots":           self.max_coder_slots,
            "max_tester_slots":          self.max_tester_slots,
            "active_coder_slots":        self.active_coder_slots,
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
        s.pipeline_mode            = bool(d.get("pipeline_mode", False))
        s.reconciliation           = d.get("reconciliation")
        s.summary_issue_url        = d.get("summary_issue_url")
        s.max_coder_slots          = int(d.get("max_coder_slots", 1))
        s.max_tester_slots         = int(d.get("max_tester_slots", 1))
        s.active_coder_slots       = int(d.get("active_coder_slots", 0))
        return s

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._save_lock:
                # Serialize to_dict() inside the lock so a concurrent thread
                # mutating self.issues cannot corrupt the snapshot (issue #776).
                payload = json.dumps(self.to_dict(), indent=2)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(payload, encoding="utf-8")
                # os.replace() is atomic on POSIX — readers never see a partial file.
                os.replace(tmp, path)
        except OSError as _e:
            structured_log.error("sprint_state_write_error", f"could not write state JSON: {_e}", path=str(path), exc=str(_e))


@dataclass
class GateResult:
    gate:    str
    passed:  bool
    skipped: bool = False
    output:  str  = ""
    # Optional failure-category override. When set on a failed gate, the dispatch
    # layer uses it instead of the default gate→category map — e.g. a missing
    # pytest binary is an ENV_ERROR (infra), not a PYTEST_FAIL (code defect).
    category: Optional[str] = None

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
