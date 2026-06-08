#!/usr/bin/env python3
"""Generate a daily summary report for Commander.

Writes .commander/reports/YYYY-MM-DD.md from structured logger files (primary)
or sprint state files (fallback) when logs are unavailable.

Usage:
    python3 scripts/generate_daily_report.py [--date YYYY-MM-DD] [--db-path PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def _resolve_commander_dir() -> Path:
    """Walk up from cwd to find .commander/, fall back to cwd/.commander/."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".commander"
        if candidate.is_dir():
            return candidate
    return cwd / ".commander"


def _categorize_error(entry: dict) -> str:
    """Return TIMEOUT / TOOL_ERROR / LLM_REFUSAL / UNKNOWN from a log entry."""
    msg = (entry.get("message") or "").lower()
    event = (entry.get("event") or "").lower()
    exit_code = entry.get("exit_code")
    subprocess_stderr = (entry.get("subprocess_stderr") or "").lower()

    if (
        exit_code == 124
        or "timeout" in msg
        or "timed out" in msg
        or "timeout" in event
        or "deadline" in msg
    ):
        return "TIMEOUT"
    if "refusal" in msg or "cannot assist" in msg or "i'm unable" in msg:
        return "LLM_REFUSAL"
    if (
        "subprocess_nonzero_exit" in event
        or "tool_error" in event
        or "non-zero" in msg
        or "exit_code" in entry
    ):
        return "TOOL_ERROR"
    return "UNKNOWN"


def _categorize_from_failure_reason(reason: str) -> str:
    r = reason.lower()
    if "timeout" in r or "timed out" in r:
        return "TIMEOUT"
    if "refusal" in r or "refuse" in r or "cannot assist" in r:
        return "LLM_REFUSAL"
    if "tool" in r or "subprocess" in r or "exit" in r:
        return "TOOL_ERROR"
    return "UNKNOWN"


