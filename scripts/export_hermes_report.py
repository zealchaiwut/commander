#!/usr/bin/env python3
"""Nightly Hermes dev-report contract exporter (issue #1959).

Queries the Commander DB and analytics layer and writes a versioned JSON
contract that Hermes can consume.  Degrades gracefully on failure: logs to
stderr, exits 0, leaves any existing output file untouched.

Usage:
    python3 scripts/export_hermes_report.py [--dry-run] [--output PATH]
        [--db-path PATH] [--stale-blocked-days N] [--stale-waiting-days N]
        [--stale-backlog-days N]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_BKK = ZoneInfo("Asia/Bangkok")
_SCOPE = "dev_report"
_STATE_FILENAME = ".commander_export_state.json"

_BLOCKED_LABELS = frozenset({
    "rework", "blocked", "failed", "sit-failed", "returned-from-qa", "returned",
})
_DONE_LABELS = frozenset({"done", "uat"})
_WAITING_LABELS = frozenset({"uat"})
_PLANNING_STATES = frozenset({"draft", "planned", "planning"})

_DEFAULT_STALE_BLOCKED_DAYS = 3
_DEFAULT_STALE_WAITING_DAYS = 2
_DEFAULT_STALE_BACKLOG_DAYS = 7

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"


# ── Path resolution ───────────────────────────────────────────────────────────

def _resolve_output_path(output_flag: str | None) -> Path:
    """Resolve output path per AC12 resolution order."""
    if output_flag:
        return Path(output_flag)
    env = os.environ.get("COMMANDER_REPORT_PATH", "").strip()
    if env:
        return Path(env)
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return Path(hermes_home) / "contracts" / "commander_report.latest.json"
    return Path.home() / ".hermes" / "contracts" / "commander_report.latest.json"


def _resolve_db_path(db_path_flag: str | None) -> str | None:
    """Resolve db_path from --db-path flag or $DB_PATH env var (AC13)."""
    if db_path_flag:
        return db_path_flag
    env = os.environ.get("DB_PATH", "").strip()
    return env or None


# ── Bangkok date ──────────────────────────────────────────────────────────────

def _bkk_date(now: datetime) -> str:
    """Return Asia/Bangkok date string for the given UTC datetime (AC1)."""
    return now.astimezone(_BKK).date().isoformat()


# ── DB helpers ────────────────────────────────────────────────────────────────

def _open_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return conn


def _label_names(labels_json: str) -> frozenset[str]:
    try:
        items = json.loads(labels_json) if labels_json else []
        return frozenset(
            (item["name"] if isinstance(item, dict) else str(item)) for item in items
        )
    except Exception:
        return frozenset()


def _query_issues(conn: sqlite3.Connection, repo: str) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT issue_number, title, state, labels, updated_at "
            "FROM issues WHERE repo = ?",
            (repo,),
        ).fetchall()
    except Exception:
        return []
    result = []
    for r in rows:
        result.append({
            "issue_number": int(r["issue_number"]),
            "title": r["title"] or f"#{r['issue_number']}",
            "state": r["state"] or "open",
            "label_names": _label_names(r["labels"]),
            "updated_at": r["updated_at"] or "",
        })
    return result


def _query_sprints(conn: sqlite3.Connection, project_key: str) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT label, state, created_at, started_at, ended_at "
            "FROM sprints WHERE project = ?",
            (project_key,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _query_token_usage(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT COALESCE(model_name, 'unknown') AS model_name, "
            "COALESCE(SUM(input_tokens), 0) AS total_input, "
            "COALESCE(SUM(output_tokens), 0) AS total_output "
            "FROM token_usage GROUP BY model_name"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Cost ──────────────────────────────────────────────────────────────────────

def _compute_cost(
    conn: sqlite3.Connection, price_map: dict | None
) -> tuple[str, str]:
    """Return (cost_str, cost_source) per AC8."""
    rows = _query_token_usage(conn)

    if price_map:
        try:
            total_usd = 0.0
            matched = False
            for row in rows:
                model = (row["model_name"] or "unknown").lower()
                entry = price_map.get(model) or price_map.get(model.split("-20")[0])
                if not entry:
                    continue
                matched = True
                price_in = float(entry.get("in", 0)) / 1_000_000
                price_out = float(entry.get("out", 0)) / 1_000_000
                total_usd += int(row["total_input"] or 0) * price_in
                total_usd += int(row["total_output"] or 0) * price_out
            if matched:
                return f"${total_usd:.2f}", "price_map"
        except Exception:
            pass

    try:
        total = sum(
            int(r["total_input"] or 0) + int(r["total_output"] or 0) for r in rows
        )
        if total > 0:
            return f"{total} tokens", "token_count"
    except Exception:
        pass

    return "unknown", "unknown"


# ── Stale detection ───────────────────────────────────────────────────────────

def _age_days(ts_str: str, now: datetime) -> float | None:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return None


def _compute_stale(
    issues: list[dict],
    sprints: list[dict],
    stale_blocked_days: int,
    stale_waiting_days: int,
    stale_backlog_days: int,
    now: datetime,
) -> list[dict]:
    """Classify stale items across three kinds (AC5)."""
    stale: list[dict] = []

    for issue in issues:
        if issue["state"] != "open":
            continue
        if not (issue["label_names"] & _BLOCKED_LABELS):
            continue
        age = _age_days(issue["updated_at"], now)
        if age is not None and age >= stale_blocked_days:
            stale.append({
                "kind": "blocked",
                "issue_number": issue["issue_number"],
                "title": issue["title"],
                "age_days": round(age, 1),
            })

    for sprint in sprints:
        if sprint.get("state") != "ready_to_merge":
            continue
        age = _age_days(sprint.get("ended_at") or "", now)
        if age is not None and age >= stale_waiting_days:
            stale.append({
                "kind": "waiting_signoff",
                "sprint_label": sprint["label"],
                "age_days": round(age, 1),
            })

    for sprint in sprints:
        if sprint.get("state") not in _PLANNING_STATES:
            continue
        age = _age_days(sprint.get("created_at") or "", now)
        if age is not None and age >= stale_backlog_days:
            stale.append({
                "kind": "backlog",
                "sprint_label": sprint["label"],
                "age_days": round(age, 1),
            })

    return stale


# ── State (fixed-detection persistence) ──────────────────────────────────────

def _load_state(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state_atomic(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)


def _compute_fixed(
    repo: str,
    issues: list[dict],
    prev_state: dict,
    current_blocked: frozenset[int],
) -> list[dict]:
    """Issues that were blocked in the previous run but are resolved now (AC6)."""
    prev_blocked = frozenset(
        int(n) for n in prev_state.get(repo, {}).get("blocked_issue_numbers", [])
    )
    fixed_numbers = prev_blocked - current_blocked
    title_map = {i["issue_number"]: i["title"] for i in issues}
    return [
        {"issue_number": n, "title": title_map.get(n, f"#{n}")}
        for n in sorted(fixed_numbers)
    ]


# ── Per-project status ────────────────────────────────────────────────────────

def _project_status(
    shipped: list[dict],
    in_progress: dict | None,
    blocked_numbers: frozenset[int],
    stale: list[dict],
) -> str:
    if shipped:
        return "shipped"
    if in_progress is not None:
        return "in_progress"
    if blocked_numbers:
        return "blocked"
    if any(s["kind"] == "waiting_signoff" for s in stale):
        return "waiting_signoff"
    return "idle"


# ── Per-project entry builder ──────────────────────────────────────────────────

def _build_project_entry(
    project: dict,
    conn: sqlite3.Connection,
    now: datetime,
    prev_state: dict,
    stale_blocked_days: int,
    stale_waiting_days: int,
    stale_backlog_days: int,
) -> tuple[dict, dict]:
    """Return (entry, project_state_for_save)."""
    repo = project.get("repo", "")
    name = project.get("name", repo.split("/")[-1] if "/" in repo else repo)

    issues = _query_issues(conn, repo)
    sprints = _query_sprints(conn, repo)

    blocked_numbers = frozenset(
        i["issue_number"]
        for i in issues
        if i["state"] == "open" and (i["label_names"] & _BLOCKED_LABELS)
    )

    stale = _compute_stale(
        issues, sprints, stale_blocked_days, stale_waiting_days, stale_backlog_days, now
    )

    shipped = [
        {"issue_number": i["issue_number"], "title": i["title"]}
        for i in issues
        if i["label_names"] & _DONE_LABELS
    ]

    waiting = [
        {"issue_number": i["issue_number"], "title": i["title"]}
        for i in issues
        if i["state"] == "open" and (i["label_names"] & _WAITING_LABELS)
    ]

    in_progress_sprint = next(
        (
            {"sprint_label": s["label"], "started_at": s.get("started_at")}
            for s in sprints
            if s.get("state") == "running"
        ),
        None,
    )

    fixed = _compute_fixed(repo, issues, prev_state, blocked_numbers)

    status = _project_status(shipped, in_progress_sprint, blocked_numbers, stale)

    entry = {
        "name": name,
        "status": status,
        "in_progress": in_progress_sprint,
        "shipped": shipped,
        "fixed": fixed,
        "stale": stale,
        "waiting": waiting,
        "counts": {
            "shipped": len(shipped),
            "in_progress": 1 if in_progress_sprint else 0,
            "blocked": len(blocked_numbers),
            "waiting": len(waiting),
            "stale": len(stale),
            "fixed": len(fixed),
        },
    }

    proj_state = {"blocked_issue_numbers": sorted(blocked_numbers)}
    return entry, proj_state


# ── Brief artifacts persistence ───────────────────────────────────────────────

def _persist_brief_artifact(
    conn: sqlite3.Connection, date: str, payload: dict, generated_at: str
) -> None:
    """Upsert the dev_report payload into brief_artifacts (AC10)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS brief_artifacts (
            scope        TEXT NOT NULL,
            project      TEXT NOT NULL DEFAULT '',
            date         TEXT NOT NULL,
            payload      TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (scope, project, date)
        )
    """)
    conn.execute(
        "INSERT OR REPLACE INTO brief_artifacts "
        "(scope, project, date, payload, generated_at) VALUES (?, ?, ?, ?, ?)",
        (_SCOPE, "", date, json.dumps(payload), generated_at),
    )
    conn.commit()


# ── Atomic write ──────────────────────────────────────────────────────────────

def _write_atomic(path: Path, payload: dict) -> None:
    """Write payload JSON atomically via tmp file + os.replace (AC9)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ── Legacy price-map loader ────────────────────────────────────────────────────

