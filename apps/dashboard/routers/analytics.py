"""Analytics cost endpoint — token usage per sprint/ticket/agent (issue #786).

All data is sourced from existing `total_tokens` fields in agent_runs and
token_usage tables. Zero new token-capture code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

# ── Path setup ───────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import db as _db  # noqa: E402
import projects as _projects_module  # noqa: E402
import settings_repo as _settings_repo  # noqa: E402

_PROJECTS_BASE = Path.home() / "dev"
_SIZES = ("S", "M", "L", "XL")

# ── Helpers mirrored from server.py (avoids circular import) ─────────────────

def _resolve_project_slug(slug: str) -> str:
    """Resolve slug to owner/repo string; raise 404 if not found."""
    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []
    matched = next(
        (p for p in all_projects
         if p["repo"].split("/")[-1] == slug or p["repo"] == slug),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Project '{slug}' not found")
    return matched["repo"]


def _project_root_path(repo: str) -> Path:
    slug = repo.split("/")[-1] if "/" in repo else repo
    return _PROJECTS_BASE / slug


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


# ── DB queries ────────────────────────────────────────────────────────────────

def _query_by_sprint() -> list[dict]:
    """Return token totals grouped by sprint_label, excluding NULL total_tokens."""
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            rows = conn.execute(
                """SELECT sprint_label, SUM(total_tokens) AS total_tokens
                   FROM agent_runs
                   WHERE total_tokens IS NOT NULL
                   GROUP BY sprint_label
                   ORDER BY sprint_label"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _query_by_ticket(limit: int = 10) -> list[dict]:
    """Return top-N issues by total token usage."""
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            rows = conn.execute(
                """SELECT issue_number, SUM(total_tokens) AS total_tokens
                   FROM agent_runs
                   WHERE total_tokens IS NOT NULL
                   GROUP BY issue_number
                   ORDER BY total_tokens DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _query_by_agent() -> list[dict]:
    """Return token totals grouped by agent role."""
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            rows = conn.execute(
                """SELECT agent, SUM(total_tokens) AS total_tokens
                   FROM agent_runs
                   WHERE total_tokens IS NOT NULL
                   GROUP BY agent
                   ORDER BY total_tokens DESC"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _query_trend_30d() -> list[dict]:
    """Return per-day token totals for the last 30 days."""
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            rows = conn.execute(
                """SELECT DATE(started_at) AS date, SUM(total_tokens) AS tokens
                   FROM agent_runs
                   WHERE total_tokens IS NOT NULL
                     AND started_at >= DATE('now', '-30 days')
                   GROUP BY DATE(started_at)
                   ORDER BY date"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _query_token_usage_by_model() -> list[dict]:
    """Return input/output token totals grouped by model_name from token_usage table."""
    try:
        with _db.get_conn() as conn:
            rows = conn.execute(
                """SELECT COALESCE(model_name, 'unknown') AS model_name,
                          COALESCE(SUM(input_tokens), 0) AS total_input,
                          COALESCE(SUM(output_tokens), 0) AS total_output
                   FROM token_usage
                   GROUP BY model_name"""
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Cost computation ──────────────────────────────────────────────────────────

def _blended_usd_per_token(price_map: dict) -> float:
    """Compute a blended $/total_token rate from token_usage + price_map.

    Uses actual input/output split from token_usage to weight the blended rate.
    Returns 0.0 when token_usage is empty or no model matches the price_map.
    """
    rows = _query_token_usage_by_model()
    total_in = total_out = 0
    cost_in = cost_out = 0.0
    for row in rows:
        model = (row.get("model_name") or "unknown").lower()
        entry = price_map.get(model) or price_map.get(model.split("-20")[0])
        if not entry:
            continue
        price_in = float(entry.get("in", 0)) / 1_000_000
        price_out = float(entry.get("out", 0)) / 1_000_000
        inp = int(row.get("total_input", 0) or 0)
        out = int(row.get("total_output", 0) or 0)
        total_in += inp
        total_out += out
        cost_in += inp * price_in
        cost_out += out * price_out
    total = total_in + total_out
    if total == 0:
        return 0.0
    return (cost_in + cost_out) / total


def _apply_cost(rows: list[dict], rate: float) -> list[dict]:
    """Add cost_usd field to each row using total_tokens * rate."""
    out = []
    for row in rows:
        d = dict(row)
        d["cost_usd"] = round(int(d.get("total_tokens") or 0) * rate, 6)
        out.append(d)
    return out


# ── Per-size averages ─────────────────────────────────────────────────────────

def _compute_per_size_avg(project_root: Path) -> dict[str, Any]:
    """Compute average total_tokens per issue size using estimates + agent_runs."""
    estimates_dir = _commander_dir(project_root) / "estimates"
    issue_to_size: dict[int, str] = {}

    if estimates_dir.exists():
        for est_file in estimates_dir.glob("issue-*.json"):
            try:
                data = json.loads(est_file.read_text(encoding="utf-8"))
                num = data.get("issue_number")
                size = data.get("size")
                if num is not None and size in _SIZES:
                    issue_to_size[int(num)] = size
            except Exception:
                continue

    # Aggregate total_tokens per issue from agent_runs.
    try:
        with _db.get_conn() as conn:
            _db._create_agent_runs_table(conn)
            rows = conn.execute(
                """SELECT issue_number, SUM(total_tokens) AS total_tokens
                   FROM agent_runs
                   WHERE total_tokens IS NOT NULL
                   GROUP BY issue_number"""
            ).fetchall()
        per_issue = {int(r["issue_number"]): int(r["total_tokens"]) for r in rows}
    except Exception:
        per_issue = {}

    buckets: dict[str, list[int]] = {sz: [] for sz in _SIZES}
    for issue_num, tokens in per_issue.items():
        size = issue_to_size.get(issue_num)
        if size in buckets:
            buckets[size].append(tokens)

    result: dict[str, Any] = {}
    for sz in _SIZES:
        vals = buckets[sz]
        result[sz] = {
            "count": len(vals),
            "avg_tokens": round(sum(vals) / len(vals)) if vals else None,
        }
    return result


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(tags=["analytics"])


@router.get("/api/projects/{slug}/analytics/cost")
def get_project_analytics_cost(slug: str):
    """GET /api/projects/{slug}/analytics/cost — token usage breakdown (issue #786).

    Returns token totals by sprint, by ticket (top 10), by agent, per-size averages,
    and a 30-day trend series. When a price_map is configured in settings, includes
    cost_usd estimates for each bucket; omits them otherwise.
    """
    repo = _resolve_project_slug(slug)
    project_root = _project_root_path(repo)

    # Load settings to get price_map.
    try:
        stored = _settings_repo.get_setting("app_config", project=repo)
    except Exception:
        stored = {}
    price_map: dict | None = stored.get("price_map") if isinstance(stored, dict) else None
    price_map_configured = isinstance(price_map, dict)

    by_sprint = _query_by_sprint()
    by_ticket = _query_by_ticket(limit=10)
    by_agent = _query_by_agent()
    trend_30d = _query_trend_30d()
    per_size_avg_tokens = _compute_per_size_avg(project_root)

    if price_map_configured:
        rate = _blended_usd_per_token(price_map)
        by_sprint = _apply_cost(by_sprint, rate)
        by_ticket = _apply_cost(by_ticket, rate)
        by_agent = _apply_cost(by_agent, rate)
    else:
        for row in by_sprint:
            row["cost_usd"] = None
        for row in by_ticket:
            row["cost_usd"] = None
        for row in by_agent:
            row["cost_usd"] = None

    return {
        "by_sprint": by_sprint,
        "by_ticket": by_ticket,
        "by_agent": by_agent,
        "trend_30d": trend_30d,
        "per_size_avg_tokens": per_size_avg_tokens,
        "price_map_configured": price_map_configured,
    }
