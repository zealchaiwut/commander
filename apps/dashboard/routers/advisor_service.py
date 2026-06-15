"""I/O-coupled service for the daily advisor agent (issue #881).

Keeps every side-effecting collaborator in one place — file reads, GitHub
API, DB writes, subprocess dispatch — so
``services.sprint_manager.advisor`` stays pure and unit-testable.

The advisor reads project context (PRODUCT.md, milestones, backlog, todos,
sprint briefs, failure patterns), calls ``claude -p`` for suggestions, validates
the count (3-5) and structure, then saves to the ``advisor_suggestions`` table.
No GitHub objects are created or modified.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import db
import projects as projects_module
from services.sprint_manager import advisor as adv
from services.sprint_manager import todo_repo

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

# In-process guard: one advisor run per project at a time.
_run_lock = threading.Lock()
_running: set[str] = set()


# ── Context collection ────────────────────────────────────────────────────────

def collect_inputs(
    project: str,
    repo_root: Optional[Path] = None,
    *,
    gh_list_milestones: Optional[Callable] = None,
    gh_list_backlog: Optional[Callable] = None,
    db_get_todos: Optional[Callable] = None,
    db_get_briefs: Optional[Callable] = None,
    db_get_failures: Optional[Callable] = None,
) -> dict:
    """Read all inputs the advisor needs before generating suggestions.

    Collaborators are injectable for testing; production callers omit them
    and receive the real implementations.
    """
    # ── PRODUCT.md ────────────────────────────────────────────────────────────
    product_md: Optional[str] = None
    product_md_missing = False
    root = repo_root or _guess_project_root(project)
    if root:
        product_path = Path(root) / "PRODUCT.md"
        if product_path.exists():
            try:
                product_md = product_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                product_md_missing = True
        else:
            product_md_missing = True
    else:
        product_md_missing = True

    # ── Open milestones + remaining tickets ───────────────────────────────────
    _list_ms = gh_list_milestones or (lambda repo: _gh_list_milestones(repo))
    try:
        milestones = _list_ms(project) or []
    except Exception as exc:
        logger.warning("advisor: milestones fetch failed: %s", exc)
        milestones = []

    # ── Backlog ───────────────────────────────────────────────────────────────
    _list_bl = gh_list_backlog or (lambda repo: _gh_list_backlog(repo))
    try:
        backlog = _list_bl(project) or []
    except Exception as exc:
        logger.warning("advisor: backlog fetch failed: %s", exc)
        backlog = []

    # ── Todo notes ────────────────────────────────────────────────────────────
    _get_todos = db_get_todos or (lambda p: todo_repo.list_todos(p))
    try:
        todos = _get_todos(project) or []
    except Exception as exc:
        logger.warning("advisor: todos fetch failed: %s", exc)
        todos = []

    # ── Recent sprint briefs (last 7) ─────────────────────────────────────────
    _get_briefs = db_get_briefs or (lambda p, n: _db_recent_briefs(p, n))
    try:
        briefs = _get_briefs(project, 7) or []
    except Exception as exc:
        logger.warning("advisor: briefs fetch failed: %s", exc)
        briefs = []

    # ── Failure / rework patterns from agent_runs ─────────────────────────────
    _get_failures = db_get_failures or (lambda p, n: _db_recent_failures(p, n))
    try:
        failures = _get_failures(project, 10) or []
    except Exception as exc:
        logger.warning("advisor: failures fetch failed: %s", exc)
        failures = []

    try:
        dismissed_pitches = get_dismissed_pitches(project)
    except Exception as exc:
        logger.warning("advisor: dismissed pitches fetch failed: %s", exc)
        dismissed_pitches = []

    return {
        "product_md": product_md,
        "product_md_missing": product_md_missing,
        "milestones": milestones,
        "backlog": backlog,
        "todos": todos,
        "briefs": briefs,
        "failures": failures,
        "dismissed_pitches": dismissed_pitches,
    }


def _guess_project_root(project: str) -> Optional[Path]:
    try:
        wt = projects_module.guess_coder_worktree(project)
        if wt and wt.exists():
            return wt
    except Exception:
        pass
    return None


def _gh_list_milestones(project: str) -> list[dict]:
    import github_milestones  # noqa: PLC0415
    try:
        return github_milestones.list_milestones(project) or []
    except Exception:
        return []


def _gh_list_backlog(project: str) -> list[dict]:
    import github_client as gh  # noqa: PLC0415
    try:
        issues = gh.list_all_open_issues(project, limit=50)
        return [i for i in issues if any(
            lbl.get("name") == "backlog"
            for lbl in i.get("labels", [])
        )]
    except Exception:
        return []


def _db_recent_briefs(project: str, n: int) -> list[dict]:
    from datetime import date, timedelta  # noqa: PLC0415
    today = date.today()
    results = []
    for i in range(n):
        d = (today - timedelta(days=i)).isoformat()
        row = db.get_brief_summary("project", project, d)
        if row:
            results.append({"date": d, "summary": row["summary"]})
    return results


def _db_recent_failures(project: str, n: int) -> list[dict]:
    try:
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT issue_number, sprint_label, agent, outcome, started_at "
                "FROM agent_runs "
                "WHERE outcome IN ('failed', 'needs_rework') "
                "ORDER BY started_at DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(project: str, inputs: dict) -> str:
    parts = [
        f"You are a next-build advisor for the project: {project}.",
        "Based on the context below, propose exactly 3-5 concrete suggestions for "
        "what to build next. Output ONLY a JSON array with no other text, "
        "no markdown fences, no explanation.",
        "",
        'Each suggestion must be a JSON object with exactly these fields:',
        '  "pitch"     — one-line description of the suggestion (≤ 120 chars)',
        '  "rationale" — 1-3 sentences citing specific inputs you read',
        '  "milestone" — existing milestone name or a proposed new milestone title',
        '  "scope"     — exactly one of "S" (< 1 day), "M" (1-3 days), "L" (> 3 days)',
        "",
        "=== CONTEXT ===",
        "",
    ]

    if inputs.get("product_md_missing") or not inputs.get("product_md"):
        parts.append(
            "PRODUCT.md: ABSENT — this project has no PRODUCT.md file. "
            "Note this absence in at least one suggestion's rationale."
        )
    else:
        parts.append(f"PRODUCT.md:\n{inputs['product_md'][:3000]}")

    parts.append("")
    milestones = inputs.get("milestones") or []
    if milestones:
        parts.append("Open milestones and remaining tickets:")
        for ms in milestones[:10]:
            title = ms.get("title") or ms.get("name", "?")
            open_count = ms.get("open_issues", "?")
            parts.append(f"  - {title} ({open_count} open tickets)")
    else:
        parts.append("Open milestones: none")

    parts.append("")
    backlog = inputs.get("backlog") or []
    if backlog:
        parts.append("Backlog items (up to 10):")
        for issue in backlog[:10]:
            parts.append(f"  #{issue.get('number', '?')}: {issue.get('title', '?')}")
    else:
        parts.append("Backlog: empty")

    parts.append("")
    todos = inputs.get("todos") or []
    active_todos = [t for t in todos if not t.get("done")]
    if active_todos:
        parts.append("To-do notes (active):")
        for t in active_todos[:10]:
            parts.append(f"  - {t.get('text', '')}")
    else:
        parts.append("To-do notes: none")

    parts.append("")
    briefs = inputs.get("briefs") or []
    if briefs:
        parts.append("Recent sprint briefs:")
        for b in briefs[:5]:
            parts.append(f"  [{b.get('date', '?')}] {b.get('summary', '')[:400]}")
    else:
        parts.append("Recent sprint briefs: none")

    parts.append("")
    failures = inputs.get("failures") or []
    if failures:
        parts.append("Recent rework/failure patterns:")
        for f in failures[:5]:
            parts.append(
                f"  Issue #{f.get('issue_number','?')} ({f.get('sprint_label','?')}): "
                f"{f.get('outcome','?')} by {f.get('agent','?')}"
            )
    else:
        parts.append("Recent failures/rework: none")

    parts.append("")
    dismissed_pitches = inputs.get("dismissed_pitches") or []
    if dismissed_pitches:
        parts.append("Previously dismissed suggestions (do NOT re-propose these ideas):")
        for dp in dismissed_pitches:
            parts.append(f"  - {dp}")

    parts += [
        "",
        "=== OUTPUT ===",
        "Return ONLY a JSON array of 3-5 suggestion objects. "
        "No markdown, no commentary, just the JSON.",
    ]
    return "\n".join(parts)


# ── Agent dispatch ────────────────────────────────────────────────────────────

def run_advisor_agent(
    prompt: str,
    model: str = _MODEL,
    cwd: Optional[Path] = None,
) -> list[dict]:
    """Call ``claude -p`` with the prompt; parse and return the suggestions list.

    Raises ValueError when the output cannot be parsed or fails validation.
    """
    cmd = ["claude", "--model", model, "-p", prompt]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env["CLAUDE_AGENT_ROLE"] = "advisor"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Advisor agent timed out after 300 s")
    except FileNotFoundError:
        raise ValueError("claude CLI not found; install Claude Code CLI")

    raw = result.stdout.strip()
    if not raw:
        stderr = result.stderr.strip()
        raise ValueError(f"Advisor agent produced no output. stderr: {stderr[:500]}")

    suggestions = _parse_suggestions(raw)
    return adv.validate_suggestions(suggestions)


def _parse_suggestions(raw: str) -> list[dict]:
    """Strip markdown fences and parse the JSON array from agent output."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        in_fence = False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not inner:
                inner.append(line)
        text = "\n".join(inner).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in advisor output: {text[:300]!r}")
    return json.loads(text[start : end + 1])


