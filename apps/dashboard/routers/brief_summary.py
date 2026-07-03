"""LLM brief summary: generate, cache, and fall back (issue #840).

Sits on top of the structured brief assembled by :mod:`brief_service` (#839)
and produces a short, readable narrative for the top of each brief:

* :func:`get_or_create_project_summary` — a 2–4 sentence "chief of staff"
  summary for one project's ``(project, date)``.
* :func:`get_or_create_home_summary` — a one-line recap that synthesises the
  per-project summaries across the home roll-up.

Three guarantees drive the design:

1. **The model sees only structured data.** The payload handed to Haiku is built
   purely from the assembled brief dict (counts + statuses). No web access, no
   file access, no extra context (AC2).
2. **At most one model call per key.** A model-generated summary is stored
   against ``(scope, project, date)`` and returned on subsequent loads without
   re-invoking the model (AC3). Only model output is cached — the deterministic
   templated fallback is recomputed on the fly so a transient failure never
   poisons the cache.
3. **Rendering never blocks.** When the model call fails (timeout / error /
   missing CLI) or is disabled via ``COMMANDER_DISABLE_BRIEF_LLM``, a templated
   summary built entirely from the structured fields is returned instead (AC5,
   AC6).

The model is fixed to Haiku and invoked through the ``claude`` CLI subprocess
(subscription-funded, matching the rest of the platform). ``_call_model`` is the
sole subprocess seam — patched in tests.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Optional

from . import brief_service

# Fixed model for this feature (AC: model is not runtime-selectable).
MODEL = "claude-haiku-4-5"
SUMMARY_TIMEOUT_SEC = int(os.environ.get("BRIEF_SUMMARY_TIMEOUT_SEC", "60"))

# Storage scopes (keys into db.brief_summaries).
_SCOPE_PROJECT = "project"
_SCOPE_HOME = "home"
_HOME_KEY = ""  # home recap has no project component


# ── config gate ───────────────────────────────────────────────────────────────

def _llm_enabled() -> bool:
    """False when summary generation is disabled via config (AC5)."""
    val = os.environ.get("COMMANDER_DISABLE_BRIEF_LLM", "").strip().lower()
    return val not in ("1", "true", "yes", "on")


def _db():
    """Deferred db import so a patched DB_PATH is honoured at call time."""
    import db  # noqa: PLC0415
    return db


# ── label prettifier ──────────────────────────────────────────────────────────

def _pretty_label(label: Optional[str]) -> str:
    """`sprint-50` -> `Sprint 50`; pass anything else through unchanged."""
    if not label:
        return ""
    m = re.fullmatch(r"sprint-(\d+)", str(label).strip())
    if m:
        return f"Sprint {int(m.group(1))}"
    return str(label)


# ── structured payload (the ONLY thing the model sees — AC2) ───────────────────

def build_project_payload(brief: dict) -> dict:
    """Distil a project brief into the compact structured payload for the model.

    Pure projection of ``brief``'s structured fields — counts, labels, titles,
    statuses. Carries no file paths, URLs, or instructions to fetch anything.
    """
    shipped = brief.get("shipped") or []
    in_progress = brief.get("in_progress")
    blocked = brief.get("blocked") or []
    kpis = brief.get("kpis") or {}

    payload: dict = {
        "project": brief.get("project"),
        "date": brief.get("date"),
        "shipped": [
            {
                "label": s.get("label"),
                "goal": s.get("goal", ""),
                "done": s.get("done", 0),
                "skipped": s.get("skipped", 0),
                "features": list(s.get("features") or []),
            }
            for s in shipped
        ],
        "in_progress": None,
        "needs_attention": [
            {"issue_number": b.get("issue_number"),
             "title": b.get("title", ""),
             "type": b.get("type")}
            for b in blocked
        ],
        "kpis": {
            "sprints_shipped": kpis.get("sprints_shipped", 0),
            "tickets_done": kpis.get("tickets_done", 0),
            "in_progress": kpis.get("in_progress", False),
            "in_progress_percent": kpis.get("in_progress_percent", 0),
            "needs_you": kpis.get("needs_you", 0),
        },
    }
    if in_progress:
        progress = in_progress.get("progress") or {}
        payload["in_progress"] = {
            "sprint_label": in_progress.get("sprint_label"),
            "progress": {
                "done": progress.get("done", 0),
                "total": progress.get("total", 0),
                "percent": progress.get("percent", 0),
            },
        }
    return payload


_PROJECT_PROMPT = """\
You are a chief of staff writing the one-paragraph summary at the top of a daily \
project brief. Using ONLY the structured data below, write a plain, factual \
2 to 4 sentence summary covering, in order: what shipped, what is in progress, \
and the single item most worth attention. Do not invent numbers, names, or \
status — use only what the data states. Do not use bullet points, headings, or \
markdown. Return only the summary sentences, nothing else.