def _load_price_map_from_db(conn: sqlite3.Connection) -> dict | None:
    """Try to read price_map from settings_kv table in the DB."""
    try:
        row = conn.execute(
            "SELECT value FROM settings_kv WHERE key = 'app_config' LIMIT 1"
        ).fetchone()
        if row:
            cfg = json.loads(row["value"])
            if isinstance(cfg, dict) and isinstance(cfg.get("price_map"), dict):
                return cfg["price_map"]
    except Exception:
        pass
    return None


# ── Contract builder ──────────────────────────────────────────────────────────

_UNSET: Any = object()


def build_contract(
    db_path: str,
    *,
    now: datetime | None = None,
    projects_list: list[dict] | None = None,
    price_map: Any = _UNSET,
    prev_state: dict | None = None,
    stale_blocked_days: int = _DEFAULT_STALE_BLOCKED_DAYS,
    stale_waiting_days: int = _DEFAULT_STALE_WAITING_DAYS,
    stale_backlog_days: int = _DEFAULT_STALE_BACKLOG_DAYS,
) -> dict:
    """Build the full Hermes dev-report contract.

    Returns the contract dict plus ``_new_state`` (internal key used by
    callers to persist state for the next run's fixed-detection).

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database.
    now:
        UTC datetime for "now".  Defaults to ``datetime.now(timezone.utc)``.
        Injected by tests for determinism.
    projects_list:
        Override project list (skips ``projects.json`` read).  Tests inject
        this directly; production callers pass ``None`` to auto-load.
    price_map:
        Override price map.  Pass ``None`` to skip cost; pass ``_UNSET``
        (default) to auto-load from ``settings_kv``.
    prev_state:
        Previous run's state dict (for fixed-detection).  ``None`` → empty.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if prev_state is None:
        prev_state = {}

    conn = _open_conn(db_path)
    try:
        # Auto-load price_map if not overridden
        resolved_price_map: dict | None
        if price_map is _UNSET:
            resolved_price_map = _load_price_map_from_db(conn)
        else:
            resolved_price_map = price_map

        # Auto-load projects if not injected
        if projects_list is None:
            projects_list = _auto_load_projects()

        for_date = _bkk_date(now)
        generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        bkk_midnight = now.astimezone(_BKK).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        window_start = bkk_midnight.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        window_end = generated_at

        project_entries: list[dict] = []
        new_state: dict = {}

        for proj in projects_list:
            entry, proj_state = _build_project_entry(
                proj, conn, now, prev_state,
                stale_blocked_days, stale_waiting_days, stale_backlog_days,
            )
            new_state[proj.get("repo", "")] = proj_state
            project_entries.append(entry)

        cost_str, cost_source = _compute_cost(conn, resolved_price_map)

        # Legacy rollup keys (AC4): flat "name: description" strings
        completed: list[str] = []
        needs_review: list[str] = []
        dead_letter: list[str] = []

        for entry in project_entries:
            pname = entry["name"]
            if entry["shipped"]:
                completed.append(f"{pname}: {len(entry['shipped'])} shipped")
            if entry["waiting"]:
                needs_review.append(
                    f"{pname}: {len(entry['waiting'])} awaiting sign-off"
                )
            stale_blocked = [s for s in entry["stale"] if s["kind"] == "blocked"]
            if stale_blocked:
                dead_letter.append(
                    f"{pname}: {len(stale_blocked)} stale blocked"
                )

        return {
            "for_date": for_date,
            "generated_at": generated_at,
            "window_start": window_start,
            "window_end": window_end,
            "projects": project_entries,
            "cost": cost_str,
            "cost_source": cost_source,
            "completed": completed,
            "needs_review": needs_review,
            "dead_letter": dead_letter,
            "_new_state": new_state,
        }
    finally:
        conn.close()


def _auto_load_projects() -> list[dict]:
    """Load projects from apps/dashboard/projects.json."""
    projects_file = _DASHBOARD_DIR / "projects.json"
    if projects_file.exists():
        try:
            return json.loads(projects_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


# ── run() — testable entry point ─────────────────────────────────────────────

def run(
    db_path: str,
    output_path: Path,
    state_path: Path,
    dry_run: bool,
    projects_list: list[dict] | None,
    price_map: Any,
    stale_blocked_days: int,
    stale_waiting_days: int,
    stale_backlog_days: int,
) -> None:
    """Build the contract and write outputs (or dry-run print).

    On any exception: logs to stderr, returns without modifying any file (AC11).
    """
    try:
        if not Path(db_path).exists():
            raise FileNotFoundError(f"DB not found: {db_path}")

        prev_state = _load_state(state_path)

        contract = build_contract(
            db_path,
            projects_list=projects_list,
            price_map=price_map,
            prev_state=prev_state,
            stale_blocked_days=stale_blocked_days,
            stale_waiting_days=stale_waiting_days,
            stale_backlog_days=stale_backlog_days,
        )
        new_state = contract.pop("_new_state", {})

        if dry_run:
            sys.stdout.write(json.dumps(contract, indent=2, ensure_ascii=False) + "\n")
            return

        _write_atomic(output_path, contract)
        _save_state_atomic(state_path, new_state)

        conn = _open_conn(db_path)
        try:
            _persist_brief_artifact(
                conn, contract["for_date"], contract, contract["generated_at"]
            )
        finally:
            conn.close()

    except Exception as exc:
        sys.stderr.write(f"export_hermes_report: {exc}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nightly Hermes dev-report contract exporter"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print JSON to stdout without writing any files",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (overrides $COMMANDER_REPORT_PATH)",
    )
    parser.add_argument(
        "--db-path", default=None,
        help="SQLite DB path (overrides $DB_PATH)",
    )
    parser.add_argument(
        "--stale-blocked-days", type=int, default=_DEFAULT_STALE_BLOCKED_DAYS,
        help="Days before a blocked/rework issue is considered stale",
    )
    parser.add_argument(
        "--stale-waiting-days", type=int, default=_DEFAULT_STALE_WAITING_DAYS,
        help="Days before a ready_to_merge sprint is considered stale",
    )
    parser.add_argument(
        "--stale-backlog-days", type=int, default=_DEFAULT_STALE_BACKLOG_DAYS,
        help="Days before a planning-state sprint is considered stale backlog",
    )
    args = parser.parse_args()

    db_path = _resolve_db_path(args.db_path)
    if not db_path:
        sys.stderr.write(
            "export_hermes_report: no DB path — set --db-path or $DB_PATH\n"
        )
        sys.exit(0)

    output_path = _resolve_output_path(args.output)
    state_path = output_path.parent / _STATE_FILENAME

    run(
        db_path=db_path,
        output_path=output_path,
        state_path=state_path,
        dry_run=args.dry_run,
        projects_list=None,
        price_map=_UNSET,
        stale_blocked_days=args.stale_blocked_days,
        stale_waiting_days=args.stale_waiting_days,
        stale_backlog_days=args.stale_backlog_days,
    )


if __name__ == "__main__":
    main()
