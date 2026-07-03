"""LLM-backed backlog triage for `[follow-up]` tickets.

Reads the open `[follow-up]` backlog tickets (title + body) and asks a cheap
agent (Haiku) to assign each a priority, flag semantic duplicates, and recommend
whether it is safe to close. The dashboard renders the result as a checklist so
the human approves every close — the agent only proposes.

This is a DRY-RUN helper: it never closes anything. Mirrors the claude-subprocess
pattern used by ``estimate_issue.py`` (subscription auth, JSON-only output).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Optional

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT_SECS = 180
_BODY_CHARS = 1200          # cap each ticket body in the prompt
_MAX_TICKETS = 80           # safety cap on how many tickets we triage in one call

_VALID_PRIORITY = {"P0", "P1", "P2", "negligible"}


def extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from agent output (markdown-fence tolerant)."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _build_prompt(tickets: list[dict]) -> str:
    lines = [
        "You are triaging a software project's open `[follow-up]` backlog tickets.",
        "These are minor cleanups/notes left behind by code reviews and sprints.",
        "",
        "For EACH ticket decide:",
        "  - priority: one of P0 (must do), P1 (should do), P2 (nice to have),",
        "    or negligible (trivial/obsolete — safe to drop).",
        "  - duplicate_of: the ticket number this is a semantic duplicate of "
        "(same underlying work, even if worded differently), or null. Point",
        "    duplicates at the LOWEST-numbered ticket in the duplicate group.",
        "  - recommend_close: true ONLY when it is a duplicate OR negligible.",
        "  - reason: one short sentence.",
        "",
        "Tickets:",
    ]
    for t in tickets:
        body = (t.get("body") or "").strip().replace("\r", "")
        if len(body) > _BODY_CHARS:
            body = body[:_BODY_CHARS] + " …"
        lines.append(f"\n--- #{t.get('number')}: {t.get('title') or ''}")
        if body:
            lines.append(body)
    lines += [
        "",
        "Output ONLY a JSON object of this exact shape, no other text:",
        '{"tickets": [{"number": <int>, "priority": "P0|P1|P2|negligible", '
        '"duplicate_of": <int|null>, "recommend_close": <bool>, "reason": "<str>"}]}',
    ]
    return "\n".join(lines)


def triage_backlog(
    tickets: list[dict],
    project: Optional[str] = None,
) -> tuple[Optional[dict], Optional[str]]:
    """Run the triage agent over `tickets` (list of {number,title,body}).

    Returns (result, error_type). result is {"tickets": [...]} normalized to the
    input ticket set. error_type is one of None, "missing_claude",
    "model_error", "parse_error", "no_tickets".
    """
    tickets = [t for t in tickets if t.get("number") is not None][:_MAX_TICKETS]
    if not tickets:
        return {"tickets": []}, "no_tickets"

    prompt = _build_prompt(tickets)
    # Strip ANTHROPIC_API_KEY so claude authenticates via the subscription, like
    # the other agents (see estimate_issue.py).
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    from services.sprint_manager.model_routing import apply_provider_env
    _triage_model = apply_provider_env(env, _MODEL, repo=project)
    cmd = [
        "claude",
        "--model", _triage_model,
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "-p", prompt,
    ]
    env["CLAUDE_MODEL"] = _triage_model
    env.setdefault("CLAUDE_AGENT_ROLE", "backlog_triage")
    if project:
        env.setdefault("COMMANDER_PROJECT", project)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_SECS, env=env,
        )
    except subprocess.TimeoutExpired:
        return None, "model_error"
    except FileNotFoundError:
        return None, "missing_claude"

    if result.returncode != 0:
        return None, "model_error"

    parsed = extract_json(result.stdout)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tickets"), list):
        return None, "parse_error"

    # Normalize + clamp to the input set; default anything the model missed.
    by_num = {int(t["number"]): t for t in tickets}
    out: list[dict] = []
    seen: set[int] = set()
    for row in parsed["tickets"]:
        try:
            num = int(row.get("number"))
        except (TypeError, ValueError):
            continue
        if num not in by_num or num in seen:
            continue
        seen.add(num)
        pr = str(row.get("priority") or "").strip()
        if pr not in _VALID_PRIORITY:
            pr = "P2"
        dup = row.get("duplicate_of")
        try:
            dup = int(dup) if dup is not None else None
        except (TypeError, ValueError):
            dup = None
        if dup == num:
            dup = None
        out.append({
            "number": num,
            "title": by_num[num].get("title") or "",
            "priority": pr,
            "duplicate_of": dup,
            "recommend_close": bool(row.get("recommend_close")) or dup is not None or pr == "negligible",
            "reason": str(row.get("reason") or "").strip()[:200],
        })
    # Any ticket the model omitted: keep it, default P2, don't recommend close.
    for num, t in by_num.items():
        if num not in seen:
            out.append({
                "number": num, "title": t.get("title") or "", "priority": "P2",
                "duplicate_of": None, "recommend_close": False,
                "reason": "Not assessed by the agent.",
            })
    out.sort(key=lambda r: r["number"])
    return {"tickets": out}, None


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="LLM triage of [follow-up] backlog tickets")
    ap.add_argument("--repo", required=True, help="owner/repo")
    args = ap.parse_args()
    # Local import to avoid a hard dependency when used as a library.
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent / "apps" / "dashboard"))
    from routers import backlog_cleanup_service as bcs  # noqa: PLC0415
    import github_client as gc  # noqa: PLC0415
    issues = [i for i in gc.list_open_issues_with_body(repo_name=args.repo)
              if bcs.is_backlog_issue(i) and bcs.is_follow_up_title(i.get("title") or "")]
    tickets = [{"number": i.get("number"), "title": i.get("title"), "body": i.get("body")} for i in issues]
    result, err = triage_backlog(tickets, project=args.repo)
    print(json.dumps({"result": result, "error": err}, indent=2))
    return 0 if err in (None, "no_tickets") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