Structured brief data (JSON):
{payload}
"""


def build_project_prompt(payload: dict) -> str:
    """The fixed Haiku prompt for a project summary, embedding the payload."""
    return _PROJECT_PROMPT.format(payload=json.dumps(payload, indent=2))


# ── deterministic templated fallback (AC5) ────────────────────────────────────

def _attention_count(brief: dict) -> int:
    """Items worth attention = blocked (rework/failed) + needs-you (UAT)."""
    blocked = len(brief.get("blocked") or [])
    needs_you = (brief.get("kpis") or {}).get("needs_you", 0)
    return blocked + needs_you


def _fallback_project_summary(brief: dict) -> str:
    """Build a factual summary from the structured fields only (AC5, AC8).

    e.g. "Shipped Sprint 50, 2 features. Sprint 51 running, 25% complete.
          3 items need attention."
    """
    parts: list[str] = []

    shipped = brief.get("shipped") or []
    kpis = brief.get("kpis") or {}
    if shipped:
        labels = ", ".join(_pretty_label(s.get("label")) for s in shipped if s.get("label"))
        features = kpis.get("tickets_done", 0)
        noun = "feature" if features == 1 else "features"
        if labels:
            parts.append(f"Shipped {labels}, {features} {noun}.")
        else:
            parts.append(f"Shipped {features} {noun}.")

    in_progress = brief.get("in_progress")
    if in_progress:
        label = _pretty_label(in_progress.get("sprint_label"))
        percent = (in_progress.get("progress") or {}).get("percent", 0)
        if label:
            parts.append(f"{label} running, {percent}% complete.")
        else:
            parts.append(f"A sprint is running, {percent}% complete.")

    attention = _attention_count(brief)
    if attention:
        noun = "item" if attention == 1 else "items"
        parts.append(f"{attention} {noun} need attention.")

    if not parts:
        project = brief.get("project") or "this project"
        date = brief.get("date") or "today"
        return f"No activity for {project} on {date}."
    return " ".join(parts)


# ── model invocation (sole subprocess seam) ───────────────────────────────────

def _call_model(prompt: str) -> Optional[str]:
    """Invoke Haiku via the claude CLI, passing ONLY the prompt.

    Returns the stripped stdout, or ``None`` on any failure (missing CLI,
    timeout, non-zero exit, empty output) so the caller can fall back. The
    subprocess is given no tools and no extra context beyond ``prompt`` (AC2).
    """
    # Drop ANTHROPIC_API_KEY so the call is subscription-funded, matching the
    # rest of the platform's claude CLI usage.
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    from services.sprint_manager.model_routing import apply_provider_env
    _model = apply_provider_env(env, MODEL, repo=os.environ.get("COMMANDER_PROJECT"), role="brief")
    try:
        proc = subprocess.run(
            ["claude", "--model", _model, "--dangerously-skip-permissions",
             "-p", prompt],
            capture_output=True,
            text=True,
            timeout=SUMMARY_TIMEOUT_SEC,
            env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return out or None


# ── generation (model with fallback) ──────────────────────────────────────────

def generate_project_summary(brief: dict) -> dict:
    """Return ``{"summary", "source"}`` for one project's brief.

    ``source`` is ``"model"`` on a successful Haiku call, else ``"fallback"``.
    Any failure in the model path falls back deterministically — never raises.
    """
    if _llm_enabled():
        try:
            payload = build_project_payload(brief)
            text = _call_model(build_project_prompt(payload))
            if text:
                return {"summary": text.strip(), "source": "model"}
        except Exception:
            pass
    return {"summary": _fallback_project_summary(brief), "source": "fallback"}


def get_or_create_project_summary(slug: str, date: Optional[str] = None,
                                  force: bool = False) -> dict:
    """Return ``{"project", "date", "summary", "source"}`` for a project brief.

    Reads the cached model summary unless ``force`` (Regenerate), which clears it
    first. Only model output is cached; a fallback result is returned uncached so
    a later load can retry the model. Regenerate is scoped to the summary cache —
    it does not recompute or persist the rest of the structured brief (AC9).
    """
    db = _db()
    brief = brief_service.build_project_brief(slug, date=date)
    d = brief["date"]

    if force:
        db.delete_brief_summary(_SCOPE_PROJECT, slug, d)
    else:
        cached = db.get_brief_summary(_SCOPE_PROJECT, slug, d)
        if cached is not None:
            return {"project": slug, "date": d,
                    "summary": cached["summary"], "source": cached["source"]}

    result = generate_project_summary(brief)
    if result["source"] == "model":
        db.set_brief_summary(_SCOPE_PROJECT, slug, d, result["summary"], "model")
    return {"project": slug, "date": d, **result}


# ── home recap (AC7) ──────────────────────────────────────────────────────────

_HOME_PROMPT = """\
You are a chief of staff writing a one-line recap across several projects for a \
solo developer. Using ONLY the per-project summaries and totals below, write a \
single plain, factual sentence capturing the overall status. Do not invent \
numbers or names. Do not use markdown. Return only the one sentence.