# ── DB operations ─────────────────────────────────────────────────────────────

def save_suggestions(
    project: str,
    suggestions: list[dict],
    on_demand: bool = False,
) -> None:
    """Replace all drafts for *project* with *suggestions* (AC5, AC7)."""
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with db.get_conn() as conn:
        _ensure_table(conn)
        conn.execute(
            "DELETE FROM advisor_suggestions WHERE project = ?", (project,)
        )
        for s in suggestions:
            conn.execute(
                "INSERT INTO advisor_suggestions "
                "(project, run_at, on_demand, pitch, rationale, milestone, scope) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project,
                    run_at,
                    1 if on_demand else 0,
                    s["pitch"],
                    s["rationale"],
                    s["milestone"],
                    s["scope"],
                ),
            )
        conn.commit()


def get_suggestions(project: str) -> list[dict]:
    """Return active (non-dismissed) draft suggestions for *project* (AC8, #882)."""
    dismissed = set(get_dismissed_pitches(project))
    with db.get_conn() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT id, pitch, rationale, milestone, scope, run_at, on_demand "
            "FROM advisor_suggestions WHERE project = ? ORDER BY id ASC",
            (project,),
        ).fetchall()
    return [dict(r) for r in rows if r["pitch"] not in dismissed]


def get_suggestion_by_id(project: str, suggestion_id: int) -> dict | None:
    """Return one suggestion row by id, or None."""
    with db.get_conn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT id, pitch, rationale, milestone, scope, run_at, on_demand "
            "FROM advisor_suggestions WHERE id = ? AND project = ?",
            (suggestion_id, project),
        ).fetchone()
    return dict(row) if row else None


