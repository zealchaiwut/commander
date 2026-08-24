"""Pydantic response models for Hermes-critical endpoints (issue #2066).

Covers:
  POST /api/sprints/run
  GET  /api/sprints/{sprint_label}/live
  GET  /api/dev-report
  POST /api/tickets/draft
  POST /api/tickets/create
  GET  /api/failures
  GET  /api/board

Any-typed fields are justified inline where used.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


# ── POST /api/sprints/run (202) ───────────────────────────────────────────────

class SprintRunResponse(BaseModel):
    ok: bool
    sprint_label: str
    pid: int
    log: str
    migrated_count: int
    migrate_from: Optional[list[int]] = None


# ── GET /api/sprints/{sprint_label}/live (200) ────────────────────────────────

class TicketRef(BaseModel):
    number: Optional[int] = None
    title: str = ""


class ActiveAgent(BaseModel):
    name: str
    model: Optional[str] = None
    pid: Optional[int] = None


class ActiveAgentEntry(BaseModel):
    name: str
    ticket: Optional[TicketRef] = None
    pid: Optional[int] = None


class LevelEntry(BaseModel):
    level: int
    total: int
    merged: int
    state: str


class LogLine(BaseModel):
    timestamp: str
    type: str
    message: str


class LiveIssue(BaseModel):
    # extra="allow" because the live issue dict picks up fields from estimates,
    # agent_runs, and future per-ticket enrichments that change independently.
    model_config = ConfigDict(extra="allow")

    number: Optional[int] = None
    title: str = ""
    status: str = "pending"
    agent_status: Optional[str] = None
    agent: Optional[str] = None
    elapsed_secs: Optional[int] = None
    size: Optional[str] = None
    minutes: Optional[int] = None
    dispatch_level: int = 0
    coder_model: Optional[str] = None
    coder_backend: Optional[str] = None
    coder_provider: Optional[str] = None
    tester_attempt_count: int = 0
    coder_attempt: int = 0
    pipeline_stage: str = "pending"
    category: Optional[str] = None
    failure_reason: Optional[str] = None


class SprintLiveResponse(BaseModel):
    # extra="allow" lets running_metrics() fields (fix_rounds, token_total,
    # agent_time_split, usd_per_token, token_cost_usd, agent_runs_present)
    # pass through without requiring exhaustive enumeration of optional keys.
    model_config = ConfigDict(extra="allow")

    time_spent_sec: int
    started_at: Optional[str] = None
    current_ticket: Optional[TicketRef] = None
    active_agent: Optional[ActiveAgent] = None
    active_agents: list[ActiveAgentEntry] = []
    pipeline_mode: bool = False
    max_coder_slots: int = 1
    max_tester_slots: int = 1
    active_coder_slots: int = 0
    active_tester_slots: int = 0
    levels: list[LevelEntry] = []
    recent_log_lines: list[LogLine] = []
    done_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    pending_count: int = 0
    total_count: int = 0
    complete_count: int = 0
    est_remaining_minutes: Optional[int] = None
    issues: list[LiveIssue] = []
    llm_provider: Optional[str] = None
    fix_round_max: int = 3


# ── GET /api/dev-report (200) ─────────────────────────────────────────────────

class DevReportResponse(BaseModel):
    # extra="allow" in case future build_contract() keys are added.
    model_config = ConfigDict(extra="allow")

    for_date: str
    generated_at: str
    window_start: str
    window_end: str
    # projects: per-project entry dicts whose shape is defined by
    # export_hermes_report._build_project_entry() and changes independently
    # of this endpoint — Any is justified.
    projects: list[Any]
    cost: str
    cost_source: str
    completed: list[str]
    needs_review: list[str]
    dead_letter: list[str]


# ── POST /api/tickets/draft (200) ────────────────────────────────────────────

class TicketDraftResponse(BaseModel):
    draft_id: str
    title: str
    body: str


# ── POST /api/tickets/create (201) ───────────────────────────────────────────

class TicketCreateResponse(BaseModel):
    number: int
    url: str


# ── GET /api/failures (200) ──────────────────────────────────────────────────

class FailureRow(BaseModel):
    source: str
    issue_number: Optional[int] = None
    sprint_label: Optional[str] = None
    project: Optional[str] = None
    agent: Optional[str] = None
    category: Optional[str] = None
    reason: Optional[str] = None
    failure_class: Optional[str] = None
    message: Optional[str] = None
    attempt_kind: Optional[str] = None
    branch: Optional[str] = None
    log_url: Optional[str] = None
    ts: Optional[str] = None


# ── GET /api/board (200) ─────────────────────────────────────────────────────

class BoardBacklog(BaseModel):
    count: int
    # GitHub issue dicts whose shape is governed by the GH REST API and
    # varies across labels/states — Any is justified.
    tickets: list[Any]


class BoardSections(BaseModel):
    # Sprint cards carry lifecycle-dependent inline fields (tickets, run_stats,
    # mini_rail, dep_order, outcome, conflicts, finish_card, branch_status,
    # etc.) that change across lifecycle states — Any is justified per card.
    running: list[Any] = []
    needs_rework: list[Any] = []
    ready_to_merge: list[Any] = []
    draft: list[Any] = []
    lineage: list[Any] = []
    backlog: BoardBacklog


class BoardCacheInfo(BaseModel):
    hit: bool
    ttl_s: float


class BoardSizeMins(BaseModel):
    S: int
    M: int
    L: int
    XL: int


class BoardCapacity(BaseModel):
    budget_minutes: int
    size_minutes: BoardSizeMins


class BoardResponse(BaseModel):
    project: str
    generated_at: str
    sections: BoardSections
    capacity: BoardCapacity
    # per-sprint summary rows keyed by sprint label; values include
    # summary_issue_url, pr_number, lifecycle_state — dict[str, Any] because
    # the summary shape can gain new keys in sprint_history_service.
    summaries: dict[str, Any]
    cache: BoardCacheInfo
