"""Tests for issue #419 — Add paginated GET /api/logs/runs endpoint.

Verifies that:
- GET /api/logs/runs returns HTTP 200 with items array and pagination metadata
- Each item includes run_id, project, sprint_label, start_time, end_time, ticket_count, outcome
- Results sorted newest-first by start_time by default
- ?project=<name> filters results to that project only
- ?sprint_label=<label> filters by sprint label (exact match)
- ?start_date and ?end_date filter by run date range (inclusive)
- ?page and ?page_size control pagination; defaults are page=1, page_size=20
- Invalid date formats return HTTP 400 with a descriptive error message
- Missing or unreadable log files return HTTP 200 with empty items array, not 500
- Endpoint implemented in apps/dashboard/server.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SERVER_PY = DASHBOARD_DIR / "server.py"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

REQUIRED_ITEM_FIELDS = {"run_id", "project", "sprint_label", "start_time", "end_time", "ticket_count", "outcome"}


def _make_state(
    project: str,
    sprint_label: str,
    start_ts: str,
    wall_clock: float = 120.0,
    issues: list | None = None,
) -> dict:
    return {
        "project": project,
        "sprint_label": sprint_label,
        "start_timestamp": start_ts,
        "wall_clock_secs": wall_clock,
        "issues": issues or [],
    }


def _write_state(sprints_dir: Path, filename: str, state: dict) -> None:
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / filename).write_text(json.dumps(state), encoding="utf-8")


def _make_client_with_projects(tmp_path: Path, projects: list[dict]):
    """Return a TestClient with load_projects and _project_root_path patched."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    client_holder = {}

    with (
        patch.object(srv.projects_module, "load_projects", return_value=projects),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        client_holder["client"] = client
        client_holder["srv"] = srv
        client_holder["fake_root"] = fake_root
        yield client_holder


# ── AC1: returns 200 with items, page, page_size, total ──────────────────────

def test_endpoint_returns_200_with_pagination_envelope(tmp_path):
    """GET /api/logs/runs must return 200 with items array and pagination metadata."""
    slug = "alpha"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-s1-state.json", _make_state(
        slug, "s1", "2024-06-15T10:00:00"
    ))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/logs/runs")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "items" in data, f"Response must have 'items' key; got keys: {list(data.keys())}"
    assert "page" in data, f"Response must have 'page' key; got keys: {list(data.keys())}"
    assert "page_size" in data, f"Response must have 'page_size' key; got keys: {list(data.keys())}"
    assert "total" in data, f"Response must have 'total' key; got keys: {list(data.keys())}"
    assert isinstance(data["items"], list), "items must be a list"


# ── AC2: each item has all 7 required fields ──────────────────────────────────

def test_each_item_has_required_fields(tmp_path):
    """Each item must include run_id, project, sprint_label, start_time, end_time, ticket_count, outcome."""
    slug = "myproject"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-s2-state.json", _make_state(
        slug, "s2", "2024-07-01T08:00:00", issues=[{"status": "done"}]
    ))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/logs/runs")

    data = resp.json()
    assert data["items"], "Expected at least one item"
    item = data["items"][0]
    missing = REQUIRED_ITEM_FIELDS - set(item.keys())
    assert not missing, f"Item missing fields: {missing}; got: {list(item.keys())}"


def test_item_run_id_is_string(tmp_path):
    """run_id must be a non-empty string."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-sp1-state.json", _make_state(slug, "sp1", "2024-01-10T12:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    item = resp.json()["items"][0]
    assert isinstance(item["run_id"], str) and item["run_id"], "run_id must be a non-empty string"


def test_item_ticket_count_is_integer(tmp_path):
    """ticket_count must be an integer equal to the number of issues in the state file."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-tc-state.json", _make_state(
        slug, "tc", "2024-02-10T12:00:00",
        issues=[{"status": "done"}, {"status": "done"}, {"status": "done"}]
    ))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    item = resp.json()["items"][0]
    assert item["ticket_count"] == 3, f"ticket_count must be 3; got {item['ticket_count']}"


