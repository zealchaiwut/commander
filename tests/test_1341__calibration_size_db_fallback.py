"""Tests for issue #1341: _resolve_calibration_size reads size-* from SQLite issues table.

AC items verified:
  AC1 — _resolve_calibration_size priority (3) reads size-* from SQLite issues table
  AC2 — empty state labels + DB row → correct size returned
  AC3 — no DB row for issue → None, no crash, no GitHub call
  AC4 — priorities 1 (JSON file) and 2 (state.estimates) still take precedence
  AC5 — unit test: no estimate file, no state.estimates, no state labels, size-M in
         SQLite → resolved to "M"
  AC6 — no live GitHub API call during size resolution
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path, rows: list[tuple[str, int, list]] | None = None) -> Path:
    """Create a temp SQLite DB with the issues table; insert optional rows."""
    db_path = tmp_path / "test.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE issues (
                repo         TEXT NOT NULL DEFAULT '',
                issue_number INTEGER NOT NULL,
                title        TEXT,
                state        TEXT,
                labels       TEXT NOT NULL DEFAULT '[]',
                updated_at   TEXT,
                raw          TEXT,
                PRIMARY KEY (repo, issue_number)
            )
        """)
        for repo, num, labels in (rows or []):
            conn.execute(
                "INSERT INTO issues (repo, issue_number, labels) VALUES (?, ?, ?)",
                (repo, int(num), json.dumps(labels)),
            )
        conn.commit()
    return db_path


def _write_state(project_root: Path, sprint_label: str, issues: list[dict],
                 project: str = "owner/repo") -> Path:
    import re
    m = re.search(r"(\d+)", sprint_label)
    n = m.group(1) if m else sprint_label
    sprints_dir = project_root / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    path = sprints_dir / f"sprint-{n}-state.json"
    path.write_text(json.dumps({
        "project": project,
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "issues": issues,
    }), encoding="utf-8")
    return path


def _done_issue(num: int, coder_min: float = 10.0, tester_min: float = 5.0) -> dict:
    """Done issue dict with NO labels key (simulates real state files)."""
    return {
        "number": num,
        "status": "done",
        "coder_started_at": "2026-06-01T10:00:00Z",
        "coder_finished_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_started_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_finished_at": f"2026-06-01T10:{int(coder_min + tester_min):02d}:00Z",
    }


# ---------------------------------------------------------------------------
# AC1 — _resolve_calibration_size reads size-* from SQLite issues table
# ---------------------------------------------------------------------------

class TestAC1SizeFromSQLite:
    """AC1: priority (3) resolves from SQLite issues table, not just state file labels."""

    def test_size_m_from_db_when_no_state_labels(self, tmp_path):
        """DB size-M resolves to 'M' when issue_labels is empty."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("owner/repo", 42, [{"name": "size-M", "color": "0075ca"}])])
        result = _resolve_calibration_size(
            42, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="owner/repo"
        )
        assert result == "M"

    def test_size_xl_from_db(self, tmp_path):
        """DB size-XL resolves to 'XL'."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("myorg/myrepo", 7, [{"name": "size-XL"}])])
        result = _resolve_calibration_size(
            7, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="myorg/myrepo"
        )
        assert result == "XL"

    def test_size_s_from_db(self, tmp_path):
        """DB size-S resolves to 'S'."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("a/b", 1, [{"name": "size-S"}])])
        result = _resolve_calibration_size(
            1, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="a/b"
        )
        assert result == "S"


# ---------------------------------------------------------------------------
# AC2 — empty state labels + DB row → size returned
# ---------------------------------------------------------------------------

class TestAC2EmptyStateLabelsWithDBHit:
    """AC2: issue with no 'labels' key in state dict but DB row → size resolved."""

    def test_empty_list_from_state_uses_db(self, tmp_path):
        """issue.get('labels') == [] → DB lookup fires."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("org/project", 99, [{"name": "size-L"}])])
        result = _resolve_calibration_size(
            99, tmp_path / "no_estimates", {}, [],
            db_path=db_path, repo="org/project"
        )
        assert result == "L"

    def test_missing_labels_key_uses_db(self, tmp_path):
        """Passing issue_labels=[] (simulating missing key) triggers DB lookup."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("x/y", 5, [{"name": "size-M"}])])
        result = _resolve_calibration_size(
            5, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="x/y"
        )
        assert result == "M"


# ---------------------------------------------------------------------------
# AC3 — no DB row → None, no crash
# ---------------------------------------------------------------------------

class TestAC3NoRowInDB:
    """AC3: missing DB row returns None without raising."""

    def test_returns_none_when_no_db_row(self, tmp_path):
        """Empty issues table → None."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path)
        result = _resolve_calibration_size(
            55, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="owner/repo"
        )
        assert result is None

    def test_returns_none_when_db_file_missing(self, tmp_path):
        """Nonexistent db_path → None without crash."""
        from calibration_cache_service import _resolve_calibration_size

        result = _resolve_calibration_size(
            55, tmp_path / "estimates", {}, [],
            db_path=tmp_path / "nonexistent.db", repo="owner/repo"
        )
        assert result is None

    def test_returns_none_when_wrong_repo(self, tmp_path):
        """Row for different repo → not matched, returns None."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("other/repo", 10, [{"name": "size-M"}])])
        result = _resolve_calibration_size(
            10, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="owner/repo"
        )
        assert result is None


# ---------------------------------------------------------------------------
# AC4 — priorities 1 and 2 unchanged
# ---------------------------------------------------------------------------

class TestAC4PrioritiesUnchanged:
    """AC4: JSON estimate file and state.estimates still take precedence over DB."""

    def test_json_estimate_beats_db(self, tmp_path):
        """JSON estimate file (priority 1) overrides DB label."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("owner/repo", 10, [{"name": "size-XL"}])])
        estimates_dir = tmp_path / "estimates"
        estimates_dir.mkdir()
        (estimates_dir / "issue-10.json").write_text(json.dumps({"size": "S"}))

        result = _resolve_calibration_size(
            10, estimates_dir, {}, [],
            db_path=db_path, repo="owner/repo"
        )
        assert result == "S", "JSON file must override DB label"

    def test_state_estimates_beats_db(self, tmp_path):
        """state.estimates (priority 2) overrides DB label."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("owner/repo", 20, [{"name": "size-L"}])])
        state_estimates = {20: {"size": "M"}}

        result = _resolve_calibration_size(
            20, tmp_path / "estimates", state_estimates, [],
            db_path=db_path, repo="owner/repo"
        )
        assert result == "M", "state.estimates must override DB label"

    def test_state_file_label_still_works_without_db(self, tmp_path):
        """Existing callers passing issue_labels without db_path still work."""
        from calibration_cache_service import _resolve_calibration_size

        result = _resolve_calibration_size(
            33, tmp_path / "estimates", {}, [{"name": "size-L"}]
        )
        assert result == "L", "State file label must still resolve without db_path"

    def test_state_file_label_beats_db(self, tmp_path):
        """State file label takes priority over DB label when both present."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("owner/repo", 15, [{"name": "size-XL"}])])
        result = _resolve_calibration_size(
            15, tmp_path / "estimates", {}, [{"name": "size-S"}],
            db_path=db_path, repo="owner/repo"
        )
        assert result == "S", "State file label must override DB label"


