"""Tests for issue #579 — GET /api/sprints/{sprint_label}/conflicts endpoint.

Verifies:
- Returns HTTP 200 with conflicts array + pending_count
- Lists conflicting ticket pairs with both IDs, titles, and shared file paths
- Returns HTTP 200 with empty conflicts array when no overlaps
- Returns HTTP 404 when no issues exist for the sprint label
- Returns HTTP 400 for invalid sprint label format
- Conflict detection ignores non-pending (in-progress/sit/uat/done) tickets
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


def _issue(number: int, title: str, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "url": f"https://github.com/test/repo/issues/{number}",
        "body": "",
        "labels": [{"name": l} for l in labels],
    }


def _write_estimate(estimates_dir: Path, issue_num: int, files: list[str]) -> None:
    estimates_dir.mkdir(parents=True, exist_ok=True)
    est = {
        "issue_number": issue_num,
        "size": "S",
        "confidence": "high",
        "files_likely_affected": files,
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
    }
    (estimates_dir / f"issue-{issue_num}.json").write_text(
        json.dumps(est), encoding="utf-8"
    )


def _make_client(tmp_path: Path, issues: list[dict]):
    if "server" in sys.modules:
        del sys.modules["server"]

    import server as srv

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    with (
        patch.object(srv.github_client, "list_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        yield client, tmp_path


# ── AC: two pending tickets sharing a file → conflict listed ─────────────────

def test_conflict_detected_for_two_overlapping_pending_tickets(tmp_path):
    issues = [
        _issue(1, "Auth login", ["sprint-99", "size-S"]),
        _issue(2, "Auth signup", ["sprint-99", "size-M"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    estimates_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate(estimates_dir, 1, ["src/auth/login.ts"])
    _write_estimate(estimates_dir, 2, ["src/auth/login.ts"])

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending_count"] == 2
    assert len(data["conflicts"]) == 1
    c = data["conflicts"][0]
    assert {c["ticket1_id"], c["ticket2_id"]} == {1, 2}
    assert c["shared_files"] == ["src/auth/login.ts"]
    assert c["ticket1_title"] in {"Auth login", "Auth signup"}
    assert c["ticket2_title"] in {"Auth login", "Auth signup"}


# ── AC: no overlaps → 200 with empty conflicts array ─────────────────────────

def test_no_conflicts_when_files_are_disjoint(tmp_path):
    issues = [
        _issue(10, "Feature A", ["sprint-99"]),
        _issue(11, "Feature B", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    estimates_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate(estimates_dir, 10, ["src/a.py"])
    _write_estimate(estimates_dir, 11, ["src/b.py"])

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflicts"] == []
    assert data["pending_count"] == 2


# ── AC: sprint does not exist → 404 ──────────────────────────────────────────

def test_404_when_sprint_label_has_no_issues(tmp_path):
    gen = _make_client(tmp_path, [])
    client, _ = next(gen)

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 404


# ── AC: invalid sprint label → 400 ───────────────────────────────────────────

def test_400_for_invalid_sprint_label(tmp_path):
    gen = _make_client(tmp_path, [])
    client, _ = next(gen)

    resp = client.get("/api/sprints/not-a-sprint/conflicts?project=test/repo")
    assert resp.status_code == 400


# ── AC: non-pending tickets excluded from conflict detection ──────────────────

def test_in_progress_ticket_not_included_in_conflict_detection(tmp_path):
    issues = [
        _issue(20, "Pending ticket", ["sprint-99"]),
        _issue(21, "In-progress ticket", ["sprint-99", "in-progress"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    estimates_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate(estimates_dir, 20, ["src/shared.ts"])
    _write_estimate(estimates_dir, 21, ["src/shared.ts"])

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflicts"] == []
    assert data["pending_count"] == 1


def test_sit_ticket_not_included_in_conflict_detection(tmp_path):
    issues = [
        _issue(30, "Pending A", ["sprint-99"]),
        _issue(31, "SIT ticket", ["sprint-99", "SIT"]),
        _issue(32, "Pending B", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    estimates_dir = root / "repo" / ".commander" / "estimates"
    _write_estimate(estimates_dir, 30, ["src/shared.ts"])
    _write_estimate(estimates_dir, 31, ["src/shared.ts"])
    _write_estimate(estimates_dir, 32, ["src/shared.ts"])

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    # Only 30 and 32 are pending — they share the same file
    assert data["pending_count"] == 2
    assert len(data["conflicts"]) == 1
    assert {data["conflicts"][0]["ticket1_id"], data["conflicts"][0]["ticket2_id"]} == {30, 32}


# ── AC: three-way overlap produces two conflict pairs ────────────────────────

def test_three_tickets_with_partial_overlap(tmp_path):
    issues = [
        _issue(40, "Ticket A", ["sprint-99"]),
        _issue(41, "Ticket B", ["sprint-99"]),
        _issue(42, "Ticket C", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)

    estimates_dir = root / "repo" / ".commander" / "estimates"
    # A and B share file1; A and C share file2; B and C share no files
    _write_estimate(estimates_dir, 40, ["src/file1.ts", "src/file2.ts"])
    _write_estimate(estimates_dir, 41, ["src/file1.ts"])
    _write_estimate(estimates_dir, 42, ["src/file2.ts"])

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending_count"] == 3
    assert len(data["conflicts"]) == 2
    pairs = [{c["ticket1_id"], c["ticket2_id"]} for c in data["conflicts"]]
    assert {40, 41} in pairs
    assert {40, 42} in pairs


# ── AC: tickets without estimates produce no conflict ────────────────────────

def test_tickets_without_estimates_never_conflict(tmp_path):
    issues = [
        _issue(50, "Unestimated A", ["sprint-99"]),
        _issue(51, "Unestimated B", ["sprint-99"]),
    ]
    gen = _make_client(tmp_path, issues)
    client, root = next(gen)
    # No estimate files written — files_likely_affected defaults to []

    resp = client.get("/api/sprints/sprint-99/conflicts?project=test/repo")
    assert resp.status_code == 200
    data = resp.json()
    assert data["conflicts"] == []
    assert data["pending_count"] == 2