def get_dismissed_pitches(project: str) -> list[str]:
    """Return pitches the user has dismissed for *project* (#882)."""
    with db.get_conn() as conn:
        _ensure_dismissed_table(conn)
        rows = conn.execute(
            "SELECT pitch FROM advisor_dismissed WHERE project = ? ORDER BY id ASC",
            (project,),
        ).fetchall()
    return [r["pitch"] for r in rows]


def dismiss_suggestion(project: str, suggestion_id: int) -> dict:
    """Persist a suggestion's pitch to the dismissed store (#882)."""
    with db.get_conn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT pitch FROM advisor_suggestions WHERE id = ? AND project = ?",
            (suggestion_id, project),
        ).fetchone()
    if row is None:
        raise ValueError(f"Suggestion {suggestion_id} not found for project {project!r}")
    pitch = row["pitch"]
    dismissed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with db.get_conn() as conn:
        _ensure_dismissed_table(conn)
        conn.execute(
            "INSERT OR IGNORE INTO advisor_dismissed (project, pitch, dismissed_at) "
            "VALUES (?, ?, ?)",
            (project, pitch, dismissed_at),
        )
        conn.commit()
    return {"dismissed": True, "pitch": pitch}


def _ensure_table(conn: sqlite3.Connection) -> None:
    db._create_advisor_suggestions_table(conn)