# ---------------------------------------------------------------------------
# AC5 — exact scenario from issue AC
# ---------------------------------------------------------------------------

class TestAC5IssueScenario:
    """AC5: no estimate file, no state.estimates, no state labels, size-M in SQLite → 'M'."""

    def test_ac5_full_scenario(self, tmp_path):
        """Issue #1341 AC5 literal scenario: all sources absent except SQLite."""
        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("myorg/myrepo", 42, [{"name": "size-M", "color": "0075ca"}])])

        estimates_dir = tmp_path / "estimates"  # not created — no JSON
        state_estimates = {}                    # no state.estimates entry
        issue_labels = []                       # no labels in state dict

        result = _resolve_calibration_size(
            42, estimates_dir, state_estimates, issue_labels,
            db_path=db_path, repo="myorg/myrepo"
        )
        assert result == "M", (
            f"Expected 'M' from SQLite issues table, got {result!r}. "
            "DB fallback must fire when JSON, state.estimates, and state labels are absent."
        )


# ---------------------------------------------------------------------------
# AC6 — no live GitHub API call
# ---------------------------------------------------------------------------

class TestAC6NoGitHubCall:
    """AC6: size resolution uses only local SQLite — no HTTP/GitHub calls."""

    def test_no_github_call_via_urllib(self, tmp_path, monkeypatch):
        """urllib.request.urlopen must not be called during resolution."""
        import urllib.request

        def fail_urlopen(*args, **kwargs):
            raise AssertionError("GitHub API called during size resolution — AC6 violated")

        monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

        from calibration_cache_service import _resolve_calibration_size

        db_path = _make_db(tmp_path, [("owner/repo", 5, [{"name": "size-S"}])])
        result = _resolve_calibration_size(
            5, tmp_path / "estimates", {}, [],
            db_path=db_path, repo="owner/repo"
        )
        assert result == "S"


# ---------------------------------------------------------------------------
# Integration: _refresh_calibration_cache threads DB path through absorb
# ---------------------------------------------------------------------------

class TestIntegrationAbsorbWithDB:
    """Verify _refresh_calibration_cache uses DB fallback for state files without labels."""

    def test_refresh_counts_ticket_via_db_label(self, tmp_path):
        """_refresh_calibration_cache resolves size from DB when state file has no labels."""
        from calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        # State file issue with NO labels key — simulates real production state files
        issue = _done_issue(101)
        _write_state(project_root, "sprint-1", [issue], project="owner/repo")

        # DB has size-M label for this issue
        db_path = _make_db(tmp_path, [("owner/repo", 101, [{"name": "size-M"}])])

        cache = _refresh_calibration_cache(project_root, configured_minutes, db_path=db_path)
        m_count = cache["by_size"]["M"]["count"]
        assert m_count == 1, (
            f"Ticket not counted via DB label fallback: M count={m_count}. "
            "State file has no labels; DB must supply the size."
        )

    def test_refresh_skips_ticket_when_no_db_row(self, tmp_path):
        """Ticket with no estimate, no state labels, no DB row is skipped (not counted)."""
        from calibration_cache_service import _refresh_calibration_cache

        configured_minutes = {"S": 5, "M": 15, "L": 30, "XL": 90}
        project_root = tmp_path / "project"

        issue = _done_issue(202)
        _write_state(project_root, "sprint-2", [issue], project="owner/repo")

        db_path = _make_db(tmp_path)  # empty DB, no row for issue 202

        cache = _refresh_calibration_cache(project_root, configured_minutes, db_path=db_path)
        total = sum(cache["by_size"][s]["count"] for s in ("S", "M", "L", "XL"))
        assert total == 0, f"Expected 0 tickets counted, got {total}"
