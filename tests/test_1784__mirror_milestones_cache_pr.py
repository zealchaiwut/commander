"""Tests for issue #1784 — mirror-back list_milestones; cache PR lookups.

AC mapping:
AC1  github_client.py contains exactly one def list_milestones (no F811)
AC2  list_milestones reads from db.get_mirrored_milestones when populated;
     zero gh subprocesses in that path
AC3  list_milestones falls back to live gh api when mirror is empty
AC4  Return shape preserved: active_milestone accepts both dueOn and due_on
     without modification
AC5  get_pr is wrapped with _cached() using key pr:{repo}:{number}, 30s TTL
AC6  find_open_pr_for_head is wrapped with _cached() using key
     pr_head:{repo}:{branch}, 30s TTL
AC7  PR-mutating ops (merge_pr) invalidate all cache entries matching pr: prefix
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

import github_client  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_subprocess_json(payload) -> MagicMock:
    r = MagicMock()
    r.returncode = 0
    r.stdout = json.dumps(payload)
    return r


def _milestone(number: int, title: str = "", state: str = "open",
               due_on: str | None = None) -> dict:
    return {
        "number": number,
        "title": title or f"M{number}",
        "state": state,
        "due_on": due_on,
        "description": "",
    }


# ── AC1: single definition (no F811) ─────────────────────────────────────────

def test_ac1_single_list_milestones_definition():
    """AC1: github_client.py has exactly one def list_milestones."""
    src = (_REPO_ROOT / "apps" / "dashboard" / "github_client.py").read_text()
    tree = ast.parse(src)
    defs = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "list_milestones"
    ]
    assert len(defs) == 1, (
        f"Expected exactly 1 def list_milestones in github_client.py, "
        f"found {len(defs)} at lines {[d.lineno for d in defs]}"
    )


# ── AC2: mirror-backed, zero gh subprocesses ─────────────────────────────────

def test_ac2_mirror_populated_zero_gh_calls():
    """AC2: when mirror returns milestones, no gh subprocess is spawned."""
    milestones = [_milestone(1, "Sprint X"), _milestone(2, "Sprint Y")]
    subprocess_calls: list = []

    def counting_subprocess(args, **kwargs):
        subprocess_calls.append(list(str(a) for a in args))
        return _fake_subprocess_json([])

    github_client.invalidate()
    with patch.object(github_client, "_mirror_milestones", return_value=milestones):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = github_client.list_milestones("owner/repo")

    gh_calls = [c for c in subprocess_calls if "gh" in c]
    assert gh_calls == [], (
        f"Expected 0 gh subprocess calls when mirror populated, "
        f"got {len(gh_calls)}: {gh_calls}"
    )
    assert result == milestones


def test_ac2_mirror_returns_cached_result():
    """AC2: mirror result is returned as-is (no transformation)."""
    milestones = [_milestone(3, "M3", due_on="2026-09-01T00:00:00Z")]
    github_client.invalidate()
    with patch.object(github_client, "_mirror_milestones", return_value=milestones):
        result = github_client.list_milestones("owner/repo")
    assert result == milestones
    assert result[0]["due_on"] == "2026-09-01T00:00:00Z"


# ── AC3: live fallback when mirror empty ──────────────────────────────────────

def test_ac3_fallback_when_mirror_empty():
    """AC3: when mirror is empty, falls back to live gh api."""
    live_payload = [
        {"number": 10, "title": "Live M10", "state": "open", "due_on": None,
         "description": ""},
    ]
    subprocess_calls: list = []

    def fake_subprocess(args, **kwargs):
        subprocess_calls.append(list(str(a) for a in args))
        return _fake_subprocess_json(live_payload)

    github_client.invalidate()
    with patch.object(github_client, "_mirror_milestones", return_value=[]):
        with patch("subprocess.run", side_effect=fake_subprocess):
            result = github_client.list_milestones("owner/repo")

    api_calls = [c for c in subprocess_calls if "gh" in c and "api" in c]
    assert len(api_calls) >= 1, (
        f"Expected ≥1 gh api call when mirror is empty, got {len(api_calls)}"
    )
    assert result == live_payload


def test_ac3_fallback_returns_none_mirror():
    """AC3: mirror returning None also triggers live fallback."""
    live_payload = [{"number": 11, "title": "Live", "state": "open",
                     "due_on": None, "description": ""}]
    subprocess_calls: list = []

    def fake_subprocess(args, **kwargs):
        subprocess_calls.append(list(str(a) for a in args))
        return _fake_subprocess_json(live_payload)

    github_client.invalidate()
    with patch.object(github_client, "_mirror_milestones", return_value=None):
        with patch("subprocess.run", side_effect=fake_subprocess):
            result = github_client.list_milestones("owner/repo")

    api_calls = [c for c in subprocess_calls if "gh" in c and "api" in c]
    assert len(api_calls) >= 1
    assert result == live_payload


# ── AC4: return shape — both dueOn and due_on work ───────────────────────────

def test_ac4_active_milestone_accepts_due_on():
    """AC4: active_milestone handles due_on field (mirror/REST shape)."""
    sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
    from sprint_planner import active_milestone  # noqa: PLC0415

    milestones = [
        {"number": 1, "title": "Future", "state": "open",
         "due_on": "2027-01-01T00:00:00Z"},
        {"number": 2, "title": "Sooner", "state": "open",
         "due_on": "2026-08-01T00:00:00Z"},
    ]
    result = active_milestone(milestones)
    assert result is not None
    assert result["number"] == 2, "Should pick nearest due date"


def test_ac4_active_milestone_accepts_dueOn():
    """AC4: active_milestone handles dueOn field (legacy gh CLI shape)."""
    sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
    from sprint_planner import active_milestone  # noqa: PLC0415

    milestones = [
        {"number": 1, "title": "Future", "state": "open",
         "dueOn": "2027-01-01T00:00:00Z"},
        {"number": 2, "title": "Sooner", "state": "open",
         "dueOn": "2026-08-01T00:00:00Z"},
    ]
    result = active_milestone(milestones)
    assert result is not None
    assert result["number"] == 2


# ── AC5: get_pr is cached (key pr:{repo}:{number}, 30s TTL) ──────────────────

def test_ac5_get_pr_two_calls_one_subprocess():
    """AC5: two get_pr calls for same PR within 30s → exactly one gh subprocess."""
    pr_payload = {
        "number": 42, "title": "Fix foo", "state": "open",
        "html_url": "https://github.com/owner/repo/pull/42",
        "body": "", "head": {"ref": "feature/42-foo"}, "base": {"ref": "develop"},
    }
    call_count = [0]

    def counting_subprocess(args, **kwargs):
        call_count[0] += 1
        return _fake_subprocess_json(pr_payload)

    github_client.invalidate()
    with patch("subprocess.run", side_effect=counting_subprocess):
        r1 = github_client.get_pr(42, "owner/repo")
        r2 = github_client.get_pr(42, "owner/repo")

    assert call_count[0] == 1, (
        f"Expected exactly 1 subprocess call for 2 get_pr requests, got {call_count[0]}"
    )
    assert r1["number"] == 42
    assert r2["number"] == 42


def test_ac5_get_pr_cache_key_per_pr_number():
    """AC5: different PR numbers each make their own subprocess call."""
    payloads = {
        42: {"number": 42, "title": "PR 42", "state": "open", "html_url": "",
             "body": "", "head": {"ref": "f/42"}, "base": {"ref": "develop"}},
        43: {"number": 43, "title": "PR 43", "state": "open", "html_url": "",
             "body": "", "head": {"ref": "f/43"}, "base": {"ref": "develop"}},
    }
    call_count = [0]

    def counting_subprocess(args, **kwargs):
        call_count[0] += 1
        # Extract PR number from the API URL arg
        url_arg = next((a for a in args if "pulls/" in str(a)), "pulls/42")
        num = int(str(url_arg).split("pulls/")[-1].split("?")[0])
        return _fake_subprocess_json(payloads.get(num, payloads[42]))

    github_client.invalidate()
    with patch("subprocess.run", side_effect=counting_subprocess):
        github_client.get_pr(42, "owner/repo")
        github_client.get_pr(43, "owner/repo")
        github_client.get_pr(42, "owner/repo")  # third call, same as first — cached

    assert call_count[0] == 2, (
        f"Expected 2 subprocess calls (one per unique PR), got {call_count[0]}"
    )


# ── AC6: find_open_pr_for_head is cached ─────────────────────────────────────

def test_ac6_find_open_pr_two_calls_one_subprocess():
    """AC6: two find_open_pr_for_head calls for same branch → one gh subprocess."""
    pr_list = [
        {"number": 55, "title": "Feature branch PR", "state": "open",
         "html_url": "https://github.com/owner/repo/pull/55",
         "body": "", "head": {"ref": "feature/55-bar"}, "base": {"ref": "develop"}},
    ]
    call_count = [0]

    def counting_subprocess(args, **kwargs):
        call_count[0] += 1
        return _fake_subprocess_json(pr_list)

    github_client.invalidate()
    with patch("subprocess.run", side_effect=counting_subprocess):
        r1 = github_client.find_open_pr_for_head("feature/55-bar", "owner/repo")
        r2 = github_client.find_open_pr_for_head("feature/55-bar", "owner/repo")

    assert call_count[0] == 1, (
        f"Expected exactly 1 subprocess call for 2 find_open_pr_for_head requests, "
        f"got {call_count[0]}"
    )
    assert r1 is not None
    assert r1["number"] == 55
    assert r2 == r1


def test_ac6_find_open_pr_none_result_cached():
    """AC6: None result (no open PR) is also cached."""
    call_count = [0]

    def counting_subprocess(args, **kwargs):
        call_count[0] += 1
        return _fake_subprocess_json([])

    github_client.invalidate()
    with patch("subprocess.run", side_effect=counting_subprocess):
        r1 = github_client.find_open_pr_for_head("feature/99-baz", "owner/repo")
        r2 = github_client.find_open_pr_for_head("feature/99-baz", "owner/repo")

    assert call_count[0] == 1, (
        f"Expected 1 subprocess for no-PR result, got {call_count[0]}"
    )
    assert r1 is None
    assert r2 is None


# ── AC7: PR mutation invalidates pr: cache ────────────────────────────────────

def test_ac7_merge_pr_invalidates_pr_cache():
    """AC7: calling merge_pr invalidates cached get_pr results."""
    pr_payload_before = {
        "number": 77, "title": "Before merge", "state": "open",
        "html_url": "", "body": "",
        "head": {"ref": "feature/77-x"}, "base": {"ref": "develop"},
    }
    pr_payload_after = {
        "number": 77, "title": "After merge", "state": "merged",
        "html_url": "", "body": "",
        "head": {"ref": "feature/77-x"}, "base": {"ref": "develop"},
    }
    call_count = [0]
    return_after_merge = [False]

    def counting_subprocess(args, **kwargs):
        call_count[0] += 1
        args_list = [str(a) for a in args]
        # merge_pr calls: ["gh", "pr", "merge", ...]
        if "pr" in args_list and "merge" in args_list:
            return_after_merge[0] = True
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            return r
        payload = pr_payload_after if return_after_merge[0] else pr_payload_before
        return _fake_subprocess_json(payload)

    github_client.invalidate()
    with patch("subprocess.run", side_effect=counting_subprocess):
        # First get_pr — populates cache
        r1 = github_client.get_pr(77, "owner/repo")
        assert r1["state"].lower() in ("open", "open"), f"Got state: {r1['state']}"

        # Merge — should invalidate pr: cache entries
        github_client.merge_pr(77, "owner/repo")

        # Second get_pr — cache was invalidated, should re-issue subprocess
        r2 = github_client.get_pr(77, "owner/repo")

    # Should have: 1 (get_pr) + 1 (merge_pr) + 1 (get_pr after invalidation) = 3
    assert call_count[0] == 3, (
        f"Expected 3 subprocess calls (get_pr + merge + get_pr after invalidation), "
        f"got {call_count[0]}"
    )
    assert r2["title"] == "After merge", (
        f"Expected post-merge state after invalidation, got {r2['title']!r}"
    )


def test_ac7_merge_pr_invalidates_pr_head_cache():
    """AC7: merge_pr also invalidates find_open_pr_for_head cache."""
    pr_list_open = [
        {"number": 88, "title": "Open PR", "state": "open", "html_url": "",
         "body": "", "head": {"ref": "feature/88-y"}, "base": {"ref": "develop"}},
    ]
    call_count = [0]
    merged = [False]

    def counting_subprocess(args, **kwargs):
        call_count[0] += 1
        args_list = [str(a) for a in args]
        if "pr" in args_list and "merge" in args_list:
            merged[0] = True
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            return r
        # After merge, PR is gone (empty list)
        return _fake_subprocess_json([] if merged[0] else pr_list_open)

    github_client.invalidate()
    with patch("subprocess.run", side_effect=counting_subprocess):
        r1 = github_client.find_open_pr_for_head("feature/88-y", "owner/repo")
        assert r1 is not None

        github_client.merge_pr(88, "owner/repo")

        r2 = github_client.find_open_pr_for_head("feature/88-y", "owner/repo")

    # 1 (find) + 1 (merge) + 1 (find after invalidation) = 3
    assert call_count[0] == 3, (
        f"Expected 3 subprocess calls, got {call_count[0]}"
    )
    assert r2 is None, "After merge, open PR should not be found"
