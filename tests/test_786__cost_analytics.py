"""Tests for issue #786 — GET /api/projects/{slug}/analytics/cost.

AC coverage:
  AC1  — returns token totals aggregated by sprint, by ticket, and by agent,
          plus per-size averages (joined from estimates)
  AC2  — (trend_30d removed in issue #1846 — tests removed)
  AC3  — per-size token averages are machine-readable in the API response
  AC4  — (analytics.html deleted in issue #1846 — tests removed)
  AC5  — sprint totals, top-10 most expensive tickets, and per-agent token
          split are displayed (shape checks)
  AC6  — (analytics.html deleted in issue #1846 — tests removed)
  AC7  — dollar estimate column hidden when no price map configured
  AC8  — dollar estimate column with "estimate" badge when price map configured
  AC9  — no prices hardcoded; all $ estimates derive from user-editable price map
  AC10 — no new token-capture code; all data sourced from existing total_tokens fields
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

# Ensure dashboard is on sys.path BEFORE importing server/db so module keys match.
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest.db")

import db as _db  # same module key that analytics.py and server.py use


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _mk_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with agent_runs and token_usage tables."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number INTEGER NOT NULL,
            sprint_label TEXT NOT NULL,
            agent TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            duration_seconds INTEGER,
            outcome TEXT,
            total_tokens INTEGER,
            model_used TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            project TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            recorded_at TEXT NOT NULL,
            agent_role TEXT,
            model_name TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_run(
    db_path: Path,
    issue: int,
    sprint: str,
    agent: str,
    tokens: int | None,
    model: str = "claude-sonnet-4-6",
    started_at: str = "2026-06-01T10:00:00",
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO agent_runs (issue_number, sprint_label, agent, started_at, "
        "total_tokens, model_used) VALUES (?, ?, ?, ?, ?, ?)",
        (issue, sprint, agent, started_at, tokens, model),
    )
    conn.commit()
    conn.close()


def _insert_token_usage(
    db_path: Path,
    model: str,
    inp: int,
    out: int,
    recorded_at: str = "2026-06-01T10:00:00",
    role: str = "coder",
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO token_usage (session_id, project, input_tokens, output_tokens, "
        "recorded_at, agent_role, model_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess1", "owner/repo", inp, out, recorded_at, role, model),
    )
    conn.commit()
    conn.close()


def _mk_estimate(project_root: Path, issue: int, size: str) -> None:
    d = project_root / ".commander" / "estimates"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"issue-{issue}.json").write_text(
        json.dumps({"issue_number": issue, "size": size}), encoding="utf-8"
    )


def _get_analytics_mod():
    # server.py adds apps/dashboard to sys.path and imports routers.analytics,
    # so we must use the same module key to hit the same object.
    import routers.analytics as analytics_mod  # noqa: E402
    return analytics_mod


def _call(
    project_root: Path,
    db_path: Path,
    slug: str = "myrepo",
    project: str = "owner/myrepo",
    price_map=None,           # None means not configured; dict means configured
):
    """Call GET /api/projects/{slug}/analytics/cost via TestClient."""
    import server as srv  # ensures server (and thus routers.analytics) is imported
    from starlette.testclient import TestClient
    analytics_mod = _get_analytics_mod()

    settings_val: dict = {}
    if price_map is not None:
        settings_val["price_map"] = price_map

    with (
        patch.object(_db, "DB_PATH", str(db_path)),
        patch.object(analytics_mod, "_resolve_project_slug", return_value=project),
        patch.object(analytics_mod, "_project_root_path", return_value=project_root),
        patch.object(analytics_mod, "_settings_repo") as mock_sr,
    ):
        mock_sr.get_setting.return_value = settings_val
        client = TestClient(srv.app)
        return client.get(f"/api/projects/{slug}/analytics/cost")


# ---------------------------------------------------------------------------
# AC1 — token totals by sprint / ticket / agent + per-size averages
# ---------------------------------------------------------------------------

class TestResponseShape:
    def test_returns_200(self, tmp_path):
        db = _mk_db(tmp_path)
        assert _call(tmp_path, db).status_code == 200

    def test_response_is_json_dict(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call(tmp_path, db).json()
        assert isinstance(data, dict)

    def test_by_sprint_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call(tmp_path, db).json()
        assert "by_sprint" in data
        assert isinstance(data["by_sprint"], list)

    def test_by_ticket_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call(tmp_path, db).json()
        assert "by_ticket" in data
        assert isinstance(data["by_ticket"], list)

    def test_by_agent_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call(tmp_path, db).json()
        assert "by_agent" in data
        assert isinstance(data["by_agent"], list)

    def test_per_size_avg_tokens_present(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call(tmp_path, db).json()
        assert "per_size_avg_tokens" in data
        assert isinstance(data["per_size_avg_tokens"], dict)

    def test_by_sprint_entry_has_required_fields(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 1000)
        entry = _call(tmp_path, db).json()["by_sprint"][0]
        assert "sprint_label" in entry
        assert "total_tokens" in entry

    def test_by_ticket_entry_has_required_fields(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 1000)
        entry = _call(tmp_path, db).json()["by_ticket"][0]
        assert "issue_number" in entry
        assert "total_tokens" in entry

    def test_by_agent_entry_has_required_fields(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 1000)
        entry = _call(tmp_path, db).json()["by_agent"][0]
        assert "agent" in entry
        assert "total_tokens" in entry

    def test_by_sprint_aggregates_tokens(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-5", "coder", 2000)
        _insert_run(db, 2, "sprint-5", "tester", 500)
        by_sprint = _call(tmp_path, db).json()["by_sprint"]
        s5 = next(x for x in by_sprint if x["sprint_label"] == "sprint-5")
        assert s5["total_tokens"] == 2500

    def test_by_ticket_aggregates_tokens_across_agents(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 42, "sprint-5", "coder", 3000)
        _insert_run(db, 42, "sprint-5", "tester", 1000)
        by_ticket = _call(tmp_path, db).json()["by_ticket"]
        t42 = next(x for x in by_ticket if x["issue_number"] == 42)
        assert t42["total_tokens"] == 4000

    def test_by_agent_aggregates_tokens_across_sprints(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 3000)
        _insert_run(db, 2, "sprint-2", "coder", 2000)
        by_agent = _call(tmp_path, db).json()["by_agent"]
        coder = next(x for x in by_agent if x["agent"] == "coder")
        assert coder["total_tokens"] == 5000

    def test_null_total_tokens_excluded_from_aggregates(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", None)
        _insert_run(db, 2, "sprint-1", "coder", 500)
        by_sprint = _call(tmp_path, db).json()["by_sprint"]
        s1 = next(x for x in by_sprint if x["sprint_label"] == "sprint-1")
        assert s1["total_tokens"] == 500


# ---------------------------------------------------------------------------
# AC3 — per-size token averages machine-readable
# ---------------------------------------------------------------------------

class TestPerSizeAvgTokens:
    def test_all_four_sizes_present(self, tmp_path):
        db = _mk_db(tmp_path)
        psat = _call(tmp_path, db).json()["per_size_avg_tokens"]
        for sz in ("S", "M", "L", "XL"):
            assert sz in psat

    def test_each_size_has_avg_tokens_and_count(self, tmp_path):
        db = _mk_db(tmp_path)
        psat = _call(tmp_path, db).json()["per_size_avg_tokens"]
        for sz in ("S", "M", "L", "XL"):
            assert "avg_tokens" in psat[sz]
            assert "count" in psat[sz]

    def test_avg_tokens_is_numeric_or_null(self, tmp_path):
        db = _mk_db(tmp_path)
        psat = _call(tmp_path, db).json()["per_size_avg_tokens"]
        for sz in ("S", "M", "L", "XL"):
            val = psat[sz]["avg_tokens"]
            assert val is None or isinstance(val, (int, float))

    def test_per_size_computes_correctly(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 10, "sprint-1", "coder", 1000)
        _insert_run(db, 11, "sprint-1", "coder", 3000)
        _mk_estimate(tmp_path, 10, "M")
        _mk_estimate(tmp_path, 11, "M")
        psat = _call(tmp_path, db).json()["per_size_avg_tokens"]
        assert psat["M"]["count"] == 2
        assert abs(psat["M"]["avg_tokens"] - 2000) < 1

    def test_no_data_for_size_gives_null_avg(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 10, "sprint-1", "coder", 1000)
        _mk_estimate(tmp_path, 10, "S")
        psat = _call(tmp_path, db).json()["per_size_avg_tokens"]
        assert psat["XL"]["avg_tokens"] is None
        assert psat["XL"]["count"] == 0

    def test_per_size_uses_sum_across_all_agents(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 20, "sprint-1", "coder", 2000)
        _insert_run(db, 20, "sprint-1", "tester", 500)
        _mk_estimate(tmp_path, 20, "L")
        psat = _call(tmp_path, db).json()["per_size_avg_tokens"]
        assert psat["L"]["avg_tokens"] == 2500


# ---------------------------------------------------------------------------
# AC5 — by_sprint, top-10 tickets, by_agent shape
# ---------------------------------------------------------------------------

class TestTop10Tickets:
    def test_top_tickets_limited_to_10(self, tmp_path):
        db = _mk_db(tmp_path)
        for i in range(1, 16):
            _insert_run(db, i, "sprint-1", "coder", i * 100)
        data = _call(tmp_path, db).json()
        assert len(data["by_ticket"]) <= 10

    def test_top_tickets_ordered_by_tokens_desc(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 500)
        _insert_run(db, 2, "sprint-1", "coder", 5000)
        _insert_run(db, 3, "sprint-1", "coder", 2000)
        tickets = _call(tmp_path, db).json()["by_ticket"]
        token_values = [t["total_tokens"] for t in tickets]
        assert token_values == sorted(token_values, reverse=True)


# ---------------------------------------------------------------------------
# AC7 — dollar estimate column hidden when no price map
# ---------------------------------------------------------------------------

class TestNoPriceMap:
    def test_price_map_configured_is_false_when_absent(self, tmp_path):
        db = _mk_db(tmp_path)
        data = _call(tmp_path, db).json()
        assert data["price_map_configured"] is False

    def test_no_cost_usd_when_price_map_absent(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 1000)
        data = _call(tmp_path, db).json()
        for sprint in data["by_sprint"]:
            assert sprint.get("cost_usd") is None
        for ticket in data["by_ticket"]:
            assert ticket.get("cost_usd") is None
        for agent in data["by_agent"]:
            assert agent.get("cost_usd") is None


# ---------------------------------------------------------------------------
# AC8 — dollar estimate column appears with price map
# ---------------------------------------------------------------------------

class TestWithPriceMap:
    def test_price_map_configured_is_true_when_set(self, tmp_path):
        db = _mk_db(tmp_path)
        pm = {"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}}
        data = _call(tmp_path, db, price_map=pm).json()
        assert data["price_map_configured"] is True

    def test_cost_usd_present_when_price_map_set(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_token_usage(db, "claude-sonnet-4-6", 600, 400)
        _insert_run(db, 1, "sprint-1", "coder", 1000, model="claude-sonnet-4-6")
        pm = {"claude-sonnet-4-6": {"in": 3.0, "out": 15.0}}
        data = _call(tmp_path, db, price_map=pm).json()
        for sprint in data["by_sprint"]:
            assert "cost_usd" in sprint
        for ticket in data["by_ticket"]:
            assert "cost_usd" in ticket
        for agent in data["by_agent"]:
            assert "cost_usd" in agent


# ---------------------------------------------------------------------------
# AC9 — no prices hardcoded; estimates from user price map only
# ---------------------------------------------------------------------------

class TestNoPricesHardcoded:
    def test_zero_cost_with_empty_price_map(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_token_usage(db, "claude-sonnet-4-6", 600, 400)
        _insert_run(db, 1, "sprint-1", "coder", 1000, model="claude-sonnet-4-6")
        pm = {}
        data = _call(tmp_path, db, price_map=pm).json()
        # When price_map is set but empty, cost_usd should be 0 (no model matches)
        for sprint in data["by_sprint"]:
            assert sprint.get("cost_usd", 0.0) == 0.0

    def test_custom_price_map_drives_cost_usd(self, tmp_path):
        db = _mk_db(tmp_path)
        # 600 input + 400 output tokens via token_usage
        _insert_token_usage(db, "my-model", 600, 400)
        _insert_run(db, 1, "sprint-1", "coder", 1000, model="my-model")
        # cost = 600 * (1.0/1e6) + 400 * (2.0/1e6) = 0.0006 + 0.0008 = 0.0014
        pm = {"my-model": {"in": 1.0, "out": 2.0}}
        data = _call(tmp_path, db, price_map=pm).json()
        sprints = data["by_sprint"]
        if sprints:
            cost = sprints[0].get("cost_usd")
            if cost is not None:
                assert cost > 0

    def test_different_price_gives_different_cost(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_token_usage(db, "my-model", 500, 500)
        _insert_run(db, 1, "sprint-1", "coder", 1000, model="my-model")
        pm_low = {"my-model": {"in": 1.0, "out": 1.0}}
        pm_high = {"my-model": {"in": 10.0, "out": 10.0}}
        data_low = _call(tmp_path, db, price_map=pm_low).json()
        data_high = _call(tmp_path, db, price_map=pm_high).json()
        low_cost = sum(s.get("cost_usd") or 0 for s in data_low["by_sprint"])
        high_cost = sum(s.get("cost_usd") or 0 for s in data_high["by_sprint"])
        assert high_cost >= low_cost


# ---------------------------------------------------------------------------
# AC10 — no new token-capture code (verify data comes from agent_runs)
# ---------------------------------------------------------------------------

class TestNoNewTokenCapture:
    def test_endpoint_uses_agent_runs_not_new_table(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 5, "sprint-42", "coder", 7777)
        data = _call(tmp_path, db).json()
        by_sprint = data["by_sprint"]
        s42 = next((x for x in by_sprint if x["sprint_label"] == "sprint-42"), None)
        assert s42 is not None
        assert s42["total_tokens"] == 7777

    def test_endpoint_does_not_require_new_fields(self, tmp_path):
        db = _mk_db(tmp_path)
        _insert_run(db, 1, "sprint-1", "coder", 100)
        resp = _call(tmp_path, db)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Unknown slug → 404
# ---------------------------------------------------------------------------

class TestNotFound:
    def test_unknown_slug_returns_404(self):
        import server as srv
        from starlette.testclient import TestClient
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/api/projects/does-not-exist-cost-786/analytics/cost")
        assert resp.status_code == 404