def _load_structured_log(log_dir: Path, target_date: date) -> list[dict]:
    """Load JSON-line entries from structured-YYYY-MM-DD.log."""
    log_file = log_dir / f"structured-{target_date.isoformat()}.log"
    if not log_file.exists():
        return []
    entries: list[dict] = []
    with open(log_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _parse_structured_logs(entries: list[dict]) -> dict:
    sprints_seen: set[str] = set()
    tickets_shipped: dict[int, dict] = {}
    tickets_failed: dict[int, dict] = {}
    ticket_first_ts: dict[int, datetime] = {}
    ticket_last_ts: dict[int, datetime] = {}

    for entry in entries:
        ts_str = entry.get("ts", "")
        issue_num = entry.get("issue_num")
        sprint_label = entry.get("sprint_label")
        event = entry.get("event", "")
        level = entry.get("level", "")
        status = entry.get("status")

        if sprint_label:
            sprints_seen.add(sprint_label)

        if issue_num is not None and ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if issue_num not in ticket_first_ts or ts < ticket_first_ts[issue_num]:
                    ticket_first_ts[issue_num] = ts
                if issue_num not in ticket_last_ts or ts > ticket_last_ts[issue_num]:
                    ticket_last_ts[issue_num] = ts
            except Exception:
                pass

        if event == "ticket_update_start" and status == "uat" and issue_num is not None:
            tickets_shipped[issue_num] = {
                "issue_num": issue_num,
                "title": f"#{issue_num}",
                "sprint_label": sprint_label or "unknown",
            }
            tickets_failed.pop(issue_num, None)

        if (
            level == "ERROR"
            and issue_num is not None
            and issue_num not in tickets_shipped
        ):
            if issue_num not in tickets_failed:
                tickets_failed[issue_num] = {
                    "issue_num": issue_num,
                    "title": f"#{issue_num}",
                    "sprint_label": sprint_label or "unknown",
                    "error_cat": _categorize_error(entry),
                }

    durations = []
    all_issue_nums = set(ticket_first_ts) | set(ticket_last_ts)
    for num in all_issue_nums:
        if num in ticket_first_ts and num in ticket_last_ts:
            delta = (ticket_last_ts[num] - ticket_first_ts[num]).total_seconds()
            if delta > 0:
                durations.append(delta)

    avg_duration_min = (
        round(sum(durations) / len(durations) / 60, 1) if durations else None
    )

    return {
        "source": "structured_logs",
        "sprints_completed": sorted(sprints_seen),
        "tickets_shipped": list(tickets_shipped.values()),
        "tickets_failed": list(tickets_failed.values()),
        "avg_duration_min": avg_duration_min,
        "ticket_count": len(all_issue_nums),
    }


def _parse_state_files(sprints_dir: Path, target_date: date) -> dict:
    sprints_completed: list[str] = []
    tickets_shipped: list[dict] = []
    tickets_failed: list[dict] = []
    durations: list[float] = []

    if not sprints_dir.exists():
        return {
            "source": "state_files",
            "sprints_completed": [],
            "tickets_shipped": [],
            "tickets_failed": [],
            "avg_duration_min": None,
            "ticket_count": 0,
        }

    for state_file in sorted(sprints_dir.glob("sprint-*-state.json")):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        start_str = data.get("start_timestamp", "")
        if not start_str:
            continue
        try:
            start_dt = datetime.fromisoformat(start_str.rstrip("Z")).replace(
                tzinfo=timezone.utc
            )
        except Exception:
            continue

        wall_clock = float(data.get("wall_clock_secs") or 0.0)
        end_dt = start_dt + timedelta(seconds=wall_clock)

        started_today = start_dt.date() == target_date
        ended_today = end_dt.date() == target_date and wall_clock > 0

        if not (started_today or ended_today):
            continue

        sprint_label = data.get(
            "sprint_label", state_file.stem.replace("-state", "")
        )
        sprints_completed.append(sprint_label)

        for issue in data.get("issues", []):
            issue_num = issue.get("number")
            title = issue.get("title") or f"#{issue_num}"
            status = issue.get("status")
            failure_reason = issue.get("failure_reason") or ""

            if status == "done":
                tickets_shipped.append({
                    "issue_num": issue_num,
                    "title": title,
                    "sprint_label": sprint_label,
                })
            elif status == "failed":
                tickets_failed.append({
                    "issue_num": issue_num,
                    "title": title,
                    "sprint_label": sprint_label,
                    "error_cat": _categorize_from_failure_reason(failure_reason),
                })

            coder_start = issue.get("coder_started_at")
            tester_end = issue.get("tester_finished_at") or issue.get("coder_finished_at")
            if coder_start and tester_end:
                try:
                    t0 = datetime.fromisoformat(coder_start.rstrip("Z")).replace(
                        tzinfo=timezone.utc
                    )
                    t1 = datetime.fromisoformat(tester_end.rstrip("Z")).replace(
                        tzinfo=timezone.utc
                    )
                    delta = (t1 - t0).total_seconds()
                    if delta > 0:
                        durations.append(delta)
                except Exception:
                    pass

    avg_duration_min = (
        round(sum(durations) / len(durations) / 60, 1) if durations else None
    )
    ticket_count = len(tickets_shipped) + len(tickets_failed)

    return {
        "source": "state_files",
        "sprints_completed": sorted(sprints_completed),
        "tickets_shipped": tickets_shipped,
        "tickets_failed": tickets_failed,
        "avg_duration_min": avg_duration_min,
        "ticket_count": ticket_count,
    }


def _query_token_usage(db_path: str, target_date: date) -> dict | None:
    from_str = f"{target_date.isoformat()}T00:00:00"
    to_str = f"{target_date.isoformat()}T23:59:59"
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT SUM(input_tokens) AS total_in, SUM(output_tokens) AS total_out "
            "FROM token_usage WHERE recorded_at >= ? AND recorded_at <= ?",
            (from_str, to_str),
        ).fetchone()
        conn.close()
        if row:
            return {
                "input_tokens": int(row["total_in"] or 0),
                "output_tokens": int(row["total_out"] or 0),
            }
    except Exception:
        pass
    return None


def _token_fallback_from_states(sprints_dir: Path, target_date: date) -> dict | None:
    if not sprints_dir.exists():
        return None
    total_in = 0
    total_out = 0
    for sf in sprints_dir.glob("sprint-*-state.json"):
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
            start_str = data.get("start_timestamp", "")
            if not start_str:
                continue
            start_dt = datetime.fromisoformat(start_str.rstrip("Z")).replace(
                tzinfo=timezone.utc
            )
            if start_dt.date() == target_date:
                total_in += int(data.get("total_tokens_in") or 0)
                total_out += int(data.get("total_tokens_out") or 0)
        except Exception:
            continue
    if total_in > 0 or total_out > 0:
        return {"input_tokens": total_in, "output_tokens": total_out}
    return None