def _ensure_dismissed_table(conn: sqlite3.Connection) -> None:
    db._create_advisor_dismissed_table(conn)


def _ensure_look_ahead_table(conn: sqlite3.Connection) -> None:
    db._create_advisor_look_ahead_table(conn)


# Placeholder so tests can monkeypatch it (the production path never calls this).
def _gh_post(*args, **kwargs):  # pragma: no cover
    raise NotImplementedError("advisor_service._gh_post is a test-only stub target")


# ── Look-ahead prompt and agent ───────────────────────────────────────────────

def build_look_ahead_prompt(project: str, inputs: dict, suggestions: list[dict]) -> str:
    """Build the prompt that asks the advisor to produce a 2-5-sprint look-ahead."""
    ms_lines = []
    for ms in (inputs.get("milestones") or [])[:10]:
        title = ms.get("title") or ms.get("name", "?")
        open_count = ms.get("open_issues", "?")
        ms_lines.append(f"  - {title} ({open_count} open tickets)")

    suggestion_pitches = "\n".join(
        f"  {i + 1}. {s.get('pitch', '')}" for i, s in enumerate(suggestions)
    )

    parts = [
        f"You are a sprint planning advisor for the project: {project}.",
        "Given the context below, produce a short ordered look-ahead of 2 to 5 future "
        "sprints. Each entry is one line describing which milestone slice or themes "
        "that sprint would address.",
        "",
        "Output ONLY a JSON array of 2-5 strings. No markdown, no extra text.",
        "Format example: [\"Sprint 73: Auth layer — User Auth milestone\", \"Sprint 74: ...\"]",
        "",
        "=== CONTEXT ===",
        "",
        "Open milestones:",
    ]
    if ms_lines:
        parts.extend(ms_lines)
    else:
        parts.append("  (none)")

    parts += [
        "",
        "Current advisor suggestions (top priorities):",
        suggestion_pitches or "  (none)",
        "",
        "=== OUTPUT ===",
        "Return ONLY a JSON array of 2-5 one-line strings. No markdown, no commentary.",
    ]
    return "\n".join(parts)


def run_look_ahead_agent(
    prompt: str,
    model: str = _MODEL,
    cwd: Optional[Path] = None,
) -> list[str]:
    """Call ``claude -p`` with the look-ahead prompt; parse and validate the result.

    Raises ValueError when output cannot be parsed or fails validation.
    No GitHub objects are created or modified.
    """
    cmd = ["claude", "--model", model, "-p", prompt]
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    env["CLAUDE_AGENT_ROLE"] = "advisor"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Look-ahead agent timed out after 180 s")
    except FileNotFoundError:
        raise ValueError("claude CLI not found; install Claude Code CLI")

    raw = result.stdout.strip()
    if not raw:
        stderr = result.stderr.strip()
        raise ValueError(f"Look-ahead agent produced no output. stderr: {stderr[:500]}")

    return _parse_look_ahead(raw)


