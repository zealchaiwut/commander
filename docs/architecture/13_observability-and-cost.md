# 13. Observability & cost

*The telemetry tiers we ranked.*

[← Contents](0_content.md) · [← Prev: Security & secrets](12_security-and-secrets.md) · [Next: Roadmap & sequencing →](14_roadmap-and-sequencing.md)

## 13.1 Agent/role metrics

Model, duration, tokens — the **#1 ranked tier**.

Captured at dispatch time (`agent_runs` row: `model_used`, `routing_reason`, `attempt_kind`). Estimate-based routing (#789) feeds model selection into this record.

## 13.2 Per-card stats

Timing, retries, which agent.

Fix-loop history (`_fix_history` per ticket) + `RETRY_EXHAUSTED` tagging. Sprint history pane reads from multiple sources today — consolidation is part of the lifecycle redesign ([sprint-lifecycle.md](sprint-lifecycle.md)).

## 13.3 Logs & events

Live stream + history.

**Structured logging** ([2.2b](2_app-dashboard-architecture.md#22b-backend-logging--structured-logger-disk-first-phase-1-neon-later-phase-2)):

| Phase | Sink | Status |
|-------|------|--------|
| Phase 1 | Disk (`services/logging.py`, JSON-lines + prd.log) | Landed |
| Phase 2 | Neon `run_events` + `ticket_events` | Pending — **#2 ranked tier** |

Four legacy surfaces (per-run file, per-issue file, alert log, SQLite `events`) still coexist during transition. **Open:** file-tail vs true SSE live-stream for the Live View panel — settle in [2.3b](2_app-dashboard-architecture.md#23b-frontend-sitemap--pageapi-binding--live-log--pending).

Correlation key: `run_id` (parent) + `issue_num` (child). Every invocation — sprint and manual — must mint a `run_id`.

## 13.4 Cost visibility

Token tracking, the dropped cost dashboard.

`token_usage` table with `agent_role` + `model_name`. Sprint summaries show `cost_estimate` ($0.00 when subscription-funded). Audit: `GET /api/debug/token-usage/by-agent-model`. Estimate-based coder routing ([4e](4_agents.md)) is cost-relevant post-June-15.

## 13.5 Sprint history & analytics

_TODO — depends on lifecycle source-of-truth consolidation (section 1 + [sprint-lifecycle.md](sprint-lifecycle.md))._
