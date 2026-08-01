"""log_search_service.py — cross-run log search via ripgrep (issue #785).

Public API
----------
search_logs(**kwargs) → dict
_discover_log_dirs() → list[tuple[Path, str]]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ── Path setup ───────────────────────────────────────────────────────────────
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
_SERVICES_ROOT = _DASHBOARD_ROOT.parent.parent / "services" / "sprint_manager"
for _p in (str(_DASHBOARD_ROOT), str(_SERVICES_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import projects as _projects_module  # noqa: E402

from project_resolver import resolve_project_path as _project_root_path  # noqa: E402

DEFAULT_LIMIT = 500
DEFAULT_TIMEOUT = 10.0  # seconds for ripgrep subprocess

# rg executable — prefer the system rg, not the claude-code shim
_RG_BINARY = "rg"


def _commander_dir(project_root: Path) -> Path:
    return project_root / ".commander"


def _discover_log_dirs() -> list[tuple[Path, str]]:
    """Return [(log_dir, repo_string), ...] for all known projects."""
    try:
        all_projects = _projects_module.load_projects()
    except Exception:
        all_projects = []

    result: list[tuple[Path, str]] = []
    for proj in all_projects:
        repo = proj.get("repo", "")
        if not repo:
            continue
        project_root = _project_root_path(repo)
        log_dir = _commander_dir(project_root) / "logs"
        if log_dir.exists():
            result.append((log_dir, repo))
    return result


def _load_sprint_state_issues(log_dir: Path) -> dict[str, list[int]]:
    """Return {sprint_label: [issue_num, ...]} from sprint state JSONs."""
    sprints_dir = log_dir.parent / "sprints"
    if not sprints_dir.exists():
        return {}
    mapping: dict[str, list[int]] = {}
    for state_path in sprints_dir.glob("sprint-*-state.json"):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        label = data.get("sprint_label", "")
        if not label:
            continue
        issues = [i.get("number") for i in (data.get("issues") or [])
                  if isinstance(i.get("number"), int)]
        mapping[label] = issues
    return mapping


def _prefilter_files(
    log_dirs: list[tuple[Path, str]],
    *,
    sprint: Optional[str],
    issue: Optional[int],
) -> list[tuple[Path, str, Optional[int], Optional[str]]]:
    """Return [(file_path, repo, issue_num, sprint_label), ...] after pre-filtering.

    Uses sprint state JSONs (the DB index) to narrow the file list before
    ripgrep runs.
    """
    results: list[tuple[Path, str, Optional[int], Optional[str]]] = []

    for log_dir, repo in log_dirs:
        # ── issue-specific file ───────────────────────────────────────────────
        if issue is not None:
            p = log_dir / f"sprint-issue-{issue}.log"
            if p.exists():
                # Determine sprint from state JSONs
                sprint_for_issue: Optional[str] = None
                state_map = _load_sprint_state_issues(log_dir)
                for lbl, nums in state_map.items():
                    if issue in nums:
                        sprint_for_issue = lbl
                        break
                if sprint is None or sprint_for_issue == sprint:
                    results.append((p, repo, issue, sprint_for_issue))
            continue  # skip other file types when issue is specified

        # ── sprint-run log ────────────────────────────────────────────────────
        if sprint is not None:
            for p in sorted(log_dir.glob(f"sprint-run-{sprint}-*.log")):
                results.append((p, repo, None, sprint))
            # Also include per-issue logs for that sprint
            state_map = _load_sprint_state_issues(log_dir)
            for inum in state_map.get(sprint, []):
                p = log_dir / f"sprint-issue-{inum}.log"
                if p.exists():
                    results.append((p, repo, inum, sprint))
            continue

        # ── no structural filter: include all log files ───────────────────────
        for p in log_dir.glob("sprint-issue-*.log"):
            m = re.search(r"sprint-issue-(\d+)\.log$", p.name)
            inum = int(m.group(1)) if m else None
            results.append((p, repo, inum, None))
        for p in log_dir.glob("sprint-run-*.log"):
            m = re.search(r"sprint-run-(sprint-[\d.]+)-", p.name)
            slabel = m.group(1) if m else None
            results.append((p, repo, None, slabel))
        for p in log_dir.glob("structured-*.log"):
            results.append((p, repo, None, None))

    return results


def _parse_rg_line(
    line: str,
    file_meta: dict[Path, tuple[str, Optional[int], Optional[str]]],
) -> Optional[dict]:
    """Parse one rg output line ``path:linenum:text`` → match dict or None."""
    # rg --no-heading --line-number --with-filename: PATH:LINENUM:TEXT
    idx1 = line.find(":")
    if idx1 < 0:
        return None
    idx2 = line.find(":", idx1 + 1)
    if idx2 < 0:
        return None
    path_str = line[:idx1]
    try:
        line_num = int(line[idx1 + 1:idx2])
    except ValueError:
        return None
    text = line[idx2 + 1:]

    p = Path(path_str)
    repo, issue_num, sprint_label = file_meta.get(p, ("", None, None))

    # Try to enrich from structured JSONL lines
    agent: Optional[str] = None
    if p.name.startswith("structured-"):
        try:
            obj = json.loads(text)
            issue_num = issue_num or obj.get("issue_num")
            sprint_label = sprint_label or obj.get("sprint_label")
            agent = obj.get("agent_role")
        except (json.JSONDecodeError, ValueError):
            pass
    elif p.name.startswith("sprint-run-"):
        agent = "dispatch"
    else:
        agent = None  # coder or tester — indistinguishable in plain-text logs

    return {
        "sprint": sprint_label,
        "issue": issue_num,
        "agent": agent,
        "line_offset": line_num - 1,  # 0-based for UI deep-linking
        "text": text.rstrip(),
        "file": p.name,
    }


def _build_rg_pattern(
    q: Optional[str],
    level: Optional[str],
    event_type: Optional[str],
) -> Optional[str]:
    """Build the ripgrep pattern from text-filter params.

    Returns None if no text filter is given (caller should use '.' to match all).
    """
    if q:
        return re.escape(q) if not _is_safe_regex(q) else q
    if level:
        # Match the level token in plain text or JSON
        return re.escape(level.upper())
    if event_type:
        return re.escape(event_type)
    return None


def _is_safe_regex(s: str) -> bool:
    """Return True if s can be used as a raw regex without re-escaping."""
    try:
        re.compile(s)
        return True
    except re.error:
        return False


def search_logs(
    *,
    project: Optional[str] = None,
    sprint: Optional[str] = None,
    issue: Optional[int] = None,
    agent: Optional[str] = None,
    event_type: Optional[str] = None,
    level: Optional[str] = None,
    time_range: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    timeout_secs: float = DEFAULT_TIMEOUT,
) -> dict:
    """Search log files using ripgrep with DB-indexed pre-filtering.

    Returns
    -------
    {
        "matches": [...],   # up to limit items
        "total": int,       # count before capping
        "capped": bool,
        "timed_out": bool,
        "query_ms": int,
    }
    """
    t0 = time.monotonic()

    # ── discover project log directories ────────────────────────────────────
    log_dirs = _discover_log_dirs()
    if project:
        log_dirs = [(d, r) for d, r in log_dirs
                    if r == project or r.split("/")[-1] == project]

    # ── pre-filter files using sprint state JSONs ────────────────────────────
    file_list = _prefilter_files(log_dirs, sprint=sprint, issue=issue)
    if not file_list:
        return {"matches": [], "total": 0, "capped": False,
                "timed_out": False, "query_ms": int((time.monotonic() - t0) * 1000)}

    file_meta: dict[Path, tuple[str, Optional[int], Optional[str]]] = {
        p: (repo, inum, slabel) for p, repo, inum, slabel in file_list
    }
    paths = [str(p) for p, _, _, _ in file_list]

    # ── build ripgrep command ────────────────────────────────────────────────
    pattern = _build_rg_pattern(q, level, event_type) or "."

    rg_cmd = [
        _RG_BINARY,
        "--line-number",
        "--no-heading",
        "--with-filename",
        "--text",               # treat binary as text
        "-m", str(limit + 1),  # fetch one extra to detect truncation
        "--",
        pattern,
    ] + paths

    # ── run ripgrep with timeout ─────────────────────────────────────────────
    timed_out = False
    rg_output = ""
    try:
        result = subprocess.run(
            rg_cmd,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
        )
        rg_output = result.stdout or ""
    except subprocess.TimeoutExpired:
        timed_out = True
    except FileNotFoundError:
        # rg not installed — return empty rather than crashing
        pass

    # ── parse matches ────────────────────────────────────────────────────────
    raw_matches: list[dict] = []
    for line in rg_output.splitlines():
        parsed = _parse_rg_line(line, file_meta)
        if parsed is None:
            continue
        # Post-filter by agent (can't pre-filter at file level for plain-text)
        if agent and parsed.get("agent") and parsed["agent"] != agent:
            continue
        raw_matches.append(parsed)

    total = len(raw_matches)
    capped = total > limit
    matches = raw_matches[:limit]

    return {
        "matches": matches,
        "total": total,
        "capped": capped,
        "timed_out": timed_out,
        "query_ms": int((time.monotonic() - t0) * 1000),
    }
