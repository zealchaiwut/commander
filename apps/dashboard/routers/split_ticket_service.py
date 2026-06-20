"""Service logic for the split-ticket flow (issue #1423).

Provides:
  - suggest_split()    — lightweight BA call to pre-fill draft titles / AC scopes
  - confirm_split()    — create two child issues, handle original, trigger estimates
  - build_child_labels() — label inheritance logic (strip size-*, estimated)
  - _parse_ba_suggest_output() — parse BA call output or return fallback
  - _trigger_estimate() — fire background re-estimate for a new child issue
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _DASHBOARD_ROOT.parent.parent
_SERVICES_ROOT = _REPO_ROOT / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import github_client as gc_module  # noqa: E402

# Labels that are per-ticket lifecycle state — never inherited by children.
_STRIP_LABELS = frozenset({"size-S", "size-M", "size-L", "size-XL", "estimated", "in-progress"})
_MODEL = "claude-haiku-4-5-20251001"


# ── Label helpers ─────────────────────────────────────────────────────────────

def build_child_labels(parent_labels: list[str], sprint_label: str) -> list[str]:
    """Return labels for a child issue: parent labels minus lifecycle/size labels.

    Keeps the sprint label, enhancement, and any custom labels.
    Always strips size-* and estimated so children get fresh estimates.
    """
    result = []
    for lbl in parent_labels:
        if lbl in _STRIP_LABELS:
            continue
        result.append(lbl)
    # Ensure the sprint label is present (it should already be in parent_labels)
    if sprint_label and sprint_label not in result:
        result.append(sprint_label)
    return result


# ── BA call helpers ───────────────────────────────────────────────────────────

def _build_suggest_prompt(issue_title: str, issue_body: str) -> str:
    body_excerpt = (issue_body or "")[:2000]
    return (
        f"You are a BA agent. A developer wants to split this ticket into two "
        f"focused child issues.\n\n"
        f"TICKET TITLE: {issue_title}\n\n"
        f"TICKET BODY:\n{body_excerpt}\n\n"
        f"Return ONLY a JSON object (no markdown fences, no commentary) with this shape:\n"
        f'{{"child1":{{"title":"...","ac_scope":"..."}},'
        f'"child2":{{"title":"...","ac_scope":"..."}}}}\n\n'
        f"Each child title should be a concise focused description of one half of "
        f"the original ticket. Each ac_scope should be 2-4 acceptance-criteria lines "
        f"in the format '- [ ] ...'."
    )


def _parse_ba_suggest_output(raw: Optional[str]) -> dict:
    """Parse BA output into {child1, child2, fallback}.

    Returns fallback=True with empty child titles when raw is None or unparseable.
    """
    _empty = {
        "child1": {"title": "", "ac_scope": ""},
        "child2": {"title": "", "ac_scope": ""},
        "fallback": True,
    }
    if not raw:
        return _empty

    text = raw.strip()
    # Strip markdown fences if present
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

    # Find JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return _empty

    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return _empty

    child1 = data.get("child1") or {}
    child2 = data.get("child2") or {}
    if not child1.get("title") or not child2.get("title"):
        return _empty

    return {
        "child1": {
            "title": str(child1.get("title", "")),
            "ac_scope": str(child1.get("ac_scope", "")),
        },
        "child2": {
            "title": str(child2.get("title", "")),
            "ac_scope": str(child2.get("ac_scope", "")),
        },
        "fallback": False,
    }


def suggest_split(
    issue_num: int,
    project: str,
    gc=None,
) -> dict:
    """Call the BA agent to suggest a 2-way split for an issue.

    Returns {child1: {title, ac_scope}, child2: {title, ac_scope}, fallback: bool}.
    On any error the fallback variant is returned instead of raising.
    """
    if gc is None:
        gc = gc_module

    try:
        issue = gc.get_issue(issue_num, repo_name=project)
    except Exception:
        return _parse_ba_suggest_output(None)

    title = issue.get("title", "")
    body = issue.get("body", "") or ""
    prompt = _build_suggest_prompt(title, body)

    try:
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_AGENT_ROLE"] = "ba"
        result = subprocess.run(
            ["claude", "--model", _MODEL, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        raw = result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        raw = None

    return _parse_ba_suggest_output(raw)


# ── Estimate trigger ──────────────────────────────────────────────────────────

def _trigger_estimate(issue_num: int, project: str, repo: str) -> None:
    """Fire a background re-estimate run for *issue_num* via the estimate CLI."""
    estimate_script = _SERVICES_ROOT / "estimate_issue.py"
    if not estimate_script.exists():
        return

    def _run():
        try:
            subprocess.run(
                [
                    sys.executable,
                    str(estimate_script),
                    "--issue", str(issue_num),
                    "--repo", repo,
                    "--save-label",
                    "--force",
                ],
                capture_output=True,
                timeout=180,
                cwd=str(_REPO_ROOT),
            )
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


# ── Confirm split ─────────────────────────────────────────────────────────────

def confirm_split(
    issue_num: int,
    sprint_label: str,
    project: str,
    child1_title: str,
    child1_body: str,
    child2_title: str,
    child2_body: str,
    action: str,  # "remove" | "close"
    gc=None,
) -> dict:
    """Create two child issues, handle the original, and trigger estimates.

    Returns {child1_num, child2_num, ok}.
    """
    if gc is None:
        gc = gc_module

    # Fetch original issue labels
    original = gc.get_issue(issue_num, repo_name=project)
    parent_label_names = [lbl["name"] for lbl in original.get("labels", [])]
    child_labels = build_child_labels(parent_label_names, sprint_label)

    # Create both child issues
    child1_num, _ = gc.create_issue(
        title=child1_title,
        body=child1_body,
        labels=child_labels,
        repo_name=project,
    )
    child2_num, _ = gc.create_issue(
        title=child2_title,
        body=child2_body,
        labels=child_labels,
        repo_name=project,
    )

    # Handle original: remove from sprint or close with comment
    if action == "close":
        comment = (
            f"Split into #{child1_num} and #{child2_num}.\n\n"
            f"Child issues:\n- #{child1_num}: {child1_title}\n- #{child2_num}: {child2_title}"
        )
        gc.add_comment(issue_num, comment, repo_name=project)
        gc.close_issue(issue_num, repo_name=project, reason="not planned")
    else:
        # action == "remove": strip sprint label so ticket stays open in backlog
        gc.assign_sprint_by_label(issue_num, None, repo_name=project)

    # Trigger background re-estimates for both children (AC5)
    _trigger_estimate(child1_num, project, project)
    _trigger_estimate(child2_num, project, project)

    return {
        "child1_num": child1_num,
        "child2_num": child2_num,
        "ok": True,
    }
