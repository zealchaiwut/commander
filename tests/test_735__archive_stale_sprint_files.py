"""Tests for issue #735 — Archive stale sprint files to reduce startup noise.

Each test is anchored to a specific acceptance criterion (AC) from the issue.

AC coverage:
  AC1  — `--dry-run` prints every file that *would* be archived without moving anything.
  AC2  — no flag moves only sprint-N-plan.json, sprint-N.json (zero-issue placeholder),
         and sprint-N-state.json for finished sprints into .commander/sprints/archive/.
  AC3  — a sprint is finished only when it has a summary issue OR summary markdown
         AND no live process is running it.
  AC4  — sprint-N-status.json, sprint-N-estimate.json, and summary markdown are never moved.
  AC5  — no files are deleted; archive is reversible (moved files exist in archive/).
  AC6  — POST /api/maintenance/sprints/cleanup returns {archived: [...], kept_count: N}.
  AC7  — Settings UI shows a dry-run preview + confirmation button (HTML structure).
  AC8  — startup restore skips archive/ and emits a single summary log line.
  AC9  — status/estimate/summary data still readable after cleanup (analytics unaffected).
  AC10 — cleanup is idempotent (running twice produces no additional moves or errors).
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPTS_DIR = REPO_ROOT / "scripts"
STATIC_DIR = DASHBOARD_DIR / "static"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(DASHBOARD_DIR))

import clean_sprint_files as csf  # noqa: E402


# ── Seeding helpers ───────────────────────────────────────────────────────────

def _seed_sprint(sprints_dir, n, *, finished=True, placeholder_tickets=0,
                 running=False, with_status=True, with_estimate=True):
    """Create the per-sprint files for sprint N.

    finished=True writes a summary markdown file (one of the two finish signals).
    running=True writes a live PID file (current process PID) so the sprint counts
    as still running.
    """
    sprints_dir.mkdir(parents=True, exist_ok=True)
    (sprints_dir / f"sprint-{n}-plan.json").write_text(
        json.dumps({"state": "completed", "tickets": []}), encoding="utf-8")
    (sprints_dir / f"sprint-{n}-state.json").write_text(
        json.dumps({"sprint": n}), encoding="utf-8")
    # placeholder sprint-N.json with the requested number of tickets
    tickets = [{"issue_number": 100 + i} for i in range(placeholder_tickets)]
    (sprints_dir / f"sprint-{n}.json").write_text(
        json.dumps({"label": f"sprint-{n}", "tickets": tickets}), encoding="utf-8")
    if with_status:
        (sprints_dir / f"sprint-{n}-status.json").write_text(
            json.dumps({"phase": "done"}), encoding="utf-8")
    if with_estimate:
        (sprints_dir / f"sprint-{n}-estimate.json").write_text(
            json.dumps({"estimates": []}), encoding="utf-8")
    if finished:
        (sprints_dir / f"sprint-{n}-summary-mon.md").write_text(
            f"# Sprint {n} Executive Summary\n", encoding="utf-8")
    if running:
        import os
        (sprints_dir / f"sprint-{n}-pid").write_text(str(os.getpid()), encoding="utf-8")


@pytest.fixture
def sprints_dir(tmp_path):
    d = tmp_path / ".commander" / "sprints"
    d.mkdir(parents=True)
    return d


# ── AC3: finished-sprint detection ────────────────────────────────────────────

def test_finished_requires_summary_and_not_running(sprints_dir):
    """AC3: finished == (summary md or summary issue) AND not running."""
    _seed_sprint(sprints_dir, 1, finished=True, running=False)
    assert csf.is_sprint_finished(sprints_dir, 1, has_summary_issue=lambda n: False)


def test_not_finished_when_no_summary(sprints_dir):
    """AC3: a sprint with no summary md and no summary issue is NOT finished."""
    _seed_sprint(sprints_dir, 2, finished=False, running=False)
    assert not csf.is_sprint_finished(sprints_dir, 2, has_summary_issue=lambda n: False)


def test_not_finished_when_running(sprints_dir):
    """AC3: a sprint with a live PID is NOT finished even if it has a summary."""
    _seed_sprint(sprints_dir, 3, finished=True, running=True)
    assert not csf.is_sprint_finished(sprints_dir, 3, has_summary_issue=lambda n: False)


def test_finished_via_summary_issue_only(sprints_dir):
    """AC3: a posted summary issue (no markdown) also marks a sprint finished."""
    _seed_sprint(sprints_dir, 4, finished=False, running=False)
    assert csf.is_sprint_finished(sprints_dir, 4, has_summary_issue=lambda n: n == 4)


# ── AC1: dry-run lists, moves nothing ─────────────────────────────────────────

def test_dry_run_lists_without_moving(sprints_dir):
    """AC1: --dry-run reports plan/placeholder/state but leaves files in place."""
    _seed_sprint(sprints_dir, 5, finished=True, placeholder_tickets=0)
    result = csf.run_cleanup(sprints_dir, dry_run=True,
                             has_summary_issue=lambda n: False)
    names = set(result["archived"])
    assert names == {"sprint-5-plan.json", "sprint-5.json", "sprint-5-state.json"}
    # Nothing moved.
    assert (sprints_dir / "sprint-5-plan.json").exists()
    assert (sprints_dir / "sprint-5-state.json").exists()
    assert (sprints_dir / "sprint-5.json").exists()
    assert not (sprints_dir / csf.ARCHIVE_DIRNAME).exists()


# ── AC2: real run moves only the three target files for finished sprints ───────

def test_real_run_moves_only_targets(sprints_dir):
    """AC2: plan, zero-issue placeholder, and state move; nothing else does."""
    _seed_sprint(sprints_dir, 6, finished=True, placeholder_tickets=0)
    result = csf.run_cleanup(sprints_dir, dry_run=False,
                             has_summary_issue=lambda n: False)
    archive = sprints_dir / csf.ARCHIVE_DIRNAME
    assert set(result["archived"]) == {
        "sprint-6-plan.json", "sprint-6.json", "sprint-6-state.json"}
    for name in result["archived"]:
        assert (archive / name).exists()
        assert not (sprints_dir / name).exists()


def test_in_progress_sprint_not_touched(sprints_dir):
    """AC2/AC3: an unfinished (running, no summary) sprint keeps all its files."""
    _seed_sprint(sprints_dir, 7, finished=False, running=True, placeholder_tickets=0)
    csf.run_cleanup(sprints_dir, dry_run=False, has_summary_issue=lambda n: False)
    assert (sprints_dir / "sprint-7-plan.json").exists()
    assert (sprints_dir / "sprint-7-state.json").exists()
    assert (sprints_dir / "sprint-7.json").exists()


def test_nonempty_placeholder_not_moved(sprints_dir):
    """AC2: sprint-N.json is moved ONLY when it is a zero-issue placeholder."""
    _seed_sprint(sprints_dir, 8, finished=True, placeholder_tickets=3)
    result = csf.run_cleanup(sprints_dir, dry_run=False,
                             has_summary_issue=lambda n: False)
    assert "sprint-8.json" not in result["archived"]
    assert (sprints_dir / "sprint-8.json").exists()  # kept in place
    # plan + state still archived
    assert "sprint-8-plan.json" in result["archived"]
    assert "sprint-8-state.json" in result["archived"]


# ── AC4: status / estimate / summary never moved ──────────────────────────────

def test_status_estimate_summary_never_moved(sprints_dir):
    """AC4: status, estimate, and summary markdown stay regardless of state."""
    _seed_sprint(sprints_dir, 9, finished=True, placeholder_tickets=0)
    csf.run_cleanup(sprints_dir, dry_run=False, has_summary_issue=lambda n: False)
    assert (sprints_dir / "sprint-9-status.json").exists()
    assert (sprints_dir / "sprint-9-estimate.json").exists()
    assert (sprints_dir / "sprint-9-summary-mon.md").exists()
    archive = sprints_dir / csf.ARCHIVE_DIRNAME
    assert not (archive / "sprint-9-status.json").exists()
    assert not (archive / "sprint-9-estimate.json").exists()
    assert not (archive / "sprint-9-summary-mon.md").exists()


# ── AC5: nothing deleted, reversible ──────────────────────────────────────────

def test_no_deletion_archive_is_reversible(sprints_dir):
    """AC5: archived files still exist (in archive/) — moved, never deleted."""
    _seed_sprint(sprints_dir, 10, finished=True, placeholder_tickets=0)
    csf.run_cleanup(sprints_dir, dry_run=False, has_summary_issue=lambda n: False)
    archive = sprints_dir / csf.ARCHIVE_DIRNAME
    archived_files = list(archive.glob("*.json"))
    assert len(archived_files) == 3
    # Move one back; it lands in the original directory intact.
    src = archive / "sprint-10-state.json"
    dst = sprints_dir / "sprint-10-state.json"
    src.rename(dst)
    assert dst.exists()
    assert json.loads(dst.read_text())["sprint"] == 10


# ── AC9: analytics-relevant data unchanged after cleanup ──────────────────────

def test_status_and_estimate_readable_after_cleanup(sprints_dir):
    """AC9: status and estimate payloads are unchanged and still parseable."""
    _seed_sprint(sprints_dir, 11, finished=True, placeholder_tickets=0)
    before_status = (sprints_dir / "sprint-11-status.json").read_text()
    before_est = (sprints_dir / "sprint-11-estimate.json").read_text()
    csf.run_cleanup(sprints_dir, dry_run=False, has_summary_issue=lambda n: False)
    assert (sprints_dir / "sprint-11-status.json").read_text() == before_status
    assert (sprints_dir / "sprint-11-estimate.json").read_text() == before_est


# ── AC10: idempotency ─────────────────────────────────────────────────────────

def test_cleanup_idempotent(sprints_dir):
    """AC10: a second run archives nothing and raises no error."""
    _seed_sprint(sprints_dir, 12, finished=True, placeholder_tickets=0)
    first = csf.run_cleanup(sprints_dir, dry_run=False,
                            has_summary_issue=lambda n: False)
    assert len(first["archived"]) == 3
    second = csf.run_cleanup(sprints_dir, dry_run=False,
                             has_summary_issue=lambda n: False)
    assert second["archived"] == []
    assert second["kept_count"] >= 1


def test_cleanup_tolerates_existing_archive_copy(sprints_dir):
    """A live file whose archive name already exists must not raise (partial rerun)."""
    _seed_sprint(sprints_dir, 14, finished=True, placeholder_tickets=0)
    archive = sprints_dir / csf.ARCHIVE_DIRNAME
    archive.mkdir(parents=True, exist_ok=True)
    live = sprints_dir / "sprint-14-plan.json"
    shutil.copy2(live, archive / live.name)
    result = csf.run_cleanup(sprints_dir, dry_run=False,
                             has_summary_issue=lambda n: False)
    assert "sprint-14-plan.json" in result["archived"]
    assert not live.exists()
    assert (archive / "sprint-14-plan.json").exists()


def test_kept_count_nonzero(sprints_dir):
    """AC6: kept_count reflects files left behind (status/estimate/summary)."""
    _seed_sprint(sprints_dir, 13, finished=True, placeholder_tickets=0)
    result = csf.run_cleanup(sprints_dir, dry_run=False,
                             has_summary_issue=lambda n: False)
    assert result["kept_count"] > 0


# ── AC6: API endpoint ─────────────────────────────────────────────────────────

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """TestClient with project root pointed at tmp_path and GitHub summary stubbed."""
    sys.modules.pop("server", None)
    import server as srv
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    project_root = tmp_path / "proj"
    (project_root / ".commander" / "sprints").mkdir(parents=True)

    monkeypatch.setattr(srv, "_project_root_path", lambda repo: project_root)
    monkeypatch.setattr(srv, "_finished_sprint_summaries", lambda repo: {})

    with patch.object(srv.projects_module, "load_projects",
                      return_value=[{"repo": "owner/proj", "slug": "proj"}]):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv, project_root


def test_endpoint_returns_archived_and_kept_count(api_client):
    """AC6: POST /api/maintenance/sprints/cleanup returns archived + kept_count."""
    client, srv, project_root = api_client
    sprints_dir = project_root / ".commander" / "sprints"
    _seed_sprint(sprints_dir, 20, finished=True, placeholder_tickets=0)

    resp = client.post("/api/maintenance/sprints/cleanup", json={"project": "owner/proj"})
    assert resp.status_code == 200
    data = resp.json()
    assert "archived" in data and isinstance(data["archived"], list)
    assert set(data["archived"]) == {
        "sprint-20-plan.json", "sprint-20.json", "sprint-20-state.json"}
    assert data["kept_count"] > 0
    archive = sprints_dir / csf.ARCHIVE_DIRNAME
    assert (archive / "sprint-20-plan.json").exists()


def test_endpoint_dry_run_moves_nothing(api_client):
    """AC7 (backend): dry_run preview lists files without moving them."""
    client, srv, project_root = api_client
    sprints_dir = project_root / ".commander" / "sprints"
    _seed_sprint(sprints_dir, 21, finished=True, placeholder_tickets=0)

    resp = client.post("/api/maintenance/sprints/cleanup",
                       json={"project": "owner/proj", "dry_run": True})
    assert resp.status_code == 200
    data = resp.json()
    assert set(data["archived"]) == {
        "sprint-21-plan.json", "sprint-21.json", "sprint-21-state.json"}
    # Still in place.
    assert (sprints_dir / "sprint-21-plan.json").exists()
    assert not (sprints_dir / csf.ARCHIVE_DIRNAME).exists()


# ── AC8: startup emits a single summary log line ──────────────────────────────

def test_startup_emits_single_archive_summary_line(tmp_path, monkeypatch, capsys):
    """AC8: startup restore skips archive/ and prints one 'Skipped N archived' line."""
    sys.modules.pop("server", None)
    import server as srv
    from unittest.mock import patch

    project_root = tmp_path / "proj"
    archive = project_root / ".commander" / "sprints" / csf.ARCHIVE_DIRNAME
    archive.mkdir(parents=True)
    for i in range(3):
        (archive / f"sprint-{i}-plan.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(srv, "_project_root_path", lambda repo: project_root)

    with patch.object(srv.projects_module, "load_projects",
                      return_value=[{"repo": "owner/proj"}]):
        srv._restore_sprint_statuses_on_startup()

    out = capsys.readouterr().out
    summary_lines = [ln for ln in out.splitlines()
                     if "archived sprint file" in ln]
    assert len(summary_lines) == 1, f"expected one summary line, got: {summary_lines}"
    assert "3" in summary_lines[0]
    # No per-file line for any archived file.
    assert "sprint-0-plan.json" not in out
    assert "sprint-1-plan.json" not in out


# ── AC7: Settings UI structure ────────────────────────────────────────────────

def test_ui_has_cleanup_card_and_preview_and_confirm():
    """AC7: project.html exposes a maintenance cleanup card with preview + confirm."""
    html = (STATIC_DIR / "project.html").read_text()
    assert 'id="ps-sprint-cleanup-card"' in html, "cleanup card missing"
    assert 'id="ps-stale-scan-btn"' in html, "stale branch scan button missing from settings"
    assert 'id="ps-cleanup-preview"' in html, "preview panel missing"
    # A preview (dry-run) trigger and a confirm trigger must both exist.
    assert "sprintCleanupPreview(" in html, "preview button handler missing"
    assert "sprintCleanupConfirm(" in html, "confirm button handler missing"


def test_ui_confirm_calls_cleanup_endpoint():
    """AC7: the confirm handler posts to the cleanup endpoint."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "/api/maintenance/sprints/cleanup" in html
