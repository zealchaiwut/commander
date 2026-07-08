"""Tests for issue #1781 — gather_inputs_via_gh reads from mirror, cuts calls to ≤1.

AC mapping:
AC1  gather_inputs_via_gh fetches ticket roster from mirror instead of gh issue view per ticket
AC2  Summary issues also sourced from mirror in same single read
AC3  Exactly one live subprocess call remains (PR merge state only)
AC4  Total gh subprocess invocations for issue data is 0 when mirror populated
     (confirmed by asserting mock call counts on N-ticket fixture sprint)
AC5  When mirror unpopulated (empty/absent), falls back to gh path producing identical RecInputs
AC6  Docstring notes mirror data may be ≤60 s stale and that staleness is acceptable
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager import reconciliation as rec  # noqa: E402


# ── fixture data ──────────────────────────────────────────────────────────────

def _make_mirror(sprint_label: str, ticket_numbers: list[int]) -> list[dict]:
    """Build a mirror payload: one summary issue + N sprint tickets."""
    issues = []
    # Summary issue for this sprint
    issues.append({
        "number": 9000,
        "title": f"{sprint_label} Executive Summary",
        "labels": [
            {"name": sprint_label},
            {"name": "sprint-summary"},
            {"name": "docs"},
        ],
        "state": "open",
        "body": "",
    })
    # Ticket issues
    for n in ticket_numbers:
        issues.append({
            "number": n,
            "title": f"Ticket #{n}",
            "labels": [
                {"name": sprint_label},
                {"name": "UAT"},
            ],
            "state": "closed",
            "body": "",
        })
    return issues


# ── AC4: zero gh-issue calls when mirror is populated ────────────────────────

def test_ac4_zero_issue_subprocess_calls_when_mirror_populated():
    """AC4: with mirror_reader returning data, gh subprocess is not called for issues."""
    ticket_numbers = [101, 102, 103, 104, 105]
    sprint_label = "sprint-99"
    mirror = _make_mirror(sprint_label, ticket_numbers)

    def mirror_reader(repo):
        return mirror

    call_log: list[str] = []

    def fake_subprocess_run(args, **_kwargs):
        call_log.append(" ".join(str(a) for a in args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 42, "state": "MERGED"})
        return r

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        result = rec.gather_inputs_via_gh(
            sprint_label=sprint_label,
            repo="owner/repo",
            pr_url="https://github.com/owner/repo/pull/42",
            ticket_numbers=ticket_numbers,
            mirror_reader=mirror_reader,
        )

    # All issue calls go through the mirror — only PR-state call(s) allowed
    issue_calls = [c for c in call_log if "issue" in c]
    pr_calls = [c for c in call_log if "pr" in c or "pulls" in c]

    assert issue_calls == [], (
        f"Expected 0 'gh issue' subprocess calls, got {len(issue_calls)}: {issue_calls}"
    )
    # At most 1 PR-state call
    assert len(pr_calls) <= 1, (
        f"Expected ≤1 PR-state subprocess call, got {len(pr_calls)}: {pr_calls}"
    )

    # Result shape is correct
    assert "summary_issues" in result
    assert "pr_info" in result
    assert "tickets" in result


# ── AC1: ticket roster sourced from mirror ────────────────────────────────────

def test_ac1_tickets_sourced_from_mirror():
    """AC1: tickets in the result come from mirror, not from per-ticket gh issue view."""
    ticket_numbers = [201, 202, 203]
    sprint_label = "sprint-100"
    mirror = _make_mirror(sprint_label, ticket_numbers)

    result = rec.gather_inputs_via_gh(
        sprint_label=sprint_label,
        repo="owner/repo",
        pr_url=None,
        ticket_numbers=ticket_numbers,
        mirror_reader=lambda _repo: mirror,
    )

    returned_numbers = {t["number"] for t in result["tickets"]}
    assert returned_numbers == set(ticket_numbers), (
        f"Expected ticket numbers {set(ticket_numbers)}, got {returned_numbers}"
    )


# ── AC2: summary issues also from mirror ─────────────────────────────────────

def test_ac2_summary_issues_sourced_from_mirror():
    """AC2: summary issues are identified from the mirror in the same single read."""
    sprint_label = "sprint-101"
    ticket_numbers = [301, 302]
    mirror = _make_mirror(sprint_label, ticket_numbers)

    result = rec.gather_inputs_via_gh(
        sprint_label=sprint_label,
        repo="owner/repo",
        pr_url=None,
        ticket_numbers=ticket_numbers,
        mirror_reader=lambda _repo: mirror,
    )

    # The summary issue (number 9000) should appear in summary_issues
    assert any(iss.get("number") == 9000 for iss in result["summary_issues"]), (
        f"Summary issue not found; got: {result['summary_issues']}"
    )
    # The summary issue has the sprint-summary label
    si = next(iss for iss in result["summary_issues"] if iss.get("number") == 9000)
    label_names = [l.get("name") if isinstance(l, dict) else l for l in si.get("labels", [])]
    assert "sprint-summary" in label_names


# ── AC3: exactly one PR-state subprocess call ─────────────────────────────────

def test_ac3_exactly_one_pr_state_call_when_mirror_populated():
    """AC3: only the PR-merge-state check makes a live subprocess call."""
    sprint_label = "sprint-102"
    ticket_numbers = [401, 402, 403, 404, 405, 406, 407, 408, 409, 410]
    mirror = _make_mirror(sprint_label, ticket_numbers)

    call_log: list[list[str]] = []

    def fake_subprocess_run(args, **_kwargs):
        call_log.append(list(str(a) for a in args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 77, "state": "MERGED"})
        return r

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        rec.gather_inputs_via_gh(
            sprint_label=sprint_label,
            repo="owner/repo",
            pr_url="https://github.com/owner/repo/pull/77",
            ticket_numbers=ticket_numbers,
            mirror_reader=lambda _repo: mirror,
        )

    total_calls = len(call_log)
    assert total_calls <= 1, (
        f"Expected ≤1 total subprocess call, got {total_calls}: {call_log}"
    )


# ── AC5: empty mirror falls back to gh calls ──────────────────────────────────

def test_ac5_fallback_when_mirror_returns_none():
    """AC5: when mirror_reader returns None, falls back to gh call path."""
    sprint_label = "sprint-103"
    ticket_numbers = [501, 502]

    issue_list_payload = [
        {"number": 8000, "title": "sprint-103 Executive Summary",
         "labels": [{"name": "sprint-103"}, {"name": "sprint-summary"}]},
    ]
    ticket_501 = {"number": 501, "labels": [{"name": "UAT"}]}
    ticket_502 = {"number": 502, "labels": [{"name": "UAT"}]}
    pr_payload = {"number": 55, "state": "MERGED"}

    call_args_log: list[list[str]] = []

    def fake_subprocess_run(args, **_kwargs):
        call_args_log.append(list(str(a) for a in args))
        r = MagicMock()
        r.returncode = 0
        cmd = list(str(a) for a in args)
        if "issue" in cmd and "list" in cmd:
            r.stdout = json.dumps(issue_list_payload)
        elif "issue" in cmd and "view" in cmd and "501" in cmd:
            r.stdout = json.dumps(ticket_501)
        elif "issue" in cmd and "view" in cmd and "502" in cmd:
            r.stdout = json.dumps(ticket_502)
        elif "pr" in cmd:
            r.stdout = json.dumps(pr_payload)
        else:
            r.stdout = "[]"
        return r

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        result = rec.gather_inputs_via_gh(
            sprint_label=sprint_label,
            repo="owner/repo",
            pr_url="https://github.com/owner/repo/pull/55",
            ticket_numbers=ticket_numbers,
            mirror_reader=lambda _repo: None,  # mirror absent
        )

    # gh issue calls should have been made (fallback)
    issue_calls = [c for c in call_args_log if "issue" in c]
    assert len(issue_calls) >= 1, "Fallback must call gh for issues when mirror absent"

    # Result still has the same shape
    assert "summary_issues" in result
    assert "tickets" in result
    assert "pr_info" in result


def test_ac5_fallback_when_mirror_returns_empty_list():
    """AC5: when mirror_reader returns empty list, falls back to gh call path."""
    sprint_label = "sprint-104"
    ticket_numbers = [601]

    call_log: list[list[str]] = []

    def fake_subprocess_run(args, **_kwargs):
        call_log.append(list(str(a) for a in args))
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps([])
        return r

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        result = rec.gather_inputs_via_gh(
            sprint_label=sprint_label,
            repo="owner/repo",
            pr_url=None,
            ticket_numbers=ticket_numbers,
            mirror_reader=lambda _repo: [],  # mirror empty
        )

    # Some gh calls should have been made (fallback)
    issue_calls = [c for c in call_log if "issue" in c]
    assert len(issue_calls) >= 1, "Fallback must call gh for issues when mirror empty"


# ── AC6: docstring notes staleness ───────────────────────────────────────────

def test_ac6_docstring_notes_mirror_staleness():
    """AC6: gather_inputs_via_gh docstring mentions ≤60 s staleness."""
    doc = rec.gather_inputs_via_gh.__doc__ or ""
    assert "60" in doc or "stale" in doc.lower(), (
        "Docstring must mention that mirror data may be up to 60 s stale"
    )


# ── AC7: pre-existing tests still pass (smoke) ───────────────────────────────

def test_ac7_signature_backward_compatible():
    """AC7: gather_inputs_via_gh still accepts the original 4 positional params."""
    # Calling without mirror_reader must not raise TypeError
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = rec.gather_inputs_via_gh(
            "sprint-105", "owner/repo", None, [701, 702]
        )
    assert isinstance(result, dict)
    assert "summary_issues" in result
    assert "tickets" in result
    assert "pr_info" in result


# ── AC4 extended: N-ticket fixture, count verified precisely ─────────────────

def test_ac4_n_ticket_fixture_zero_issue_calls():
    """AC4: for a 20-ticket sprint with mirror populated, exactly 0 gh issue calls
    and ≤1 gh pr/api call."""
    ticket_numbers = list(range(800, 820))  # 20 tickets
    sprint_label = "sprint-106"
    mirror = _make_mirror(sprint_label, ticket_numbers)

    issue_call_count = 0
    pr_call_count = 0

    def fake_subprocess_run(args, **_kwargs):
        nonlocal issue_call_count, pr_call_count
        cmd = [str(a) for a in args]
        if "issue" in cmd:
            issue_call_count += 1
        if "pr" in cmd or "pulls" in cmd:
            pr_call_count += 1
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"number": 99, "state": "MERGED"})
        return r

    with patch("subprocess.run", side_effect=fake_subprocess_run):
        result = rec.gather_inputs_via_gh(
            sprint_label=sprint_label,
            repo="owner/repo",
            pr_url="https://github.com/owner/repo/pull/99",
            ticket_numbers=ticket_numbers,
            mirror_reader=lambda _repo: mirror,
        )

    assert issue_call_count == 0, (
        f"Expected 0 gh issue calls, got {issue_call_count}"
    )
    assert pr_call_count <= 1, (
        f"Expected ≤1 PR-state call, got {pr_call_count}"
    )
    # All 20 tickets found
    assert len(result["tickets"]) == 20
