"""Tests for issue #581 — GET /api/sprints/{sprint_label}/dep-order endpoint.

Verifies:
- Returns HTTP 200 with dep_hints keyed by ticket ID when overlapping files exist
- upstream list contains tickets this ticket should run after
- downstream list contains tickets this ticket should run before
- Tickets with no file overlap have no dep_hints entry
- Returns HTTP 404 when sprint label has no issues
- Returns HTTP 400 for invalid sprint label format
- Non-pending (in-progress/sit/uat) tickets excluded from DAG
- has_cycle=True + in_cycle_tickets when circular file-overlap detected
- Single ticket with no peers returns empty dep_hints
"""
import json
import sys
from contextlib import contextmanager
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


@contextmanager
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


# ── AC: upstream/downstream hints returned for overlapping pending tickets ────

def test_dep_hints_returned_for_overlapping_tickets(tmp_path):
    issues = [
        _issue(1, "Auth login", ["sprint-99", "size-S"]),
        _issue(2, "Auth middleware", ["sprint-99", "size-M"]),
    ]
    with _make_client(tmp_path, issues) as (client, root):
        estimates_dir = root / "repo" / ".commander" / "estimates"
        _write_estimate(estimates_dir, 1, ["src/auth.py"])
        _write_estimate(estimates_dir, 2, ["src/auth.py"])

        resp = client.get("/api/sprints/sprint-99/dep-order?project=test/repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_cycle"] is False
        hints = data["dep_hints"]
        # ticket 1 owns src/auth.py → ticket 2 is downstream
        assert str(1) in hints or 1 in hints
        owner_key = str(1) if str(1) in hints else 1
        assert any(e["id"] == 2 for e in hints[owner_key]["downstream"])
        # ticket 2 should run after ticket 1
        dep_key = str(2) if str(2) in hints else 2
        assert any(e["id"] == 1 for e in hints[dep_key]["upstream"])


# ── AC: tickets with no overlap not in dep_hints ─────────────────────────────

def test_independent_tickets_have_no_dep_hints(tmp_path):
    issues = [
        _issue(1, "Login", ["sprint-99"]),
        _issue(2, "Signup", ["sprint-99"]),
    ]
    with _make_client(tmp_path, issues) as (client, root):
        estimates_dir = root / "repo" / ".commander" / "estimates"
        _write_estimate(estimates_dir, 1, ["src/login.py"])
        _write_estimate(estimates_dir, 2, ["src/signup.py"])

        resp = client.get("/api/sprints/sprint-99/dep-order?project=test/repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_cycle"] is False
        assert data["dep_hints"] == {}


# ── AC: 404 when no issues match the sprint label ────────────────────────────

def test_404_when_sprint_not_found(tmp_path):
    with _make_client(tmp_path, []) as (client, _):
        resp = client.get("/api/sprints/sprint-99/dep-order?project=test/repo")
        assert resp.status_code == 404


# ── AC: 400 for invalid sprint label ─────────────────────────────────────────

def test_400_for_invalid_sprint_label(tmp_path):
    with _make_client(tmp_path, []) as (client, _):
        resp = client.get("/api/sprints/not-a-sprint/dep-order?project=test/repo")
        assert resp.status_code == 400


# ── AC: non-pending tickets excluded ─────────────────────────────────────────

def test_in_progress_ticket_excluded_from_dep_order(tmp_path):
    issues = [
        _issue(1, "Feature A", ["sprint-99", "in-progress"]),
        _issue(2, "Feature B", ["sprint-99"]),
    ]
    with _make_client(tmp_path, issues) as (client, root):
        estimates_dir = root / "repo" / ".commander" / "estimates"
        _write_estimate(estimates_dir, 1, ["src/shared.py"])
        _write_estimate(estimates_dir, 2, ["src/shared.py"])

        resp = client.get("/api/sprints/sprint-99/dep-order?project=test/repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_cycle"] is False
        # Only one pending ticket — no edges possible
        assert data["dep_hints"] == {}


# ── AC: cycle detected → has_cycle=True + in_cycle_tickets ───────────────────

def test_cycle_returns_has_cycle_true(tmp_path):
    # Simulate cycle: ticket 1 owns file-A and file-B, ticket 2 also owns file-A
    # and ticket 3 owns file-B — not a real cycle with build_dag's star-topology
    # (build_dag owner model never creates cycles unless input order varies).
    # Patch _build_dag to return a CycleError directly.
    issues = [
        _issue(1, "Alpha", ["sprint-99"]),
        _issue(2, "Beta", ["sprint-99"]),
    ]
    with _make_client(tmp_path, issues) as (client, root):
        estimates_dir = root / "repo" / ".commander" / "estimates"
        _write_estimate(estimates_dir, 1, ["src/a.py"])
        _write_estimate(estimates_dir, 2, ["src/a.py"])

        import server as srv
        from dag_builder import CycleError

        with patch("server._build_dag", return_value=CycleError(cycles=[["1", "2"]])):
            resp = client.get("/api/sprints/sprint-99/dep-order?project=test/repo")

        assert resp.status_code == 200
        data = resp.json()
        assert data["has_cycle"] is True
        assert set(data["in_cycle_tickets"]) == {1, 2}
        assert data["dep_hints"] == {}


# ── AC: three-way chain → correct upstream/downstream ────────────────────────

def test_three_ticket_chain_upstream_downstream(tmp_path):
    # 1 → 2 → 3 chain: ticket 1 owns file-A, ticket 2 touches file-A and file-B,
    # ticket 3 touches file-B
    issues = [
        _issue(1, "Step 1", ["sprint-99"]),
        _issue(2, "Step 2", ["sprint-99"]),
        _issue(3, "Step 3", ["sprint-99"]),
    ]
    with _make_client(tmp_path, issues) as (client, root):
        estimates_dir = root / "repo" / ".commander" / "estimates"
        _write_estimate(estimates_dir, 1, ["src/a.py"])
        _write_estimate(estimates_dir, 2, ["src/a.py", "src/b.py"])
        _write_estimate(estimates_dir, 3, ["src/b.py"])

        resp = client.get("/api/sprints/sprint-99/dep-order?project=test/repo")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_cycle"] is False
        hints = data["dep_hints"]

        def _hint(n):
            return hints.get(str(n)) or hints.get(n) or {}

        # ticket 2 should run after 1
        assert any(e["id"] == 1 for e in _hint(2).get("upstream", []))
        # ticket 3 should run after 2
        assert any(e["id"] == 2 for e in _hint(3).get("upstream", []))
        # ticket 1 has no upstream
        assert _hint(1).get("upstream", []) == []
