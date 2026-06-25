"""Tests for issue #1364 — Thread db_path into rebuild_calibration_cache.

AC coverage:
  AC1 — rebuild_calibration_cache() resolves db_path from DB_PATH env var
  AC2 — Both _calibration_absorb_state_file calls receive db_path kwarg
  AC3 — Ticket with size only in SQLite issues mirror counted by explicit rebuild
  AC4 — Explicit rebuild result matches auto-refresh result for same DB
  AC5 — When DB_PATH is unset/empty, rebuild behaves as before (no crash)
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS_ROOT = _DASHBOARD_ROOT / "routers"

for _p in (str(_DASHBOARD_ROOT), str(_ROUTERS_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_MINUTES = {"S": 5, "M": 15, "L": 30, "XL": 90}


def _make_db(tmp_path: Path, rows: list[tuple] | None = None) -> Path:
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


def _write_sprint_state(
    project_root: Path,
    sprint_label: str,
    issues: list[dict],
    *,
    archive: bool = False,
    project: str = "owner/repo",
) -> None:
    import re
    n = re.search(r"(\d+)", sprint_label).group(1)
    sprints_dir = project_root / ".commander" / "sprints"
    if archive:
        sprints_dir = sprints_dir / "archive"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": sprint_label,
        "sprint_number": int(n),
        "project": project,
        "issues": issues,
    }
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )


def _done_issue_no_labels(num: int, coder_min: float = 10.0, tester_min: float = 5.0) -> dict:
    """Done issue dict with NO labels key — simulates state files without label data."""
    return {
        "number": num,
        "status": "done",
        "coder_started_at": "2026-06-01T10:00:00Z",
        "coder_finished_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_started_at": f"2026-06-01T10:{int(coder_min):02d}:00Z",
        "tester_finished_at": f"2026-06-01T10:{int(coder_min + tester_min):02d}:00Z",
    }


# ---------------------------------------------------------------------------
# AC1 — rebuild_calibration_cache() resolves db_path from DB_PATH env var
# ---------------------------------------------------------------------------

class TestAC1DbPathResolvedFromEnv:
    """AC1: rebuild_calibration_cache reads DB_PATH env var and passes db_path down."""

    def test_db_path_env_var_enables_db_fallback(self, tmp_path, monkeypatch):
        """When DB_PATH is set, a ticket sized only in SQLite is counted by rebuild."""
        from maintenance_service import rebuild_calibration_cache

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(101)
        _write_sprint_state(project_root, "sprint-1", [issue], project="owner/repo")

        db_path = _make_db(tmp_path, [("owner/repo", 101, [{"name": "size-M"}])])
        monkeypatch.setenv("DB_PATH", str(db_path))

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["by_size"]["M"] == 1, (
            f"Expected M=1 via DB_PATH env var, got {result['by_size']}. "
            "rebuild_calibration_cache must resolve db_path from DB_PATH env var."
        )

    def test_db_path_env_var_used_for_archive_state_files(self, tmp_path, monkeypatch):
        """DB_PATH fallback applies to tickets in archive/ state files too."""
        from maintenance_service import rebuild_calibration_cache

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(202)
        _write_sprint_state(
            project_root, "sprint-1", [issue], archive=True, project="owner/repo"
        )

        db_path = _make_db(tmp_path, [("owner/repo", 202, [{"name": "size-L"}])])
        monkeypatch.setenv("DB_PATH", str(db_path))

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["by_size"]["L"] == 1, (
            f"Expected L=1 from archive state file via DB fallback, got {result['by_size']}. "
            "DB_PATH must be threaded into the archive absorb call."
        )


# ---------------------------------------------------------------------------
# AC2 — Both _calibration_absorb_state_file calls receive db_path kwarg
# ---------------------------------------------------------------------------

class TestAC2BothCallSitesReceiveDbPath:
    """AC2: both absorb call sites (archive and live) pass db_path."""

    def test_both_call_sites_counted_via_db(self, tmp_path, monkeypatch):
        """Archive and live state files both use DB fallback when DB_PATH is set."""
        from maintenance_service import rebuild_calibration_cache

        project_root = tmp_path / "project"

        # Ticket in archive sprint
        archive_issue = _done_issue_no_labels(301)
        _write_sprint_state(
            project_root, "sprint-1", [archive_issue], archive=True, project="owner/repo"
        )

        # Ticket in live sprint
        live_issue = _done_issue_no_labels(302)
        _write_sprint_state(
            project_root, "sprint-2", [live_issue], project="owner/repo"
        )

        db_path = _make_db(tmp_path, [
            ("owner/repo", 301, [{"name": "size-S"}]),
            ("owner/repo", 302, [{"name": "size-M"}]),
        ])
        monkeypatch.setenv("DB_PATH", str(db_path))

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["by_size"]["S"] == 1, (
            f"Archive ticket 301 not counted via DB. by_size={result['by_size']}"
        )
        assert result["by_size"]["M"] == 1, (
            f"Live ticket 302 not counted via DB. by_size={result['by_size']}"
        )
        assert result["total"] == 2, (
            f"Expected total=2 (1 archive + 1 live), got total={result['total']}"
        )


# ---------------------------------------------------------------------------
# AC3 — Ticket sized only in SQLite is counted by explicit rebuild
# ---------------------------------------------------------------------------

class TestAC3DbOnlyTicketCountedByRebuild:
    """AC3: ticket with size only in SQLite issues mirror appears in rebuild output."""

    def test_db_only_ticket_counted(self, tmp_path, monkeypatch):
        """No estimate file, no state.estimates, no state labels — size from SQLite only."""
        from maintenance_service import rebuild_calibration_cache

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(401)
        # No estimate file written, no state.estimates, no labels in issue dict
        _write_sprint_state(project_root, "sprint-1", [issue], project="owner/repo")

        db_path = _make_db(tmp_path, [("owner/repo", 401, [{"name": "size-XL"}])])
        monkeypatch.setenv("DB_PATH", str(db_path))

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["by_size"]["XL"] == 1, (
            f"Expected XL=1 for ticket sized only in SQLite, got {result['by_size']}. "
            "Explicit rebuild must include tickets whose size is only in the DB mirror."
        )
        assert result["total"] == 1

    def test_ticket_without_db_row_not_counted(self, tmp_path, monkeypatch):
        """Ticket with no size anywhere (not in DB either) is correctly excluded."""
        from maintenance_service import rebuild_calibration_cache

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(999)
        _write_sprint_state(project_root, "sprint-1", [issue], project="owner/repo")

        db_path = _make_db(tmp_path)  # empty DB, no row for issue 999
        monkeypatch.setenv("DB_PATH", str(db_path))

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# AC4 — Explicit rebuild result matches auto-refresh for same DB
# ---------------------------------------------------------------------------

class TestAC4RebuildMatchesAutoRefresh:
    """AC4: rebuild and auto-refresh produce identical counts for the same data."""

    def test_rebuild_matches_refresh_single_ticket(self, tmp_path, monkeypatch):
        """rebuild_calibration_cache and _refresh_calibration_cache agree on one ticket."""
        from maintenance_service import rebuild_calibration_cache
        from calibration_cache_service import _refresh_calibration_cache

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(501)
        _write_sprint_state(project_root, "sprint-1", [issue], project="owner/repo")

        db_path = _make_db(tmp_path, [("owner/repo", 501, [{"name": "size-S"}])])
        monkeypatch.setenv("DB_PATH", str(db_path))

        rebuild_result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES, dry_run=True)
        refresh_cache = _refresh_calibration_cache(project_root, _DEFAULT_MINUTES, db_path=db_path)

        refresh_result = {
            "total": sum(
                int(refresh_cache["by_size"].get(sz, {}).get("count", 0) or 0)
                for sz in ("S", "M", "L", "XL")
            ),
            "by_size": {
                sz: int(refresh_cache["by_size"].get(sz, {}).get("count", 0) or 0)
                for sz in ("S", "M", "L", "XL")
            },
        }
        assert rebuild_result == refresh_result, (
            f"Rebuild and auto-refresh results differ.\n"
            f"  rebuild:  {rebuild_result}\n"
            f"  refresh:  {refresh_result}"
        )

    def test_rebuild_matches_refresh_multiple_sizes(self, tmp_path, monkeypatch):
        """rebuild and refresh agree across S/M/L/XL when DB_PATH is set."""
        from maintenance_service import rebuild_calibration_cache
        from calibration_cache_service import _refresh_calibration_cache

        project_root = tmp_path / "project"
        issues = [
            _done_issue_no_labels(601),
            _done_issue_no_labels(602),
            _done_issue_no_labels(603),
        ]
        _write_sprint_state(project_root, "sprint-1", issues, project="myorg/myrepo")

        db_rows = [
            ("myorg/myrepo", 601, [{"name": "size-S"}]),
            ("myorg/myrepo", 602, [{"name": "size-M"}]),
            ("myorg/myrepo", 603, [{"name": "size-L"}]),
        ]
        db_path = _make_db(tmp_path, db_rows)
        monkeypatch.setenv("DB_PATH", str(db_path))

        rebuild_result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES, dry_run=True)
        refresh_cache = _refresh_calibration_cache(project_root, _DEFAULT_MINUTES, db_path=db_path)

        refresh_result = {
            "total": sum(
                int(refresh_cache["by_size"].get(sz, {}).get("count", 0) or 0)
                for sz in ("S", "M", "L", "XL")
            ),
            "by_size": {
                sz: int(refresh_cache["by_size"].get(sz, {}).get("count", 0) or 0)
                for sz in ("S", "M", "L", "XL")
            },
        }
        assert rebuild_result == refresh_result, (
            f"Rebuild and auto-refresh disagree.\n"
            f"  rebuild:  {rebuild_result}\n"
            f"  refresh:  {refresh_result}"
        )


# ---------------------------------------------------------------------------
# AC5 — When DB_PATH is unset/empty, rebuild behaves as before (no crash)
# ---------------------------------------------------------------------------

class TestAC5NoCrashWithoutDbPath:
    """AC5: DB_PATH unset or empty — rebuild works, no crash, no DB fallback."""

    def test_no_crash_when_db_path_unset(self, tmp_path, monkeypatch):
        """DB_PATH not set → rebuild completes without error, returns count summary."""
        from maintenance_service import rebuild_calibration_cache

        monkeypatch.delenv("DB_PATH", raising=False)

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(701)
        issue["labels"] = [{"name": "size-S"}]  # size available from state label
        _write_sprint_state(project_root, "sprint-1", [issue])

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert isinstance(result, dict)
        assert "total" in result
        assert "by_size" in result

    def test_no_crash_when_db_path_empty_string(self, tmp_path, monkeypatch):
        """DB_PATH='' → rebuild completes without error (treats as unset)."""
        from maintenance_service import rebuild_calibration_cache

        monkeypatch.setenv("DB_PATH", "")

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(801)
        issue["labels"] = [{"name": "size-M"}]
        _write_sprint_state(project_root, "sprint-1", [issue])

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert isinstance(result, dict)
        assert result["total"] >= 0

    def test_no_crash_when_no_sprints_dir(self, tmp_path, monkeypatch):
        """Empty project root with no sprints dir → rebuild returns zeros without crash."""
        from maintenance_service import rebuild_calibration_cache

        monkeypatch.delenv("DB_PATH", raising=False)
        project_root = tmp_path / "empty_project"
        project_root.mkdir()

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["total"] == 0

    def test_db_fallback_not_used_when_db_path_unset(self, tmp_path, monkeypatch):
        """With DB_PATH unset, ticket sized only in DB is NOT counted (fallback skipped)."""
        from maintenance_service import rebuild_calibration_cache

        monkeypatch.delenv("DB_PATH", raising=False)

        project_root = tmp_path / "project"
        issue = _done_issue_no_labels(901)  # no labels, no size anywhere accessible
        _write_sprint_state(project_root, "sprint-1", [issue], project="owner/repo")

        # DB has a row but DB_PATH is not set — rebuild must not use it
        _make_db(tmp_path, [("owner/repo", 901, [{"name": "size-M"}])])

        result = rebuild_calibration_cache(project_root, _DEFAULT_MINUTES)
        assert result["total"] == 0, (
            f"Expected 0 when DB_PATH is unset (DB fallback must be skipped), got {result['total']}"
        )
