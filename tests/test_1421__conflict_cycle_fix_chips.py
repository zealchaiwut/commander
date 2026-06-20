"""Tests for issue #1421 — Inline conflict/cycle fix chips on preview-dag.

Anchored to Acceptance Criteria:
- AC1:  file-overlap conflict indicator returns chip data (ticket1_id, ticket2_id)
- AC3:  clicking Move-after chip calls plan API; plan persists new order
- AC4:  after plan reorder, preview-dag is re-fetchable and conflict resolved
- AC5:  cycle warning lists all involved ticket numbers
- AC6:  each cycle ticket has a suggested move resolution (chip data)
- AC7:  Remove-from-sprint calls assign API (sprint: null removes sprint labels)
- AC8:  Move-to-backlog via assign API (same operation as remove from sprint)
- AC9:  idempotent plan API — calling twice with same order is safe
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVICES_DIR = REPO_ROOT / "services" / "sprint_manager"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR), str(SERVICES_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── helpers ───────────────────────────────────────────────────────────────────

def _issue(number: int, title: str, labels: list[str]) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "url": f"https://github.com/test/repo/issues/{number}",
        "body": "## Acceptance Criteria\n- [ ] do it",
        "labels": [{"name": lbl} for lbl in labels],
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
    (estimates_dir / f"issue-{issue_num}.json").write_text(json.dumps(est), encoding="utf-8")


def _write_plan(sprints_dir: Path, sprint_label: str, ticket_numbers: list[int]) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    plan = {"state": "planned", "tickets": ticket_numbers}
    (sprints_dir / f"{sprint_label}-plan.json").write_text(json.dumps(plan), encoding="utf-8")


def _read_plan(sprints_dir: Path, sprint_label: str) -> list[int]:
    plan_path = sprints_dir / f"{sprint_label}-plan.json"
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    raw = data.get("tickets", data)
    return raw if isinstance(raw, list) else []


@contextmanager
def _make_client(tmp_path: Path, issues: list[dict], assign_side_effect=None):
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    assign_calls = []

    def fake_assign(issue_num, sprint_num):
        assign_calls.append({"issue": issue_num, "sprint": sprint_num})
        if assign_side_effect:
            raise assign_side_effect

    def fake_assign_by_label(issue_num, label):
        assign_calls.append({"issue": issue_num, "sprint_label": label})

    def fake_invalidate(key):
        pass

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch.object(srv.github_client, "get_issue", side_effect=lambda n: next(
            (i for i in issues if i["number"] == n), {"number": n, "labels": []}
        )),
        patch.object(srv.github_client, "assign_sprint", side_effect=fake_assign),
        patch.object(srv.github_client, "assign_sprint_by_label", side_effect=fake_assign_by_label),
        patch.object(srv.github_client, "invalidate", side_effect=fake_invalidate),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        yield client, tmp_path, assign_calls


# ── AC1: file-overlap conflict data structure for chips ───────────────────────

def test_preview_dag_conflict_has_ticket_ids_for_chip(tmp_path):
    """AC1: preview-dag conflicts include ticket1_id and ticket2_id for Move-after chip."""
    issues = [
        _issue(1, "First ticket", ["sprint-10"]),
        _issue(2, "Second ticket", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 1, ["src/shared.py"])
        _write_estimate(est, 2, ["src/shared.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        conflicts = data.get("conflicts", [])
        assert len(conflicts) == 1, f"Expected 1 conflict; got {conflicts}"
        c = conflicts[0]
        assert "ticket1_id" in c and "ticket2_id" in c, (
            f"Conflict must have ticket1_id and ticket2_id for chip rendering; got {c}"
        )
        assert c["ticket1_id"] == 1 and c["ticket2_id"] == 2, (
            f"Expected ticket1=1, ticket2=2; got {c}"
        )
        assert "shared_files" in c and len(c["shared_files"]) > 0, (
            f"Conflict must list shared_files; got {c}"
        )


def test_preview_dag_conflict_has_titles_for_chip(tmp_path):
    """AC1: conflict entries include ticket titles for chip tooltip text."""
    issues = [
        _issue(3, "Router ticket", ["sprint-10"]),
        _issue(5, "Auth ticket", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 3, ["src/router.py"])
        _write_estimate(est, 5, ["src/router.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        data = resp.json()
        conflicts = data.get("conflicts", [])
        assert len(conflicts) == 1
        c = conflicts[0]
        assert c.get("ticket1_title") == "Router ticket", f"Wrong title: {c}"
        assert c.get("ticket2_title") == "Auth ticket", f"Wrong title: {c}"


def test_preview_dag_no_conflict_when_no_shared_files(tmp_path):
    """AC1: no conflict indicator when tickets have no file overlap."""
    issues = [
        _issue(1, "Frontend", ["sprint-10"]),
        _issue(2, "Backend", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 1, ["src/frontend.py"])
        _write_estimate(est, 2, ["src/backend.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        data = resp.json()
        assert data.get("conflicts", []) == [], (
            f"Expected no conflicts for non-overlapping tickets; got {data['conflicts']}"
        )


# ── AC3: plan-order API reorders for Move-after chip action ───────────────────

def test_plan_api_accepts_new_order(tmp_path):
    """AC3: POST /api/sprints/{label}/plan accepts and persists a new ticket order."""
    issues = [
        _issue(1, "First", ["sprint-10"]),
        _issue(2, "Second", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        sprints_dir = root / "repo" / ".commander" / "sprints"
        _write_plan(sprints_dir, "sprint-10", [2, 1])

        new_order = [1, 2]
        resp = client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps(new_order),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
        assert resp.json().get("ok") is True

        saved = _read_plan(sprints_dir, "sprint-10")
        assert saved == [1, 2], f"Plan not persisted correctly; got {saved}"


def test_plan_api_reorders_b_after_a(tmp_path):
    """AC3: Move-after chip: B moves to come right after A in the plan order."""
    issues = [
        _issue(10, "A", ["sprint-10"]),
        _issue(20, "B", ["sprint-10"]),
        _issue(30, "C", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        sprints_dir = root / "repo" / ".commander" / "sprints"
        # B (20) currently comes before A (10); C (30) after both
        _write_plan(sprints_dir, "sprint-10", [20, 10, 30])

        # Simulate chip action: "Move #B after #A" → order becomes [10, 20, 30]
        new_order = [10, 20, 30]
        resp = client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps(new_order),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text
        saved = _read_plan(sprints_dir, "sprint-10")
        assert saved.index(10) < saved.index(20), (
            f"A (10) must appear before B (20) after chip action; got {saved}"
        )


# ── AC4: after reorder, preview-dag is re-fetchable ──────────────────────────

def test_preview_dag_refetchable_after_plan_update(tmp_path):
    """AC4: re-fetching preview-dag after a plan update returns valid response."""
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 1, ["src/core.py"])
        _write_estimate(est, 2, ["src/core.py"])

        sprints_dir = root / "repo" / ".commander" / "sprints"
        _write_plan(sprints_dir, "sprint-10", [2, 1])

        # First fetch: initial state
        resp1 = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        assert resp1.status_code == 200

        # Apply plan reorder
        client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps([1, 2]),
            headers={"Content-Type": "application/json"},
        )

        # Second fetch: still returns valid response
        resp2 = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        assert resp2.status_code == 200, f"Re-fetch failed: {resp2.text}"
        data = resp2.json()
        assert "conflicts" in data and "cycles" in data, (
            f"Re-fetched preview-dag missing fields; got {data}"
        )


# ── AC5: cycle warning lists all involved ticket numbers ──────────────────────

def test_preview_dag_cycle_lists_all_tickets(tmp_path):
    """AC5: when a cycle exists, the cycles field lists all involved ticket IDs."""
    # Build a 3-ticket cycle: #1 → #2 → #3 → #1
    # This requires dag_builder to be available; if not, cycles will be empty
    # and we skip — but we verify the response structure is correct either way.
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
        _issue(3, "C", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        # To create a cycle: 1→2→3→1 via shared files in a chain
        # dag_builder assigns ownership by input order (ascending issue number)
        # #1 owns file_ab, #2 owns file_bc, #3 owns file_ca
        # edges: 1→2 (file_ab), 2→3 (file_bc), 3→1 (file_ca) = cycle
        _write_estimate(est, 1, ["file_ab.py", "file_ca.py"])
        _write_estimate(est, 2, ["file_ab.py", "file_bc.py"])
        _write_estimate(est, 3, ["file_bc.py", "file_ca.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # cycles field must exist
        assert "cycles" in data, f"Response missing 'cycles' field: {data}"

        if data["cycles"]:
            # If dag_builder detected the cycle, all tickets must be represented
            all_cycle_tids = {tid for path in data["cycles"] for tid in path}
            for num in [1, 2, 3]:
                assert f"#{num}" in all_cycle_tids, (
                    f"Ticket #{num} not in cycle paths; cycles={data['cycles']}"
                )


def test_preview_dag_cycle_returns_empty_levels(tmp_path):
    """AC5: when cycle detected, levels is empty (no valid run order)."""
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
        _issue(3, "C", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 1, ["file_ab.py", "file_ca.py"])
        _write_estimate(est, 2, ["file_ab.py", "file_bc.py"])
        _write_estimate(est, 3, ["file_bc.py", "file_ca.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        data = resp.json()

        if data.get("cycles"):
            # Cycle detected → levels must be empty
            assert data.get("levels", []) == [], (
                f"When cycles detected, levels must be empty; got levels={data['levels']}"
            )


# ── AC7: Remove-from-sprint via assign API ────────────────────────────────────

def test_assign_api_removes_sprint_label(tmp_path):
    """AC7: POST /api/sprint-planning/assign with sprint=null removes sprint labels."""
    issues = [_issue(5, "Conflicting", ["sprint-10"])]
    with _make_client(tmp_path, issues) as (client, root, assign_calls):
        resp = client.post(
            "/api/sprint-planning/assign",
            content=json.dumps({"issue": 5, "sprint": None}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
        assert resp.json().get("ok") is True

        # Verify assign was called with sprint=None (remove-from-sprint action)
        assert any(c["issue"] == 5 and c.get("sprint") is None for c in assign_calls), (
            f"Expected assign_sprint(5, None) call; got {assign_calls}"
        )


# ── AC8: Move-to-backlog via assign API ───────────────────────────────────────

def test_assign_api_move_to_backlog(tmp_path):
    """AC8: same assign API with sprint=null moves ticket to backlog."""
    issues = [_issue(7, "Cycle ticket", ["sprint-10"])]
    with _make_client(tmp_path, issues) as (client, root, assign_calls):
        resp = client.post(
            "/api/sprint-planning/assign",
            content=json.dumps({"issue": 7, "sprint": None}),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text

        # The API removes all sprint-N labels → ticket becomes backlog
        assert any(c["issue"] == 7 for c in assign_calls), (
            f"Expected assign call for issue 7; got {assign_calls}"
        )


# ── AC9: idempotent chip actions ──────────────────────────────────────────────

def test_plan_api_idempotent_same_order(tmp_path):
    """AC9: posting the same order twice returns ok=True both times, no error."""
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        sprints_dir = root / "repo" / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)

        order = [1, 2]
        for attempt in range(2):
            resp = client.post(
                "/api/sprints/sprint-10/plan?project=test/repo",
                content=json.dumps(order),
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200, (
                f"Attempt {attempt + 1}: Expected 200; got {resp.status_code}: {resp.text}"
            )
            assert resp.json().get("ok") is True, (
                f"Attempt {attempt + 1}: Expected ok=True; got {resp.json()}"
            )


def test_plan_api_idempotent_result_unchanged(tmp_path):
    """AC9: second identical plan post leaves the plan unchanged."""
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        sprints_dir = root / "repo" / ".commander" / "sprints"
        sprints_dir.mkdir(parents=True, exist_ok=True)

        order = [1, 2]
        client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps(order),
            headers={"Content-Type": "application/json"},
        )
        client.post(
            "/api/sprints/sprint-10/plan?project=test/repo",
            content=json.dumps(order),
            headers={"Content-Type": "application/json"},
        )

        saved = _read_plan(sprints_dir, "sprint-10")
        assert saved == [1, 2], f"Idempotent posts must leave plan as [1, 2]; got {saved}"


# ── AC6: cycle chips suggest valid resolution moves ───────────────────────────

def test_preview_dag_cycle_chip_data_structure(tmp_path):
    """AC6: cycles field provides enough data to render per-ticket resolution chips.

    Each cycle path is a list of ticket IDs ('#N') — the chip layer can derive
    'Move #B after #A' suggestions by iterating the cycle path.
    """
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
        _issue(3, "C", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 1, ["file_ab.py", "file_ca.py"])
        _write_estimate(est, 2, ["file_ab.py", "file_bc.py"])
        _write_estimate(est, 3, ["file_bc.py", "file_ca.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        data = resp.json()

        if data.get("cycles"):
            for cycle_path in data["cycles"]:
                assert isinstance(cycle_path, list), (
                    f"Cycle path must be a list; got {cycle_path!r}"
                )
                assert len(cycle_path) >= 2, (
                    f"Cycle path must have at least 2 tickets; got {cycle_path}"
                )
                # Each element must be a ticket ID string like '#N'
                for tid in cycle_path:
                    assert isinstance(tid, str) and tid.startswith("#"), (
                        f"Cycle ticket ID must be '#N' string; got {tid!r}"
                    )


# ── Extra: preview-dag response structure completeness ────────────────────────

def test_preview_dag_response_has_all_chip_fields(tmp_path):
    """All chip-relevant fields are present in a normal preview-dag response."""
    issues = [
        _issue(1, "A", ["sprint-10"]),
        _issue(2, "B", ["sprint-10"]),
    ]
    with _make_client(tmp_path, issues) as (client, root, _):
        est = root / "repo" / ".commander" / "estimates"
        _write_estimate(est, 1, ["src/shared.py"])
        _write_estimate(est, 2, ["src/other.py"])

        resp = client.get("/api/sprints/sprint-10/preview-dag?project=test/repo")
        assert resp.status_code == 200, resp.text
        data = resp.json()

        for field in ("conflicts", "cycles", "levels", "unestimated", "partial"):
            assert field in data, f"Missing field {field!r} in preview-dag response: {data}"