Structured data (JSON):
{payload}
"""


def build_home_payload(home: dict, project_summaries: list[dict]) -> dict:
    """Compact payload for the home recap: per-project summaries + global KPIs."""
    gk = home.get("global_kpis") or {}
    return {
        "date": home.get("date"),
        "global_kpis": {
            "sprints_shipped": gk.get("sprints_shipped", 0),
            "tickets_done": gk.get("tickets_done", 0),
            "in_progress": gk.get("in_progress", 0),
            "needs_your_call": gk.get("needs_your_call", 0),
        },
        "projects": [
            {"project": ps.get("project"), "summary": ps.get("summary", "")}
            for ps in project_summaries
        ],
    }


def build_home_prompt(payload: dict) -> str:
    return _HOME_PROMPT.format(payload=json.dumps(payload, indent=2))


def _fallback_home_summary(home: dict) -> str:
    """Deterministic one-liner from the home global KPIs (AC5, AC7)."""
    gk = home.get("global_kpis") or {}
    projects = home.get("projects") or []
    n_proj = len(projects)
    shipped = gk.get("sprints_shipped", 0)
    tickets = gk.get("tickets_done", 0)
    running = gk.get("in_progress", 0)
    needs = gk.get("needs_your_call", 0)
    proj_noun = "project" if n_proj == 1 else "projects"
    sprint_noun = "sprint" if shipped == 1 else "sprints"
    ticket_noun = "ticket" if tickets == 1 else "tickets"
    item_noun = "item" if needs == 1 else "items"
    return (
        f"Across {n_proj} {proj_noun}: shipped {shipped} {sprint_noun} "
        f"({tickets} {ticket_noun}), {running} running, "
        f"{needs} {item_noun} need your call."
    )


def generate_home_summary(home: dict, project_summaries: list[dict]) -> dict:
    """Return ``{"summary", "source"}`` for the home recap."""
    if _llm_enabled():
        try:
            payload = build_home_payload(home, project_summaries)
            text = _call_model(build_home_prompt(payload))
            if text:
                return {"summary": text.strip(), "source": "model"}
        except Exception:
            pass
    return {"summary": _fallback_home_summary(home), "source": "fallback"}


def get_or_create_home_summary(date: Optional[str] = None,
                               force: bool = False) -> dict:
    """Return ``{"date", "summary", "source"}`` for the home recap.

    Synthesises the per-project summaries into one sentence, with the same cache
    + fallback rules as the per-project summary.
    """
    db = _db()
    home = brief_service.build_home_brief(date=date)
    d = home["date"]

    if force:
        db.delete_brief_summary(_SCOPE_HOME, _HOME_KEY, d)
    else:
        cached = db.get_brief_summary(_SCOPE_HOME, _HOME_KEY, d)
        if cached is not None:
            return {"date": d, "summary": cached["summary"],
                    "source": cached["source"]}

    # Gather per-project summaries to synthesise from (each cached on its own key).
    project_summaries: list[dict] = []
    for p in home.get("projects") or []:
        slug = p.get("project")
        if not slug:
            continue
        try:
            project_summaries.append(
                get_or_create_project_summary(slug, date=d, force=force)
            )
        except Exception:
            continue

    result = generate_home_summary(home, project_summaries)
    if result["source"] == "model":
        db.set_brief_summary(_SCOPE_HOME, _HOME_KEY, d, result["summary"], "model")
    return {"date": d, **result}
