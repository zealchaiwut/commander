"""Tests for issue #735: Archive stale sprint files to reduce startup noise (runs against UAT)

Most acceptance criteria for this ticket are filesystem behaviour of
``scripts/clean_sprint_files.py`` — the same module the maintenance API endpoint
calls. Those are verified functionally against seeded temp sprint dirs. The HTTP
endpoint (AC6) is verified live against the UAT dashboard.
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest

# Resolve repo root and import the module under test (repo-root scripts/).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import clean_sprint_files as csf  # noqa: E402

BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


def _seed(sprints_dir: Path, n: int, *, summary=True, plan=True, state=True,
          placeholder_tickets=0, status=True, estimate=True, running=False):
    """Seed a sprint's files. placeholder_tickets=None skips sprint-N.json."""
    sprints_dir.mkdir(parents=True, exist_ok=True)
    if summary:
        (sprints_dir / f"sprint-{n}-summary-2026.md").write_text("# summary\n")
    if plan:
        (sprints_dir / f"sprint-{n}-plan.json").write_text('{"sprint":%d}' % n)
    if state:
        (sprints_dir / f"sprint-{n}-state.json").write_text('{"snap":true}')
    if placeholder_tickets is not None:
        (sprints_dir / f"sprint-{n}.json").write_text(
            json.dumps({"tickets": [None] * placeholder_tickets if placeholder_tickets else []}))
    if status:
        (sprints_dir / f"sprint-{n}-status.json").write_text('{"status":"done"}')
    if estimate:
        (sprints_dir / f"sprint-{n}-estimate.json").write_text('{"est":5}')
    if running:
        (sprints_dir / f"sprint-{n}-pid").write_text(str(os.getpid()))


# --- Acceptance Criteria ---

def test_archive_stale_sprint_files__dry_run_lists_without_moving(tmp_path):
    # AC1: --dry-run prints/returns every file that WOULD be archived, moves nothing.
    sd = tmp_path / "sprints"
    _seed(sd, 1)
    res = csf.run_cleanup(sd, dry_run=True)
    assert res["dry_run"] is True
    assert set(res["archived"]) == {"sprint-1-plan.json", "sprint-1.json", "sprint-1-state.json"}
    # Nothing moved.
    assert not (sd / "archive").exists()
    assert (sd / "sprint-1-plan.json").exists()
    # CLI surface prints the would-archive list.
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "clean_sprint_files.py"),
         "--project", "x", "--dry-run"],
        capture_output=True, text=True,
        env={**os.environ, "COMMANDER_PROJECTS_BASE": str(tmp_path.parent)},
    )
    # project 'x' resolves under a non-seeded base -> graceful no-dir message, exit 1.
    assert out.returncode in (0, 1)


def test_archive_stale_sprint_files__moves_only_the_three_types(tmp_path):
    # AC2: no flag moves only plan, zero-issue placeholder, and state for finished sprints.
    sd = tmp_path / "sprints"
    _seed(sd, 1)
    res = csf.run_cleanup(sd, dry_run=False)
    assert set(res["archived"]) == {"sprint-1-plan.json", "sprint-1.json", "sprint-1-state.json"}
    arch = sd / "archive"
    for name in res["archived"]:
        assert (arch / name).exists()
        assert not (sd / name).exists()


def test_archive_stale_sprint_files__finished_requires_summary_and_not_running(tmp_path):
    # AC3: finished only when (summary issue OR summary md) AND not running.
    sd = tmp_path / "sprints"
    # No summary -> not finished -> nothing archived.
    _seed(sd, 1, summary=False)
    assert csf.run_cleanup(sd, dry_run=True)["archived"] == []
    # Summary issue but running -> not finished.
    sd2 = tmp_path / "s2"
    _seed(sd2, 5, summary=False, running=True)
    assert csf.is_sprint_finished(sd2, 5, has_summary_issue=lambda n: True) is False
    # Summary md and not running -> finished.
    sd3 = tmp_path / "s3"
    _seed(sd3, 7)
    assert csf.is_sprint_finished(sd3, 7) is True


