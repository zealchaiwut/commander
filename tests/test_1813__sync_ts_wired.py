"""Tests for issue #1813 — wire stale-hit sync_ts guard into mirror body call sites.

AC mapping:
AC1  _fetch_dispatch_issue_body accepts sync_ts parameter (signature check)
AC2  _fetch_dispatch_issue_body + stale record + sync_ts triggers live fetch (guard wired)
AC3  _fetch_dispatch_issue_body + fresh record + sync_ts uses mirror (no live fetch)
AC4  _build_design_block with issue_body=None routes through _mirror_issue_body (not direct _mirror_issue)
AC5  fetch_issue accepts sync_ts parameter (signature check)
AC6  fetch_issue + stale record + sync_ts triggers live fetch (guard wired)
AC7  fetch_issue + fresh record + sync_ts uses mirror (no live fetch)
AC8  Existing callers without sync_ts are unaffected (backward-compatible defaults)
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
    def runner(args, **kwargs):
        r = MagicMock()
        r.returncode = 0
        num = issue_num or int(args[-1].split("/")[-1])
        r.stdout = json.dumps({"number": num, "title": f"Issue #{num}", "body": response_body, "labels": []})
        r.stderr = ""
        return r
    return runner


# ── AC1: _fetch_dispatch_issue_body accepts sync_ts ──────────────────────────

def test_ac1_fetch_dispatch_accepts_sync_ts_in_signature():
    """AC1: _fetch_dispatch_issue_body signature includes sync_ts parameter."""
    sig = inspect.signature(dispatch._fetch_dispatch_issue_body)
    params = set(sig.parameters)
    assert "sync_ts" in params, (
        f"_fetch_dispatch_issue_body must accept sync_ts; got params: {params}"
    )


# ── AC2: stale record + sync_ts triggers live fetch ──────────────────────────

def test_ac2_fetch_dispatch_stale_record_sync_ts_triggers_live_fetch():
    """AC2: _fetch_dispatch_issue_body with stale mirror record and sync_ts triggers live fetch.

    Stale = issue modified AFTER the mirror last synced (updatedAt > sync_ts). #1915.
    """
    stale_issue = _make_mirror_issue(101, body="Old body", updated_at="2026-07-09T13:00:00Z")
    live_calls = []

    def direct_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 101, "body": "Fresh live body"})
        r.stderr = ""
        return r

    forwarded_sync_ts = []
    original_mib = dispatch._mirror_issue_body

    def capturing_mib(repo, issue_num, runner=None, sync_ts=None):
        forwarded_sync_ts.append(sync_ts)
        return original_mib(repo, issue_num, runner=direct_runner, sync_ts=sync_ts)

    with patch("github_client._mirror_issue", return_value=stale_issue):
        with patch.object(dispatch, "_mirror_issue_body", side_effect=capturing_mib):
            dispatch._fetch_dispatch_issue_body("owner/repo", 101, sync_ts="2026-07-09T12:00:00Z")

    assert forwarded_sync_ts == ["2026-07-09T12:00:00Z"], (
        f"_fetch_dispatch_issue_body must forward sync_ts to _mirror_issue_body; "
        f"got: {forwarded_sync_ts}"
    )
    assert len(live_calls) == 1, (
        f"Stale record + sync_ts should trigger 1 live fetch, got {len(live_calls)}"
    )


# ── AC3: fresh record + sync_ts uses mirror ───────────────────────────────────

def test_ac3_fetch_dispatch_fresh_record_sync_ts_uses_mirror():
    """AC3: _fetch_dispatch_issue_body with fresh mirror record and sync_ts uses mirror, no live fetch.

    Fresh = issue modified BEFORE the mirror last synced (updatedAt <= sync_ts). #1915.
    """
    fresh_issue = _make_mirror_issue(102, body="Fresh mirror body", updated_at="2026-07-09T09:00:00Z")
    live_calls = []

    def direct_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 102, "body": "Should not be used"})
        r.stderr = ""
        return r

    original_mib = dispatch._mirror_issue_body

    def capturing_mib(repo, issue_num, runner=None, sync_ts=None):
        return original_mib(repo, issue_num, runner=direct_runner, sync_ts=sync_ts)

    with patch("github_client._mirror_issue", return_value=fresh_issue):
        with patch.object(dispatch, "_mirror_issue_body", side_effect=capturing_mib):
            result = dispatch._fetch_dispatch_issue_body(
                "owner/repo", 102, sync_ts="2026-07-09T10:00:00Z"
            )

    assert result == "Fresh mirror body", f"Expected mirror body, got: {result!r}"
    assert live_calls == [], f"Expected 0 live fetches for fresh record, got {len(live_calls)}"


# ── AC4: _build_design_block routes through _mirror_issue_body ───────────────

def test_ac4_build_design_block_uses_mirror_issue_body_not_direct_mirror(tmp_path):
    """AC4: _build_design_block with issue_body=None uses _mirror_issue_body, not github_client._mirror_issue directly."""
    from services.sprint_manager.sprint_manager import _build_design_block

    (tmp_path / "DESIGN.md").write_text(
        "# Design\n## Architecture Overview\nContent here.\n", encoding="utf-8"
    )

    body_text = "## What & Why\nSome body without refs."

    # sprint_manager imports _mirror_issue_body via `from dispatch import _mirror_issue_body`.
    # Patch the name as bound in sprint_manager's namespace.
    mib_calls = []

    def capturing_mib(repo, issue_num, runner=None, sync_ts=None):
        mib_calls.append((repo, issue_num))
        return body_text

    with patch("services.sprint_manager.sprint_manager._mirror_issue_body", side_effect=capturing_mib):
        with patch("github_client._mirror_issue") as direct_mirror_mock:
            _build_design_block(999, "owner/repo", tmp_path, issue_body=None)

    assert len(mib_calls) == 1, (
        f"_build_design_block must call _mirror_issue_body once when issue_body=None; "
        f"got {len(mib_calls)} calls"
    )
    direct_mirror_mock.assert_not_called()


def test_ac4_build_design_block_no_subprocess_on_mirror_hit_via_helper(tmp_path):
    """AC4: when _mirror_issue_body returns body, no subprocess.run call is made."""
    from services.sprint_manager.sprint_manager import _build_design_block

    (tmp_path / "DESIGN.md").write_text("# Design\n## Overview\nText.\n", encoding="utf-8")
    body_text = "## What & Why\nBody text."
    subprocess_calls = []

    with patch("services.sprint_manager.sprint_manager._mirror_issue_body", return_value=body_text):
        with patch("subprocess.run", side_effect=lambda *a, **k: subprocess_calls.append(a)):
            _build_design_block(998, "owner/repo", tmp_path, issue_body=None)

    gh_calls = [c for c in subprocess_calls if "gh" in str(c)]
    assert gh_calls == [], f"Expected 0 gh subprocess calls after routing through helper, got: {gh_calls}"


# ── AC5: fetch_issue accepts sync_ts ─────────────────────────────────────────

def test_ac5_fetch_issue_accepts_sync_ts_in_signature():
    """AC5: fetch_issue signature includes sync_ts parameter."""
    sig = inspect.signature(estimate_issue.fetch_issue)
    params = set(sig.parameters)
    assert "sync_ts" in params, (
        f"fetch_issue must accept sync_ts; got params: {params}"
    )


# ── AC6: fetch_issue + stale record + sync_ts triggers live fetch ─────────────

def test_ac6_fetch_issue_stale_record_sync_ts_triggers_live_fetch():
    """AC6: fetch_issue with stale mirror record and sync_ts triggers live fetch.

    Stale = issue modified AFTER mirror last synced (updatedAt > sync_ts). #1915.
    """
    stale_issue = _make_mirror_issue(201, body="Old body", updated_at="2026-07-09T13:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({
            "number": 201, "title": "Issue #201", "body": "Fresh live body", "labels": []
        })
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=stale_issue):
        result = estimate_issue.fetch_issue(
            201, "owner/repo", runner=counting_runner, sync_ts="2026-07-09T12:00:00Z"
        )

    assert len(live_calls) == 1, (
        f"Stale record + sync_ts must trigger 1 live fetch, got {len(live_calls)}"
    )
    assert result["body"] == "Fresh live body"


# ── AC7: fetch_issue + fresh record + sync_ts uses mirror ────────────────────

def test_ac7_fetch_issue_fresh_record_sync_ts_uses_mirror():
    """AC7: fetch_issue with fresh mirror record and sync_ts uses mirror, no live fetch.

    Fresh = issue modified BEFORE mirror last synced (updatedAt <= sync_ts). #1915.
    """
    fresh_issue = _make_mirror_issue(202, body="Fresh mirror body", updated_at="2026-07-09T09:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 202, "body": "Should not be used", "labels": []})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=fresh_issue):
        result = estimate_issue.fetch_issue(
            202, "owner/repo", runner=counting_runner, sync_ts="2026-07-09T10:00:00Z"
        )

    assert live_calls == [], f"Expected 0 live fetches for fresh record, got {len(live_calls)}"
    assert result["body"] == "Fresh mirror body"


# ── AC8: backward-compatible defaults ────────────────────────────────────────

def test_ac8_fetch_dispatch_without_sync_ts_uses_mirror_as_before():
    """AC8: _fetch_dispatch_issue_body without sync_ts behaves as before (uses mirror, no stale check)."""
    old_issue = _make_mirror_issue(301, body="Old body", updated_at="2026-01-01T00:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 301, "body": "Live body"})
        r.stderr = ""
        return r

    original_mib = dispatch._mirror_issue_body

    def routing_mib(repo, issue_num, runner=None, sync_ts=None):
        return original_mib(repo, issue_num, runner=counting_runner, sync_ts=sync_ts)

    with patch("github_client._mirror_issue", return_value=old_issue):
        with patch.object(dispatch, "_mirror_issue_body", side_effect=routing_mib):
            result = dispatch._fetch_dispatch_issue_body("owner/repo", 301)

    # Without sync_ts, old records are NOT considered stale → mirror used directly.
    assert live_calls == [], f"Without sync_ts, mirror should be used without stale check; got {len(live_calls)} live calls"
    assert result == "Old body"


def test_ac8_fetch_issue_without_sync_ts_uses_mirror_as_before():
    """AC8: fetch_issue without sync_ts behaves as before (uses mirror, no stale check)."""
    old_issue = _make_mirror_issue(302, body="Old mirror body", updated_at="2026-01-01T00:00:00Z")
    live_calls = []

    def counting_runner(args, **kwargs):
        live_calls.append(list(args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 302, "title": "Issue #302", "body": "Live body", "labels": []})
        r.stderr = ""
        return r

    with patch("github_client._mirror_issue", return_value=old_issue):
        result = estimate_issue.fetch_issue(302, "owner/repo", runner=counting_runner)

    assert live_calls == [], (
        f"Without sync_ts, fetch_issue should use mirror without stale check; got {len(live_calls)} live calls"
    )
    assert result["body"] == "Old mirror body"
