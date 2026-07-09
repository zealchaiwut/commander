"""Split an XL ticket into 2-4 smaller tickets via a BA agent.

preview() runs a Business-Analyst agent on the XL ticket and returns proposed
child specs (no writes). apply() creates the (possibly user-edited) children on
GitHub with the sprint label, then closes the XL as 'not planned' with a comment
linking the children. Mirrors the bulk-create BA pattern (one ticket → many).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile

_SPLIT_TIMEOUT = 180  # seconds for the BA agent

# Per-ticket lifecycle state — never inherited by children. Covers size buckets,
# the estimate marker, and every workflow-stage label (in-progress → SIT → UAT)
# so a child always starts clean and earns its own stage via the re-estimate flow.
_STRIP_LABELS = frozenset({
    "size-S", "size-M", "size-L", "size-XL", "estimated", "in-progress", "SIT", "UAT"
})


def build_child_labels(parent_labels: list[str], sprint_label: str) -> list[str]:
    """Return labels for a child issue: parent labels minus lifecycle/size/state.

    Keeps the sprint label, enhancement, and any custom labels. Always strips
    size-*, estimated, in-progress, SIT, and UAT so children get fresh estimates
    and start in a clean backlog/planning state.
    """
    result = []
    for lbl in parent_labels:
        if lbl in _STRIP_LABELS:
            continue
        result.append(lbl)
    if sprint_label and sprint_label not in result:
        result.append(sprint_label)
    return result


def _server():
    import server  # noqa: PLC0415
    return server


def _parse_children(text: str) -> list[dict]:
    """Extract a JSON array of {title, body} from the agent output (best-effort)."""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    data = None
    try:
        data = json.loads(t)
    except Exception:
        m = re.search(r"\[.*\]", t, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except Exception:
                data = None
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for d in data:
        if isinstance(d, dict) and (d.get("title") or "").strip() and (d.get("body") or "").strip():
            out.append({"title": str(d["title"]).strip()[:240], "body": str(d["body"]).strip()})
    return out[:4]


async def split_preview(repo: str, sprint_label: str, issue_num: int) -> dict:
    """Run the BA split agent; return proposed children. No writes."""
    srv = _server()
    try:
        issue = srv.github_client.get_issue(issue_num, repo_name=repo) or {}
    except Exception:
        issue = {}
    title = issue.get("title") or f"#{issue_num}"
    body = issue.get("body") or ""

    prompt = (
        "You are a Business Analyst. The GitHub ticket below is too large (XL) and "
        "must be split into 2 to 4 smaller, independent tickets — each about M-sized "
        "(~15 minutes of focused work) — that TOGETHER fully cover the original, with "
        "no overlap and no gaps. Each child needs a clear, specific title and a body "
        "in GitHub-flavored markdown that includes a short '## Acceptance Criteria' "
        "section.\n\n"
        f"ORIGINAL TICKET #{issue_num}: {title}\n\n{body}\n\n"
        'Output ONLY a JSON array of 2-4 objects, each with exactly two string fields: '
        '"title" and "body". No prose, no code fence.'
    )
    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    from services.sprint_manager.model_routing import apply_provider_env
    _split_model = apply_provider_env(
        sub_env, "claude-sonnet-4-6", repo=os.environ.get("COMMANDER_PROJECT"),
        role="ba",
    )
    cmd = [
        "claude", "--model", _split_model,
        "--dangerously-skip-permissions", "-p", prompt,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
            env=sub_env,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "claude CLI not found"}
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=float(_SPLIT_TIMEOUT))
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "error": "split agent timed out"}

    children = _parse_children((out or b"").decode("utf-8", "replace"))
    if not children:
        return {"ok": False, "error": "could not parse a split proposal from the agent"}
    return {"ok": True, "issue": issue_num, "title": title, "children": children}


def _compensate(gh, repo: str, created: list[dict]) -> dict:
    """Best-effort close of orphaned children created before a split failed.

    Returns a compensation report: which child numbers were closed, which could
    not be closed (so the caller can tell the user manual cleanup is needed), and
    summary booleans. Each close is independently guarded so one failure does not
    abort the rest. ``attempted`` is True only when there is something to undo.
    """
    closed: list[int] = []
    failed: list[int] = []
    for c in created:
        num = c["number"]
        try:
            gh.close_issue(num, repo_name=repo, reason="not planned")
            closed.append(num)
        except Exception:
            failed.append(num)
    return {
        "attempted": bool(created),
        "closed": closed,
        "failed": failed,
        "succeeded": bool(created) and not failed,
        "manual_cleanup_required": bool(failed),
    }


def _failure_result(gh, repo: str, created: list[dict], exc: Exception) -> dict:
    """Build the structured not-ok result for a split that failed mid-way.

    Distinguishes a *partial* failure (≥1 child already created on GitHub — needs
    cleanup) from a *total* failure (nothing created — nothing to undo), attempts
    best-effort compensation on the orphans, and surfaces the orphan numbers in
    the human-readable message so the user can recover even if compensation fails.
    """
    created_numbers = [c["number"] for c in created]
    partial = bool(created)

    if not partial:
        # Nothing was created on GitHub — a clean total failure, no cleanup needed.
        return {
            "ok": False,
            "partial": False,
            "error": f"Split failed before any child was created: {exc}. No cleanup required.",
            "created": created,
            "created_numbers": [],
            "compensation": {
                "attempted": False, "closed": [], "failed": [],
                "succeeded": False, "manual_cleanup_required": False,
            },
        }

    nums = ", ".join(f"#{n}" for n in created_numbers)
    comp = _compensate(gh, repo, created)
    if comp["manual_cleanup_required"]:
        orphans = ", ".join(f"#{n}" for n in comp["failed"])
        msg = (
            f"Split partially failed: {exc}. Child issue(s) {nums} were created on "
            f"GitHub but could not be closed automatically — manual cleanup required "
            f"on GitHub for {orphans}."
        )
    else:
        msg = (
            f"Split partially failed: {exc}. Child issue(s) {nums} were created on "
            f"GitHub and have been closed automatically (compensation succeeded)."
        )
    return {
        "ok": False,
        "partial": True,
        "error": msg,
        "created": created,
        "created_numbers": created_numbers,
        "compensation": comp,
    }


def split_apply(repo: str, sprint_label: str, issue_num: int, children: list[dict]) -> dict:
    """Create the child tickets in the sprint, close the XL as not-planned, link them.

    The flow is treated as a transaction: if a child creation fails, or the
    original-handling step (strip sprint label / close) raises, any children
    already created on GitHub are orphans. We detect that, attempt best-effort
    compensation (closing the orphans), and return a structured failure that names
    the created child numbers and whether manual cleanup is still required
    (issue #1453). The happy path is unchanged.
    """
    srv = _server()
    gh = srv.github_client
    children = [c for c in (children or []) if (c.get("title") or "").strip()]
    if not children:
        return {"ok": False, "error": "no children to create"}

    # `gh issue create --label` hard-fails on a label the repo doesn't have, so
    # ensure both exist first (create_label is idempotent — ignores "already
    # exists"). The sprint label may not exist yet on a fresh repo, and
    # "split-child" is unique to this flow.
    try:
        gh.create_label(sprint_label, "ededed", "Sprint", repo_name=repo)
    except Exception:
        pass
    try:
        gh.create_label("split-child", "c5def5", "Created by XL ticket split", repo_name=repo)
    except Exception:
        pass

    # Fetch parent labels once; strip lifecycle/stage labels via build_child_labels().
    try:
        parent_issue = gh.get_issue(issue_num, repo_name=repo) or {}
        raw = parent_issue.get("labels") or []
        parent_label_names = [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in raw]
    except Exception:
        parent_label_names = []
    child_labels = build_child_labels(parent_label_names, sprint_label) + ["split-child"]

    created: list[dict] = []
    for c in children:
        title = c["title"].strip()[:240]
        body = (c.get("body") or "").strip() + f"\n\n_Split from #{issue_num}._"
        try:
            number, url = gh.create_issue(title, body, child_labels, repo_name=repo)
            created.append({"number": number, "url": url, "title": title})
        except Exception as exc:
            # Partial: child(ren) before this one already exist on GitHub.
            return _failure_result(gh, repo, created, exc)

    links = ", ".join(f"#{c['number']}" for c in created)
    # Handle the original: comment (cosmetic, best-effort) → strip sprint label →
    # close as not planned. The strip + close are the transactional steps: if
    # either raises, the children we just created are orphans, so roll them back.
    try:
        gh.add_comment(issue_num, f"Split into {links}; closing as not planned.", repo_name=repo)
    except Exception:
        pass
    try:
        gh.assign_sprint(issue_num, None, repo_name=repo)  # strip sprint label(s)
        gh.close_issue(issue_num, repo_name=repo, reason="not planned")
    except Exception as exc:
        return _failure_result(gh, repo, created, exc)

    for pfx in ("open_issues_body:", "open_issues:", "issues:"):
        try:
            gh.invalidate(pfx)
        except Exception:
            pass
    return {"ok": True, "closed": issue_num, "children": created}