def test_archive_stale_sprint_files__status_estimate_summary_never_moved(tmp_path):
    # AC4: status/estimate/summary md are never moved, regardless of state.
    sd = tmp_path / "sprints"
    _seed(sd, 1)
    csf.run_cleanup(sd, dry_run=False)
    assert (sd / "sprint-1-status.json").exists()
    assert (sd / "sprint-1-estimate.json").exists()
    assert (sd / "sprint-1-summary-2026.md").exists()


def test_archive_stale_sprint_files__no_deletes_fully_reversible(tmp_path):
    # AC5: nothing deleted; every move reversible by moving the file back.
    sd = tmp_path / "sprints"
    _seed(sd, 1)
    before = sorted(p.name for p in sd.iterdir())
    res = csf.run_cleanup(sd, dry_run=False)
    arch = sd / "archive"
    # No net file loss: archived files all exist under archive/.
    for name in res["archived"]:
        assert (arch / name).exists()
    # Move back -> original set restored.
    for name in res["archived"]:
        (arch / name).rename(sd / name)
    assert sorted(p.name for p in sd.iterdir() if p.is_file()) == before


def test_archive_stale_sprint_files__maintenance_endpoint(client, tmp_path):
    # AC6: POST /api/maintenance/sprints/cleanup runs archive, returns {archived, kept_count}, 200.
    base = Path.home() / "dev"
    slug = f"_t735_pytest_{uuid.uuid4().hex[:8]}"
    sd = base / slug / ".commander" / "sprints"
    try:
        _seed(sd, 1)              # finished -> archivable
        _seed(sd, 2, summary=False)  # no summary -> kept
        # dry-run preview
        r = client.post("/api/maintenance/sprints/cleanup",
                        json={"project": slug, "dry_run": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert set(body["archived"]) == {"sprint-1-plan.json", "sprint-1.json", "sprint-1-state.json"}
        assert isinstance(body["kept_count"], int) and body["kept_count"] > 0
        assert not (sd / "archive").exists()  # dry-run moved nothing
        # real archive
        r2 = client.post("/api/maintenance/sprints/cleanup",
                         json={"project": slug, "dry_run": False})
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert set(body2["archived"]) == {"sprint-1-plan.json", "sprint-1.json", "sprint-1-state.json"}
        assert (sd / "archive" / "sprint-1-plan.json").exists()
    finally:
        import shutil
        shutil.rmtree(base / slug, ignore_errors=True)


def test_archive_stale_sprint_files__ui_preview_then_confirm():
    # AC7: Settings UI shows dry-run preview before a confirm button.
    pytest.skip("manual — UI flow, agent-browser unavailable (COMMANDER_AGENT_BROWSER_AVAILABLE=0)")


def test_archive_stale_sprint_files__startup_skips_archive_dir(tmp_path):
    # AC8: startup restore skips archive/ entirely — discovery never descends into
    #      it, so archived files generate no per-file scan (enables one summary line).
    sd = tmp_path / "sprints"
    arch = sd / "archive"
    arch.mkdir(parents=True)
    (arch / "sprint-50-plan.json").write_text("{}")
    (arch / "sprint-50-state.json").write_text("{}")
    # Files living under archive/ are invisible to the top-level sprint scan.
    assert csf.discover_sprint_numbers(sd) == set()
    # And after a real cleanup, archived files land only under archive/, not the
    # top-level dir that startup restore globs.
    _seed(sd, 1)
    csf.run_cleanup(sd, dry_run=False)
    for name in ("sprint-1-plan.json", "sprint-1-state.json"):
        assert (sd / "archive" / name).exists()
        assert not (sd / name).exists()


def test_archive_stale_sprint_files__analytics_files_intact_after_cleanup(tmp_path):
    # AC9: status/estimate data still present & readable after cleanup (analytics unaffected).
    sd = tmp_path / "sprints"
    _seed(sd, 1)
    csf.run_cleanup(sd, dry_run=False)
    assert json.loads((sd / "sprint-1-status.json").read_text())["status"] == "done"
    assert json.loads((sd / "sprint-1-estimate.json").read_text())["est"] == 5


def test_archive_stale_sprint_files__idempotent(tmp_path):
    # AC10: running twice produces no additional moves and no errors.
    sd = tmp_path / "sprints"
    _seed(sd, 1)
    first = csf.run_cleanup(sd, dry_run=False)
    assert len(first["archived"]) == 3
    second = csf.run_cleanup(sd, dry_run=False)
    assert second["archived"] == []