def _render_report(
    target_date: date,
    data: dict,
    token_data: dict | None,
    generated_at: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# Daily Summary — {target_date.isoformat()}")
    lines.append("")
    lines.append(f"> Generated at {generated_at}")

    if data["source"] == "state_files":
        lines.append(">")
        lines.append("> Warning: logger unavailable, data sourced from state files")

    lines.append("")

    # Sprints Completed
    lines.append("## Sprints Completed")
    lines.append("")
    if data["sprints_completed"]:
        lines.append("| Sprint |")
        lines.append("|--------|")
        for s in data["sprints_completed"]:
            lines.append(f"| {s} |")
    else:
        lines.append("No activity recorded")
    lines.append("")

    # Tickets Shipped
    lines.append("## Tickets Shipped")
    lines.append("")
    if data["tickets_shipped"]:
        lines.append("| # | Title | Sprint |")
        lines.append("|---|-------|--------|")
        for t in data["tickets_shipped"]:
            num = t.get("issue_num", "?")
            title = str(t.get("title", f"#{num}")).replace("|", "\\|")
            sprint = t.get("sprint_label", "unknown")
            lines.append(f"| {num} | {title} | {sprint} |")
    else:
        lines.append("No activity recorded")
    lines.append("")

    # Failures
    lines.append("## Failures")
    lines.append("")
    if data["tickets_failed"]:
        lines.append("| # | Title | Sprint | Error category |")
        lines.append("|---|-------|--------|----------------|")
        for t in data["tickets_failed"]:
            num = t.get("issue_num", "?")
            title = str(t.get("title", f"#{num}")).replace("|", "\\|")
            sprint = t.get("sprint_label", "unknown")
            cat = t.get("error_cat", "UNKNOWN")
            lines.append(f"| {num} | {title} | {sprint} | `{cat}` |")
    else:
        lines.append("No activity recorded")
    lines.append("")

    # Token Usage
    lines.append("## Token Usage")
    lines.append("")
    if token_data and (token_data["input_tokens"] > 0 or token_data["output_tokens"] > 0):
        total_in = token_data["input_tokens"]
        total_out = token_data["output_tokens"]
        total = total_in + total_out
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Input tokens | {total_in:,} |")
        lines.append(f"| Output tokens | {total_out:,} |")
        lines.append(f"| Total tokens | {total:,} |")
    else:
        lines.append("No activity recorded")
    lines.append("")

    # Average Ticket Duration
    lines.append("## Average Ticket Duration")
    lines.append("")
    if data["avg_duration_min"] is not None:
        lines.append(
            f"**{data['avg_duration_min']} minutes** "
            f"(across {data['ticket_count']} ticket(s))"
        )
    else:
        lines.append("No activity recorded")
    lines.append("")

    lines.append("---")
    lines.append("*Report generated by Commander daily report generator*")
    lines.append("")

    return "\n".join(lines)


def generate_report(target_date: date, db_path: str | None = None) -> Path:
    """Generate the daily report; return the path to the written file."""
    commander_dir = _resolve_commander_dir()
    log_dir = commander_dir / "logs"
    sprints_dir = commander_dir / "sprints"
    reports_dir = commander_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    entries = _load_structured_log(log_dir, target_date)
    if entries:
        data = _parse_structured_logs(entries)
    else:
        data = _parse_state_files(sprints_dir, target_date)

    if db_path is None:
        db_path = os.environ.get("DB_PATH", "").strip()

    token_data = _query_token_usage(db_path, target_date) if db_path else None
    if token_data is None:
        token_data = _token_fallback_from_states(sprints_dir, target_date)

    generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    report_md = _render_report(target_date, data, token_data, generated_at)

    out_path = reports_dir / f"{target_date.isoformat()}.md"
    out_path.write_text(report_md, encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Commander daily summary report"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Target date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to commander.db SQLite file (default: $DB_PATH env var)",
    )
    args = parser.parse_args()

    if args.date:
        try:
            target = date.fromisoformat(args.date)
        except ValueError:
            print(
                f"Error: invalid date {args.date!r} — expected YYYY-MM-DD",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        target = date.today()

    out_path = generate_report(target, db_path=args.db_path)
    print(f"Report written to {out_path}")


if __name__ == "__main__":
    main()
