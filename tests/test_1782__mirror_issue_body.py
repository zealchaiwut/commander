"""Tests for issue #1782 — read issue bodies from mirror instead of per-ticket gh api fetches.

AC mapping:
AC1  _mirror_issue_body helper exists in dispatch.py; accepts repo, issue_num, runner, sync_ts
AC2  Returns body from mirror on hit (runner never called)
AC3  Falls back to live fetch when mirror record's updated_at predates sync_ts (stale-hit guard)
AC3b Fresh mirror record (updated_at >= sync_ts) does NOT trigger live fetch
AC4  _fetch_dispatch_issue_body updated to use mirror via helper (no subprocess.run on mirror hit)
AC5  _build_design_block fallback (sprint_manager.py) updated: mirror hit skips subprocess.run
AC6  fetch_issue in estimate_issue.py reads from mirror; runner not called on mirror hit
AC7  With fully populated mirror, zero per-ticket gh api body fetches across N tickets
AC8  Mirror miss → exactly one live fetch per missing ticket
AC9  Stale hit → exactly one live fetch per stale ticket
AC10 Body from mirror is byte-for-byte identical to body from live fetch for same fixture
AC11 estimate_issue.py write paths (gh issue comment, gh issue edit) are not modified
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))

import dispatch  # noqa: E402
import estimate_issue  # noqa: E402


# ── fixture helpers ───────────────────────────────────────────────────────────

def _make_mirror_issue(
    issue_num: int,
    body: str = "Issue body text",
    updated_at: str = "2026-07-09T10:00:00Z",
) -> dict:
    return {
        "number": issue_num,
        "title": f"Issue #{issue_num}",
        "state": "open",
        "labels": [{"name": "in-progress"}],
        "body": body,
        "updatedAt": updated_at,
        "updated_at": updated_at,
    }


def _make_runner(response_body: str = "Live body", issue_num: int = 0):
    """Return a runner function that returns a successful gh api response."""
    def runner(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        num = issue_num or int(args[-1].split("/")[-1])
        r.stdout = json.dumps({"number": num, "title": f"Issue #{num}", "body": response_body, "labels": []})
        r.stderr = ""
        return r
    return runner


# ── AC1: helper exists and has correct signature ──────────────────────────────

def test_ac1_mirror_issue_body_exists_in_dispatch():
    """AC1: _mirror_issue_body is callable in dispatch.py."""
    assert hasattr(dispatch, "_mirror_issue_body"), "_mirror_issue_body not found in dispatch"
    assert callable(dispatch._mirror_issue_body)


def test_ac1_helper_signature_has_required_params():
    """AC1: helper accepts repo, issue_num, runner, sync_ts parameters."""
    sig = inspect.signature(dispatch._mirror_issue_body)
    params = set(sig.parameters)
    assert "issue_num" in params, f"Missing 'issue_num' param; got: {params}"
    assert "runner" in params, f"Missing 'runner' param; got: {params}"
    assert "sync_ts" in params, f"Missing 'sync_ts' param; got: {params}"


# ── AC2: mirror hit → body returned, runner not called ───────────────────────

def test_ac2_mirror_hit_returns_body_without_live_fetch():
    """AC2: when mirror has the issue, body is returned and runner is never called."""
    issue = _make_mirror_issue(123, body="Mirror body text")
    runner_calls = []

    def counting_runner(args, **kwargs):
        runner_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"body": "Should not be used"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=issue):
        result = dispatch._mirror_issue_body("owner/repo", 123, runner=counting_runner)

    assert result == "Mirror body text"
    assert runner_calls == [], f"Runner called {len(runner_calls)} time(s); expected 0"


def test_ac2_no_repo_returns_none():
    """AC2: when repo is empty/None, returns None (no repo to query)."""
    result = dispatch._mirror_issue_body("", 42)
    assert result is None


# ── AC3: stale-hit guard ──────────────────────────────────────────────────────

def test_ac3_stale_hit_triggers_live_fetch():
    """AC3: mirror record with updatedAt > sync_ts triggers live fetch.

    Stale = issue was modified AFTER the mirror last synced (sync_ts), so the
    mirror body may be outdated. Correct semantics per issue #1915.
    """
    issue = _make_mirror_issue(456, body="Old body", updated_at="2026-07-09T13:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 456, "body": "Fresh live body"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=issue):
        result = dispatch._mirror_issue_body(
            "owner/repo", 456,
            runner=counting_runner,
            sync_ts="2026-07-09T12:00:00Z",
        )

    assert len(live_calls) == 1, f"Expected 1 live fetch for stale record, got {len(live_calls)}"
    assert result == "Fresh live body"


def test_ac3b_fresh_mirror_record_no_live_fetch():
    """AC3b: mirror record with updatedAt <= sync_ts is fresh — no live fetch.

    Fresh = issue was last modified at or before the mirror's last sync time,
    so the mirror body is current. Correct semantics per issue #1915.
    """
    issue = _make_mirror_issue(789, body="Fresh body", updated_at="2026-07-09T09:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 789, "body": "Live body"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=issue):
        result = dispatch._mirror_issue_body(
            "owner/repo", 789,
            runner=counting_runner,
            sync_ts="2026-07-09T10:00:00Z",
        )

    assert live_calls == [], f"Expected 0 live fetches for fresh record, got {len(live_calls)}"
    assert result == "Fresh body"


# ── AC7: N-ticket, zero live fetches when mirror populated ───────────────────

def test_ac7_zero_body_fetches_for_n_tickets_when_mirror_populated():
    """AC7: with fully populated mirror, N ticket body lookups trigger zero live fetches."""
    ticket_numbers = list(range(1000, 1020))  # 20 tickets
    mirror_data = {n: _make_mirror_issue(n, body=f"Body #{n}") for n in ticket_numbers}
    live_call_count = [0]

    def counting_runner(args, **kwargs):
        live_call_count[0] += 1
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 0, "body": "live"})
        r.stderr = ""
        return r

    def mirror_reader(repo, n):
        return mirror_data.get(n)

    with patch("github_client._mirror_issue", side_effect=mirror_reader):
        for n in ticket_numbers:
            dispatch._mirror_issue_body("owner/repo", n, runner=counting_runner)

    assert live_call_count[0] == 0, (
        f"Expected 0 live fetches for {len(ticket_numbers)} mirror-populated tickets, "
        f"got {live_call_count[0]}"
    )


# ── AC8: mirror miss → exactly one live fetch per missing ticket ───────────────

def test_ac8_mirror_miss_triggers_exactly_one_live_fetch():
    """AC8: when mirror has no record, exactly one live fetch is made."""
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 99, "body": "Fetched body"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=None):
        result = dispatch._mirror_issue_body("owner/repo", 99, runner=counting_runner)

    assert len(live_calls) == 1, f"Expected exactly 1 live fetch, got {len(live_calls)}"
    assert result == "Fetched body"


def test_ac8_n_missing_tickets_trigger_n_live_fetches():
    """AC8: N missing tickets each trigger exactly 1 live fetch (total = N)."""
    missing = [200, 201, 202]
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 0, "body": "body"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=None):
        for n in missing:
            dispatch._mirror_issue_body("owner/repo", n, runner=counting_runner)

    assert len(live_calls) == len(missing), (
        f"Expected {len(missing)} live fetches, got {len(live_calls)}"
    )


# ── AC9: stale hit → exactly one live fetch per stale ticket ──────────────────

def test_ac9_stale_hit_triggers_exactly_one_live_fetch_per_stale_ticket():
    """AC9: each stale mirror record (updatedAt > sync_ts) triggers exactly 1 live fetch.

    Stale = issue was modified AFTER the mirror last synced. Correct semantics per #1915.
    """
    sync_ts = "2026-07-09T12:00:00Z"
    stale_issues = {
        300: _make_mirror_issue(300, body="Old 300", updated_at="2026-07-09T13:00:00Z"),
        301: _make_mirror_issue(301, body="Old 301", updated_at="2026-07-09T14:00:00Z"),
    }
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 0, "body": "fresh"})
        r.stderr = ""
        return r

    def mirror_reader(repo, n):
        return stale_issues.get(n)

    with patch("github_client._mirror_issue", side_effect=mirror_reader):
        for n in [300, 301]:
            dispatch._mirror_issue_body("owner/repo", n, runner=counting_runner, sync_ts=sync_ts)

    assert len(live_calls) == 2, (
        f"Expected 2 live fetches (one per stale ticket), got {len(live_calls)}"
    )


# ── AC10: body from mirror identical to body from live ───────────────────────

def test_ac10_body_from_mirror_matches_live_fetch():
    """AC10: body served from mirror is byte-for-byte identical to live fetch for same fixture."""
    body_text = "## What & Why\nTest content for the issue body.\n\n## Acceptance Criteria\n- [ ] Something"
    issue_num = 400

    mirror = _make_mirror_issue(issue_num, body=body_text)
    live_runner = _make_runner(response_body=body_text, issue_num=issue_num)

    with patch("github_client._mirror_issue", return_value=mirror):
        body_from_mirror = dispatch._mirror_issue_body("owner/repo", issue_num, runner=live_runner)

    with patch("github_client._mirror_issue", return_value=None):
        body_from_live = dispatch._mirror_issue_body("owner/repo", issue_num, runner=live_runner)

    assert body_from_mirror == body_from_live, (
        f"Mirror body: {body_from_mirror!r} != Live body: {body_from_live!r}"
    )


# ── AC4: _fetch_dispatch_issue_body uses mirror ───────────────────────────────

def test_ac4_fetch_dispatch_issue_body_uses_mirror_no_subprocess():
    """AC4: _fetch_dispatch_issue_body returns body from mirror without calling subprocess.run."""
    body_text = "Design body from mirror"
    issue = _make_mirror_issue(500, body=body_text)
    subprocess_calls = []

    def counting_subprocess(args, **kwargs):
        if "gh" in str(args):
            subprocess_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 500, "body": "Should not be used"})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=issue):
        with patch("subprocess.run", side_effect=counting_subprocess):
            result = dispatch._fetch_dispatch_issue_body("owner/repo", 500)

    assert result == body_text
    gh_api_calls = [c for c in subprocess_calls if "repos" in str(c)]
    assert gh_api_calls == [], f"Expected 0 gh api calls, got: {gh_api_calls}"


def test_ac4_fetch_dispatch_issue_body_returns_none_when_no_repo():
    """AC4: _fetch_dispatch_issue_body returns None when eff_repo is empty."""
    result = dispatch._fetch_dispatch_issue_body("", 42)
    assert result is None

    result2 = dispatch._fetch_dispatch_issue_body(None, 42)
    assert result2 is None


# ── AC5: _build_design_block updated to use mirror ───────────────────────────

def test_ac5_build_design_block_uses_mirror_when_body_none(tmp_path):
    """AC5: _build_design_block skips subprocess.run when mirror has the body (issue_body=None)."""
    from services.sprint_manager.sprint_manager import _build_design_block

    (tmp_path / "DESIGN.md").write_text(
        "# Design\n## Architecture Overview\nContent here.\n", encoding="utf-8"
    )

    body_text = "## What & Why\nSome issue body without refs."
    issue = _make_mirror_issue(600, body=body_text)
    subprocess_calls = []

    def counting_subprocess(args, **kwargs):
        subprocess_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 600, "body": body_text})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=issue):
        with patch("subprocess.run", side_effect=counting_subprocess):
            _build_design_block(600, "owner/repo", tmp_path, issue_body=None)

    gh_api_calls = [c for c in subprocess_calls if "repos" in str(c)]
    assert gh_api_calls == [], (
        f"Expected 0 gh api calls (mirror should provide body), got: {gh_api_calls}"
    )


# ── AC6: estimate_issue.fetch_issue uses mirror ───────────────────────────────

def test_ac6_fetch_issue_uses_mirror_no_subprocess():
    """AC6: fetch_issue returns data from mirror without calling subprocess."""
    body_text = "Estimate body from mirror"
    issue = _make_mirror_issue(700, body=body_text)
    runner_calls = []

    def counting_runner(args, **kwargs):
        runner_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 700, "title": "Issue 700", "body": body_text, "labels": []})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=issue):
        result = estimate_issue.fetch_issue(700, "owner/repo", runner=counting_runner)

    assert result["body"] == body_text
    assert runner_calls == [], f"Expected 0 live calls when mirror hit, got {len(runner_calls)}"


def test_ac6_fetch_issue_falls_back_to_live_on_mirror_miss():
    """AC6: fetch_issue falls back to live gh api when mirror has no record."""
    body_text = "Live estimate body"
    runner_calls = []

    def counting_runner(args, **kwargs):
        runner_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "number": 701, "title": "Issue 701", "body": body_text, "labels": []
        })
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=None):
        result = estimate_issue.fetch_issue(701, "owner/repo", runner=counting_runner)

    assert len(runner_calls) == 1, f"Expected 1 live call on mirror miss, got {len(runner_calls)}"
    assert result["body"] == body_text


def test_ac6_fetch_issue_mirror_hit_returns_correct_fields():
    """AC6: mirror-served result includes number, title, body, labels."""
    issue = _make_mirror_issue(702, body="Body text")

    with patch("github_client._mirror_issue", return_value=issue):
        result = estimate_issue.fetch_issue(702, "owner/repo", runner=None)

    assert result["number"] == 702
    assert result["title"] == "Issue #702"
    assert result["body"] == "Body text"
    assert isinstance(result["labels"], list)


# ── AC11: estimate_issue write paths not modified ─────────────────────────────

def test_ac11_post_comment_still_uses_gh_subprocess():
    """AC11: post_comment (write path) uses gh issue comment — not modified."""
    src = Path(_REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py").read_text()
    assert "gh" in src and "issue" in src and "comment" in src, (
        "gh issue comment call must still be present in estimate_issue.py"
    )


def test_ac11_apply_label_still_uses_gh_subprocess():
    """AC11: apply_label (write path) uses gh issue edit — not modified."""
    src = Path(_REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py").read_text()
    assert "gh" in src and "issue" in src and "edit" in src, (
        "gh issue edit call must still be present in estimate_issue.py"
    )
