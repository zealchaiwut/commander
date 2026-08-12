"""Planning-only module: list dispatchable backlog issues for a sprint.

Extracted from pipeline.py (issue #2245) to break the last live import path
from apps/dashboard/ into the sprint_manager orchestrator.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Optional

import github_client
from services.logging import log as structured_log

_SUMMARY_TITLE_RE = re.compile(r"^Sprint \d+(\.\d+)?\s+Executive Summary$")
_REWORK_LABELS = frozenset({"needs-rework", "need-rework", "tester-rejected"})


def _r(repo_name: Optional[str]) -> str:
    return repo_name or github_client.repo()


def _classify(labels: set[str]) -> str:
    if "UAT-approved" in labels:
        return "done"
    if "UAT" in labels:
        return "uat"
    if "SIT" in labels:
        return "sit"
    if "in-progress" in labels:
        return "in-progress"
    return "backlog"


def _is_dispatchable(labels: set[str]) -> bool:
    if "blocked" in labels:
        return False
    cls = _classify(labels)
    if cls in ("backlog", "sit", "in-progress"):
        return True
    return bool(labels & _REWORK_LABELS)


def _list_labeled_open_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return all open issues carrying ``label``, excluding sprint-summary docs."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", r,
                "--label", label,
                "--state", "open",
                "--json", "number,title,labels",
                "--limit", "200",
            ],
            capture_output=True, text=True, check=True,
        )
        issues = json.loads(out.stdout)
        result = []
        for issue in issues:
            labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
            is_summary = (
                "sprint-summary" in labels_set
                or bool(_SUMMARY_TITLE_RE.match(issue.get("title", "") or ""))
            )
            if is_summary:
                structured_log.info(
                    "dispatch_skipped_summary",
                    f"skipping summary ticket #{issue['number']} from backlog",
                    issue_num=issue["number"],
                )
                continue
            result.append(issue)
        return sorted(result, key=lambda i: i["number"])
    except Exception as e:
        structured_log.warn(
            "list_issues_failed",
            f"could not list issues: {e}",
            label=label,
            exc=str(e),
        )
        return []


def list_backlog_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return open, dispatchable issues for ``label``, sorted by number."""
    result = []
    for issue in _list_labeled_open_issues(label, repo_name=repo_name):
        labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
        if _is_dispatchable(labels_set):
            result.append(issue)
    return result