def _parse_look_ahead(raw: str) -> list[str]:
    """Strip markdown fences and parse the JSON array from look-ahead output."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = []
        in_fence = False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence or not inner:
                inner.append(line)
        text = "\n".join(inner).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in look-ahead output: {text[:300]!r}")
    entries = json.loads(text[start : end + 1])
    return adv.validate_look_ahead(entries)


# ── Look-ahead DB operations ──────────────────────────────────────────────────

def save_look_ahead(
    project: str,
    entries: list[str],
    on_demand: bool = False,
) -> None:
    """Replace the look-ahead for *project* with *entries* (AC1, issue #883).

    Passing an empty list clears the look-ahead without affecting suggestions or
    any GitHub objects. At most LOOK_AHEAD_MAX entries are persisted.
    """
    from services.sprint_manager.advisor import LOOK_AHEAD_MAX  # noqa: PLC0415
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    safe = list(entries)[:LOOK_AHEAD_MAX]
    with db.get_conn() as conn:
        _ensure_look_ahead_table(conn)
        conn.execute(
            "DELETE FROM advisor_look_ahead WHERE project = ?", (project,)
        )
        for pos, entry in enumerate(safe, start=1):
            conn.execute(
                "INSERT INTO advisor_look_ahead "
                "(project, run_at, position, entry, on_demand) "
                "VALUES (?, ?, ?, ?, ?)",
                (project, run_at, pos, str(entry), 1 if on_demand else 0),
            )
        conn.commit()


def get_look_ahead(project: str) -> list[str]:
    """Return the current look-ahead entries for *project*, ordered by position."""
    with db.get_conn() as conn:
        _ensure_look_ahead_table(conn)
        rows = conn.execute(
            "SELECT entry FROM advisor_look_ahead "
            "WHERE project = ? ORDER BY position ASC",
            (project,),
        ).fetchall()
    return [r["entry"] for r in rows]


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_advisor(
    project: str,
    on_demand: bool = False,
    repo_root: Optional[Path] = None,
) -> list[dict]:
    """Run the advisor agent, save suggestions and look-ahead, return suggestions."""
    inputs = collect_inputs(project, repo_root=repo_root)
    prompt = build_prompt(project, inputs)
    suggestions = run_advisor_agent(prompt)
    save_suggestions(project, suggestions, on_demand=on_demand)

    # Generate look-ahead alongside the normal suggestion flow (AC1, issue #883).
    try:
        la_prompt = build_look_ahead_prompt(project, inputs, suggestions)
        look_ahead = run_look_ahead_agent(la_prompt)
        save_look_ahead(project, look_ahead, on_demand=on_demand)
    except Exception as exc:
        logger.warning("advisor: look-ahead generation failed: %s", exc)

    if on_demand and adv.get_advisor_reset_on_demand(project):
        from datetime import date  # noqa: PLC0415
        adv.set_advisor_last_fired(project, date.today().isoformat())
    return suggestions


def tick(project: str) -> dict:
    """Fire the daily advisor if it is due for *project*. Non-blocking.

    Returns ``{"fired": True}`` or ``{"fired": False, "reason": "..."}``
    (AC1, AC10: no catch-up, skips silently when not due).
    """
    from datetime import date, datetime as dt  # noqa: PLC0415
    now = dt.now()
    now_hhmm = now.strftime("%H:%M")
    today = date.today().isoformat()

    scheduled = adv.get_advisor_time(project)
    last_fired = adv.get_advisor_last_fired(project)

    if not adv.should_fire(scheduled, now_hhmm, last_fired, today):
        return {"fired": False, "reason": "not_due"}

    with _run_lock:
        if project in _running:
            return {"fired": False, "reason": "already_running"}
        if adv.get_advisor_last_fired(project) == today:
            return {"fired": False, "reason": "already_fired_today"}
        adv.set_advisor_last_fired(project, today)
        _running.add(project)

    def _worker():
        try:
            run_advisor(project, on_demand=False)
        except Exception as exc:
            logger.error("advisor tick failed for %s: %s", project, exc)
        finally:
            with _run_lock:
                _running.discard(project)

    threading.Thread(target=_worker, daemon=True, name=f"advisor-{project}").start()
    return {"fired": True, "project": project}
