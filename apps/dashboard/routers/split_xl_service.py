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
    cmd = [
        "claude", "--model", "claude-sonnet-4-6",
        "--dangerously-skip-permissions", "-p", prompt,
    ]
    sub_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
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


def split_apply(repo: str, sprint_label: str, issue_num: int, children: list[dict]) -> dict:
    """Create the child tickets in the sprint, close the XL as not-planned, link them."""
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

    created: list[dict] = []
    for c in children:
        title = c["title"].strip()[:240]
        body = (c.get("body") or "").strip() + f"\n\n_Split from #{issue_num}._"
        try:
            number, url = gh.create_issue(title, body, [sprint_label, "split-child"], repo_name=repo)
            created.append({"number": number, "url": url, "title": title})
        except Exception as exc:
            return {"ok": False, "error": f"create_issue failed: {exc}", "created": created}

    links = ", ".join(f"#{c['number']}" for c in created)
    # Close the XL: comment → remove sprint label → close as not planned. Best-effort
    # on each step so a single gh hiccup doesn't strand a half-applied split.
    try:
        gh.add_comment(issue_num, f"Split into {links}; closing as not planned.", repo_name=repo)
    except Exception:
        pass
    try:
        gh.assign_sprint(issue_num, None, repo_name=repo)  # strip sprint label(s)
    except Exception:
        pass
    try:
        gh.close_issue(issue_num, repo_name=repo, reason="not planned")
    except Exception:
        pass
    for pfx in ("open_issues_body:", "open_issues:", "issues:"):
        try:
            gh.invalidate(pfx)
        except Exception:
            pass
    return {"ok": True, "closed": issue_num, "children": created}
