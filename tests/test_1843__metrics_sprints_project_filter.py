"""Tests for issue #1843 — GET /api/metrics/sprints project= filter.

AC coverage:
  AC1 — endpoint accepts optional project= query param (slug or owner/repo);
         returns only matching project's sprints when present
  AC2 — no project= param returns all projects' sprints (backward compatible)
  AC4 — behavioral: seed two projects, project= returns only matching project's sprints
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_DASHBOARD_ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(sprints_dir: Path, sprint_num: int, repo: str) -> None:
    """Write a minimal sprint-N-state.json file."""
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": f"sprint-{sprint_num}",
        "project": repo,
        "start_timestamp": "2026-06-01T10:00:00Z",
        "wall_clock_secs": 3600.0,
        "issues": [{"status": "done"}],
    }
    (sprints_dir / f"sprint-{sprint_num}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _call(project_roots: dict, project: str | None = None, **extra_params):
    """Call GET /api/metrics/sprints with optional project= filter.

    project_roots: {repo_string: Path} — maps each repo to its tmp_path root.
    Returns the starlette Response.
    """
    import server
    from starlette.testclient import TestClient

    projects_list = [{"repo": repo} for repo in project_roots]

    def _fake_root(repo):
        return project_roots[repo]

    params = {"from": "2020-01-01", "to": "2030-01-01"}
    if project is not None:
        params["project"] = project
    params.update(extra_params)

    with (
        patch("routers.metrics.projects_module.load_projects",
              return_value=projects_list),
        patch("routers.metrics._project_root_path",
              side_effect=_fake_root),
        patch("routers.metrics._bulk_rework_from_mirror",
              return_value={}),
    ):
        client = TestClient(server.app)
        return client.get("/api/metrics/sprints", params=params)


# ---------------------------------------------------------------------------
# AC1 — project= slug filter
# ---------------------------------------------------------------------------

class TestProjectSlugFilter:
    def test_project_slug_returns_only_matching(self, tmp_path):
        """AC1: project=<slug> returns only that project's sprints."""
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        _make_state(proj_a / ".commander" / "sprints", 1, "owner/proj-a")
        _make_state(proj_b / ".commander" / "sprints", 2, "owner/proj-b")

        resp = _call(
            {"owner/proj-a": proj_a, "owner/proj-b": proj_b},
            project="proj-a",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1, f"Expected 1 sprint, got {len(data)}: {data}"
        assert data[0]["project"].endswith("proj-a"), f"Wrong project: {data[0]['project']}"

    def test_project_full_repo_returns_only_matching(self, tmp_path):
        """AC1: project=owner/repo (full format) also filters correctly."""
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        _make_state(proj_a / ".commander" / "sprints", 1, "owner/proj-a")
        _make_state(proj_b / ".commander" / "sprints", 2, "owner/proj-b")

        resp = _call(
            {"owner/proj-a": proj_a, "owner/proj-b": proj_b},
            project="owner/proj-a",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1, f"Expected 1 sprint, got {len(data)}: {data}"
        assert data[0]["project"] == "owner/proj-a"

    def test_unknown_project_returns_empty(self, tmp_path):
        """AC1: project= value with no match returns an empty list, not an error."""
        proj_a = tmp_path / "proj-a"
        _make_state(proj_a / ".commander" / "sprints", 1, "owner/proj-a")

        resp = _call({"owner/proj-a": proj_a}, project="no-such-project")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_project_filter_excludes_other_projects(self, tmp_path):
        """AC1: sprints from non-matching projects are absent from the response."""
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        _make_state(proj_a / ".commander" / "sprints", 10, "owner/proj-a")
        _make_state(proj_b / ".commander" / "sprints", 20, "owner/proj-b")

        resp = _call(
            {"owner/proj-a": proj_a, "owner/proj-b": proj_b},
            project="proj-b",
        )
        data = resp.json()
        for row in data:
            assert "proj-a" not in row["project"], (
                f"proj-a data should not appear when filtering for proj-b: {row}"
            )


# ---------------------------------------------------------------------------
# AC2 — no project= → backward compatible (returns all)
# ---------------------------------------------------------------------------

class TestNoProjectFilter:
    def test_no_filter_returns_all_projects(self, tmp_path):
        """AC2: omitting project= returns sprints from all registered projects."""
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        _make_state(proj_a / ".commander" / "sprints", 1, "owner/proj-a")
        _make_state(proj_b / ".commander" / "sprints", 1, "owner/proj-b")

        resp = _call({"owner/proj-a": proj_a, "owner/proj-b": proj_b})
        assert resp.status_code == 200
        data = resp.json()
        repos = {row["project"] for row in data}
        assert "owner/proj-a" in repos, "proj-a missing from unfiltered result"
        assert "owner/proj-b" in repos, "proj-b missing from unfiltered result"

    def test_no_filter_returns_200(self, tmp_path):
        """AC2: endpoint still returns HTTP 200 with no project= param."""
        resp = _call({})
        assert resp.status_code == 200

    def test_no_filter_total_count(self, tmp_path):
        """AC2: total sprint count is sum across all projects when not filtered."""
        proj_a = tmp_path / "proj-a"
        proj_b = tmp_path / "proj-b"
        # 2 sprints for proj-a, 3 sprints for proj-b
        for n in [1, 2]:
            _make_state(proj_a / ".commander" / "sprints", n, "owner/proj-a")
        for n in [1, 2, 3]:
            _make_state(proj_b / ".commander" / "sprints", n, "owner/proj-b")

        resp = _call({"owner/proj-a": proj_a, "owner/proj-b": proj_b})
        data = resp.json()
        assert len(data) == 5, f"Expected 5 total sprints (2+3), got {len(data)}"


# ---------------------------------------------------------------------------
# AC4 — behavioral: two projects seeded; filter isolates one
# ---------------------------------------------------------------------------

class TestBehavioralTwoProjects:
    def test_commander_filter_excludes_perf_coach(self, tmp_path):
        """AC4: project=commander returns zero perf-coach sprints."""
        cmd_root = tmp_path / "commander"
        pc_root = tmp_path / "perf-coach"

        for n in [10, 11, 12]:
            _make_state(cmd_root / ".commander" / "sprints", n, "zealchaiwut/commander")
        for n in [5, 6]:
            _make_state(pc_root / ".commander" / "sprints", n, "zealchaiwut/perf-coach")

        resp = _call(
            {
                "zealchaiwut/commander": cmd_root,
                "zealchaiwut/perf-coach": pc_root,
            },
            project="commander",
        )
        assert resp.status_code == 200
        data = resp.json()

        for row in data:
            assert "perf-coach" not in row["project"], (
                f"perf-coach leaked into commander-filtered response: {row}"
            )

    def test_commander_filter_returns_correct_count(self, tmp_path):
        """AC4: project=commander returns exactly commander's sprint count."""
        cmd_root = tmp_path / "commander"
        pc_root = tmp_path / "perf-coach"

        for n in [10, 11, 12]:
            _make_state(cmd_root / ".commander" / "sprints", n, "zealchaiwut/commander")
        for n in [5, 6]:
            _make_state(pc_root / ".commander" / "sprints", n, "zealchaiwut/perf-coach")

        resp = _call(
            {
                "zealchaiwut/commander": cmd_root,
                "zealchaiwut/perf-coach": pc_root,
            },
            project="commander",
        )
        data = resp.json()
        assert len(data) == 3, (
            f"Expected 3 commander sprints, got {len(data)}: "
            f"{[r['sprint_label'] for r in data]}"
        )

    def test_perf_coach_filter_excludes_commander(self, tmp_path):
        """AC4: project=perf-coach returns zero commander sprints."""
        cmd_root = tmp_path / "commander"
        pc_root = tmp_path / "perf-coach"

        for n in [10, 11, 12]:
            _make_state(cmd_root / ".commander" / "sprints", n, "zealchaiwut/commander")
        for n in [5, 6]:
            _make_state(pc_root / ".commander" / "sprints", n, "zealchaiwut/perf-coach")

        resp = _call(
            {
                "zealchaiwut/commander": cmd_root,
                "zealchaiwut/perf-coach": pc_root,
            },
            project="perf-coach",
        )
        assert resp.status_code == 200
        data = resp.json()
        for row in data:
            assert "commander" not in row["project"], (
                f"commander leaked into perf-coach-filtered response: {row}"
            )
        assert len(data) == 2

    def test_sprint_labels_in_response_belong_to_project(self, tmp_path):
        """AC4: each sprint label in the filtered response belongs to the requested project."""
        cmd_root = tmp_path / "commander"
        pc_root = tmp_path / "perf-coach"

        for n in [10, 11]:
            _make_state(cmd_root / ".commander" / "sprints", n, "zealchaiwut/commander")
        for n in [5, 6, 7]:
            _make_state(pc_root / ".commander" / "sprints", n, "zealchaiwut/perf-coach")

        resp = _call(
            {
                "zealchaiwut/commander": cmd_root,
                "zealchaiwut/perf-coach": pc_root,
            },
            project="zealchaiwut/perf-coach",
        )
        data = resp.json()
        assert len(data) == 3
        for row in data:
            assert row["project"] == "zealchaiwut/perf-coach", (
                f"Wrong project in response: {row['project']}"
            )
