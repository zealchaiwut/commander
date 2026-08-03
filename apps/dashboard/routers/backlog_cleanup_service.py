"""Backlog cleanup — close test scaffolding and stale follow-up issues.

Scans open backlog tickets (no sprint label, backlog status) and proposes:
  * test_issue — titles from UAT/test runs (e.g. "Test Ticket from UAT")
  * follow_up_redundant — duplicate ``[follow-up]`` titles (keeps newest #)
  * follow_up_low_impact — ``[follow-up]`` tickets whose body severity is ``nit``
"""
from __future__ import annotations

import re
from typing import Any

import github_client as gc
from sprint_label_re import SPRINT_LABEL_RE

_SPRINT_LABEL_RE = SPRINT_LABEL_RE
_FOLLOW_UP_TITLE_RE = re.compile(r"^\[follow-up\]", re.I)
_SEVERITY_RE = re.compile(r"##\s*Severity\s*\n\s*(nit|suggestion)\b", re.I)
_TEST_TITLE_EXACT = frozenset({
    "test",
    "test.",
    "test ticket",
    "test issue",
    "test ticket from uat",
})
_TEST_TITLE_PREFIX_RE = re.compile(
    r"^test(?:\s+ticket|\s+issue)?(?:\s+from\s+uat)?\b",
    re.I,
)


def _issue_sprint_label(issue: dict) -> str | None:
    for lbl in issue.get("labels") or []:
        name = (lbl.get("name") if isinstance(lbl, dict) else lbl) or ""
        if _SPRINT_LABEL_RE.match(name):
            return name
    return None


def is_backlog_issue(issue: dict) -> bool:
    """Open issue with no sprint label and backlog column classification."""
    if (issue.get("state") or "").lower() == "closed":
        return False
    if _issue_sprint_label(issue):
        return False
    return gc.classify_issue(issue) == "backlog"


def is_test_issue_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    if t.lower() in _TEST_TITLE_EXACT:
        return True
    return bool(_TEST_TITLE_PREFIX_RE.match(t))


def normalize_follow_up_title(title: str) -> str:
    return re.sub(r"^\[follow-up\]\s*", "", title or "", flags=re.I).strip().lower()


def follow_up_severity(body: str) -> str | None:
    m = _SEVERITY_RE.search(body or "")
    return m.group(1).lower() if m else None


def is_follow_up_title(title: str) -> bool:
    return bool(_FOLLOW_UP_TITLE_RE.match((title or "").strip()))


def _candidate(number: int, title: str, category: str, reason: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "category": category,
        "reason": reason,
    }


def scan_backlog(repo: str) -> dict[str, Any]:
    """Return cleanup candidates for a project's open backlog."""
    issues = gc.list_open_issues_with_body(repo_name=repo, limit=200)
    backlog = [i for i in issues if is_backlog_issue(i)]

    candidates: list[dict[str, Any]] = []
    seen: set[int] = set()
    follow_ups: list[dict] = []

    for iss in backlog:
        num = int(iss["number"])
        title = iss.get("title") or ""
        if is_test_issue_title(title):
            candidates.append(_candidate(
                num, title, "test_issue",
                "Test scaffolding title — safe to close",
            ))
            seen.add(num)
        elif is_follow_up_title(title):
            follow_ups.append(iss)

    by_norm: dict[str, list[dict]] = {}
    for iss in follow_ups:
        key = normalize_follow_up_title(iss.get("title") or "")
        if not key:
            key = f"__empty__:{iss['number']}"
        by_norm.setdefault(key, []).append(iss)

    for group in by_norm.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda i: int(i["number"]))
        keeper = group[-1]
        for dup in group[:-1]:
            num = int(dup["number"])
            if num in seen:
                continue
            candidates.append(_candidate(
                num,
                dup.get("title") or "",
                "follow_up_redundant",
                f"Duplicate follow-up — keeping newer #{keeper['number']}",
            ))
            seen.add(num)

    for iss in follow_ups:
        num = int(iss["number"])
        if num in seen:
            continue
        if follow_up_severity(iss.get("body") or "") == "nit":
            candidates.append(_candidate(
                num,
                iss.get("title") or "",
                "follow_up_low_impact",
                "Follow-up marked nit severity in review body",
            ))
            seen.add(num)

    candidates.sort(key=lambda c: c["number"], reverse=True)
    counts = {
        "test_issue": sum(1 for c in candidates if c["category"] == "test_issue"),
        "follow_up_redundant": sum(
            1 for c in candidates if c["category"] == "follow_up_redundant"
        ),
        "follow_up_low_impact": sum(
            1 for c in candidates if c["category"] == "follow_up_low_impact"
        ),
    }
    return {
        "repo": repo,
        "backlog_count": len(backlog),
        "candidates": candidates,
        "counts": counts,
        "total": len(candidates),
    }


def apply_backlog_cleanup(
    repo: str,
    issue_numbers: list[int],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Close the selected backlog issues on GitHub."""
    preview = scan_backlog(repo)
    allowed = {int(c["number"]) for c in preview["candidates"]}
    to_close = [int(n) for n in issue_numbers if int(n) in allowed]

    if dry_run:
        return {
            "dry_run": True,
            "repo": repo,
            "to_close": to_close,
            "closed": [],
            "errors": [],
        }

    closed: list[int] = []
    errors: list[str] = []
    for num in to_close:
        try:
            gc.close_issue(num, repo_name=repo, reason="not planned")
            closed.append(num)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"#{num}: {exc}")

    if closed:
        gc.invalidate(f"open_issues:{repo}")
        gc.invalidate(f"open_issues_body:{repo}")
        gc.invalidate(f"all_open_issues:{repo}")

    return {
        "dry_run": False,
        "repo": repo,
        "to_close": to_close,
        "closed": closed,
        "errors": errors,
    }