def test_item_start_time_is_iso8601_string(tmp_path):
    """start_time must be a valid ISO 8601 datetime string."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-iso-state.json", _make_state(slug, "iso", "2024-03-15T09:30:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    item = resp.json()["items"][0]
    try:
        datetime.fromisoformat(item["start_time"])
    except ValueError:
        pytest.fail(f"start_time is not valid ISO 8601: {item['start_time']!r}")


def test_item_end_time_after_start_time(tmp_path):
    """end_time must be after start_time by wall_clock_secs."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-et-state.json", _make_state(
        slug, "et", "2024-04-01T10:00:00", wall_clock=300.0
    ))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    item = resp.json()["items"][0]
    start = datetime.fromisoformat(item["start_time"])
    end = datetime.fromisoformat(item["end_time"])
    assert end > start, f"end_time ({end}) must be after start_time ({start})"


# ── AC3: results sorted newest-first by start_time ───────────────────────────

def test_results_sorted_newest_first(tmp_path):
    """Results must be sorted by start_time descending (newest first)."""
    slug = "sortproj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-old-state.json", _make_state(slug, "old", "2024-01-01T00:00:00"))
    _write_state(sprints_dir, "sprint-mid-state.json", _make_state(slug, "mid", "2024-06-01T00:00:00"))
    _write_state(sprints_dir, "sprint-new-state.json", _make_state(slug, "new", "2024-12-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    items = resp.json()["items"]
    assert len(items) == 3, f"Expected 3 items, got {len(items)}"
    times = [item["start_time"] for item in items]
    assert times == sorted(times, reverse=True), f"Items not sorted newest-first: {times}"


# ── AC4: ?project=<name> filter ──────────────────────────────────────────────

def test_project_filter_returns_only_matching(tmp_path):
    """?project=<name> must return only items for that project."""
    for slug in ("alpha", "beta"):
        sprints_dir = tmp_path / slug / ".commander" / "sprints"
        _write_state(sprints_dir, f"sprint-s1-state.json", _make_state(slug, "s1", "2024-05-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[
            {"repo": "alpha"}, {"repo": "beta"}
        ]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/logs/runs?project=alpha")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items, "Expected at least one item for project=alpha"
    for item in items:
        assert item["project"] == "alpha", (
            f"All items must have project='alpha'; got {item['project']!r}"
        )


def test_project_filter_excludes_other_projects(tmp_path):
    """?project=alpha must exclude items from other projects."""
    for slug in ("alpha", "gamma"):
        sprints_dir = tmp_path / slug / ".commander" / "sprints"
        _write_state(sprints_dir, "sprint-s1-state.json", _make_state(slug, "s1", "2024-05-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[
            {"repo": "alpha"}, {"repo": "gamma"}
        ]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?project=alpha")

    projects = {item["project"] for item in resp.json()["items"]}
    assert "gamma" not in projects, f"gamma must not appear when project=alpha; got {projects}"


# ── AC5: ?sprint_label=<label> exact match filter ────────────────────────────

def test_sprint_label_filter_exact_match(tmp_path):
    """?sprint_label=<label> must return only items with that exact sprint label."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-q1-state.json", _make_state(slug, "q1", "2024-03-01T00:00:00"))
    _write_state(sprints_dir, "sprint-q2-state.json", _make_state(slug, "q2", "2024-06-01T00:00:00"))
    _write_state(sprints_dir, "sprint-q3-state.json", _make_state(slug, "q3", "2024-09-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?sprint_label=q2")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1, f"Expected 1 item for sprint_label=q2, got {len(items)}"
    assert items[0]["sprint_label"] == "q2", f"sprint_label must be 'q2'; got {items[0]['sprint_label']!r}"


def test_sprint_label_filter_no_partial_match(tmp_path):
    """?sprint_label=q must NOT match sprint_label=q2 (exact match only)."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-q2-state.json", _make_state(slug, "q2", "2024-06-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?sprint_label=q")

    items = resp.json()["items"]
    assert len(items) == 0, f"sprint_label filter must be exact; got {len(items)} items for 'q'"


# ── AC6: date range filters ───────────────────────────────────────────────────

def test_start_date_filter_excludes_older_runs(tmp_path):
    """?start_date=2024-06-01 must exclude runs before that date."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-may-state.json", _make_state(slug, "may", "2024-05-15T00:00:00"))
    _write_state(sprints_dir, "sprint-jun-state.json", _make_state(slug, "jun", "2024-06-15T00:00:00"))
    _write_state(sprints_dir, "sprint-jul-state.json", _make_state(slug, "jul", "2024-07-15T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?start_date=2024-06-01")

    assert resp.status_code == 200
    items = resp.json()["items"]
    labels = {i["sprint_label"] for i in items}
    assert "may" not in labels, f"May run must be excluded by start_date=2024-06-01; got {labels}"
    assert "jun" in labels, f"Jun run must be included; got {labels}"
    assert "jul" in labels, f"Jul run must be included; got {labels}"


def test_end_date_filter_excludes_newer_runs(tmp_path):
    """?end_date=2024-06-30 must exclude runs after that date."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-may-state.json", _make_state(slug, "may", "2024-05-15T00:00:00"))
    _write_state(sprints_dir, "sprint-jun-state.json", _make_state(slug, "jun", "2024-06-15T00:00:00"))
    _write_state(sprints_dir, "sprint-jul-state.json", _make_state(slug, "jul", "2024-07-15T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?end_date=2024-06-30")

    assert resp.status_code == 200
    items = resp.json()["items"]
    labels = {i["sprint_label"] for i in items}
    assert "jul" not in labels, f"Jul run must be excluded by end_date=2024-06-30; got {labels}"
    assert "may" in labels, f"May run must be included; got {labels}"
    assert "jun" in labels, f"Jun run must be included; got {labels}"


def test_date_range_combined_filter(tmp_path):
    """?start_date + ?end_date must return only runs within the range (inclusive)."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    for label, ts in [("apr", "2024-04-01T00:00:00"), ("jun", "2024-06-15T00:00:00"), ("aug", "2024-08-01T00:00:00")]:
        _write_state(sprints_dir, f"sprint-{label}-state.json", _make_state(slug, label, ts))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?start_date=2024-06-01&end_date=2024-06-30")

    assert resp.status_code == 200
    items = resp.json()["items"]
    labels = {i["sprint_label"] for i in items}
    assert labels == {"jun"}, f"Only 'jun' must be in range; got {labels}"


# ── AC7: pagination params ────────────────────────────────────────────────────

def test_default_page_and_page_size(tmp_path):
    """Defaults must be page=1, page_size=20."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    _write_state(sprints_dir, "sprint-s1-state.json", _make_state(slug, "s1", "2024-01-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    data = resp.json()
    assert data["page"] == 1, f"Default page must be 1; got {data['page']}"
    assert data["page_size"] == 20, f"Default page_size must be 20; got {data['page_size']}"


def test_pagination_page2_returns_different_items(tmp_path):
    """?page=2&page_size=3 must return items that differ from page 1."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    for i in range(6):
        ts = f"2024-0{i+1:02d}-01T00:00:00" if i < 9 else f"2024-{i+1}-01T00:00:00"
        ts = f"2024-01-{i+1:02d}T00:00:00"
        _write_state(sprints_dir, f"sprint-s{i}-state.json", _make_state(slug, f"s{i}", ts))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        page1 = client.get("/api/logs/runs?page=1&page_size=3").json()
        page2 = client.get("/api/logs/runs?page=2&page_size=3").json()

    assert page2["page"] == 2, f"page in metadata must be 2; got {page2['page']}"
    assert len(page2["items"]) <= 3, f"page_size=3 must return at most 3 items"
    ids1 = {i["run_id"] for i in page1["items"]}
    ids2 = {i["run_id"] for i in page2["items"]}
    assert not ids1.intersection(ids2), f"Page 1 and page 2 must not share run_ids"


def test_pagination_total_reflects_all_items(tmp_path):
    """total in pagination envelope must equal the count of all matching items."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    for i in range(5):
        _write_state(sprints_dir, f"sprint-s{i}-state.json", _make_state(slug, f"s{i}", f"2024-01-0{i+1}T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?page=1&page_size=2")

    data = resp.json()
    assert data["total"] == 5, f"total must be 5 (all items); got {data['total']}"
    assert len(data["items"]) == 2, f"items on page must be 2; got {len(data['items'])}"


def test_page_size_controls_number_of_returned_items(tmp_path):
    """?page_size=3 must return at most 3 items per page."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    for i in range(10):
        _write_state(sprints_dir, f"sprint-s{i}-state.json", _make_state(slug, f"s{i}", f"2024-01-{i+1:02d}T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?page_size=3")

    assert len(resp.json()["items"]) == 3, (
        f"page_size=3 must return exactly 3 items; got {len(resp.json()['items'])}"
    )


# ── AC8: invalid date formats return HTTP 400 ─────────────────────────────────

def test_invalid_start_date_returns_400(tmp_path):
    """?start_date=not-a-date must return HTTP 400 with descriptive error."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?start_date=not-a-date")

    assert resp.status_code == 400, f"Expected 400 for invalid start_date; got {resp.status_code}"
    body = resp.json()
    detail = body.get("detail", "")
    assert detail, f"Error response must include a 'detail' field with a message; got {body}"


def test_invalid_end_date_returns_400(tmp_path):
    """?end_date=badvalue must return HTTP 400 with descriptive error."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?end_date=badvalue")

    assert resp.status_code == 400, f"Expected 400 for invalid end_date; got {resp.status_code}"
    body = resp.json()
    assert body.get("detail"), f"Error response must include a non-empty 'detail'; got {body}"


def test_invalid_start_date_error_message_is_descriptive(tmp_path):
    """400 error for invalid start_date must mention the invalid value or format."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs?start_date=2024/06/01")

    assert resp.status_code == 400
    detail = resp.json().get("detail", "")
    assert "2024/06/01" in detail or "ISO" in detail or "format" in detail.lower(), (
        f"Error detail must describe the invalid value or format; got: {detail!r}"
    )


# ── AC9: missing/unreadable log files return 200 with empty items ─────────────

def test_no_sprints_dir_returns_200_empty_items(tmp_path):
    """If no sprints directory exists, must return 200 with items=[]."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": "ghost"}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    assert resp.status_code == 200, f"Expected 200 even when no logs exist; got {resp.status_code}"
    data = resp.json()
    assert data["items"] == [], f"items must be [] when no logs exist; got {data['items']}"


def test_no_projects_returns_200_empty_items(tmp_path):
    """If load_projects returns empty list, must return 200 with items=[]."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_corrupt_state_file_does_not_cause_500(tmp_path):
    """Corrupt JSON in a state file must be skipped gracefully, not cause 500."""
    slug = "proj"
    sprints_dir = tmp_path / slug / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True)
    (sprints_dir / "sprint-corrupt-state.json").write_text("NOT VALID JSON", encoding="utf-8")
    _write_state(sprints_dir, "sprint-good-state.json", _make_state(slug, "good", "2024-05-01T00:00:00"))

    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": slug}]),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    assert resp.status_code == 200, f"Corrupt state file must not cause 500; got {resp.status_code}"
    assert resp.json()["items"], "Good state file must still produce items despite corrupt sibling"


def test_load_projects_exception_returns_200_empty(tmp_path):
    """If load_projects raises an exception, endpoint must still return 200 with items=[]."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    def fake_root(repo: str) -> Path:
        return tmp_path / (repo.split("/")[-1] if "/" in repo else repo)

    with (
        patch.object(srv.projects_module, "load_projects", side_effect=Exception("DB offline")),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        resp = TestClient(srv.app).get("/api/logs/runs")

    assert resp.status_code == 200, f"load_projects failure must return 200; got {resp.status_code}"
    assert resp.json()["items"] == []


# ── AC10: endpoint implemented in server.py ───────────────────────────────────

def test_endpoint_defined_in_server_py():
    """get_logs_runs function must be defined in apps/dashboard/server.py."""
    src = SERVER_PY.read_text()
    assert "def get_logs_runs(" in src or "async def get_logs_runs(" in src, (
        "get_logs_runs endpoint must be defined in apps/dashboard/server.py"
    )
    assert '"/api/logs/runs"' in src or "'/api/logs/runs'" in src, (
        "Route /api/logs/runs must be registered in apps/dashboard/server.py"
    )


def test_endpoint_registered_with_get_decorator():
    """The route must use @app.get('/api/logs/runs') decorator."""
    src = SERVER_PY.read_text()
    assert '@app.get("/api/logs/runs")' in src or "@app.get('/api/logs/runs')" in src, (
        "server.py must register GET /api/logs/runs with @app.get decorator"
    )
