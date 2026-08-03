"""Tests for issue #2051 — stale docs and missing token-usage debug endpoint.

Acceptance Criteria:
  AC1 — GET /api/debug/token-usage/by-agent-model is exposed and returns a list
         (DB function already existed; endpoint was never wired)
  AC2 — frontend-map.md and 2.3a-frontend-module-boundaries.md reflect as-built
         tab structure: Failures and Brain present; deleted Logs/Metrics/Notes absent
  AC3 — scripts/run_post_sprint.py provides a CLI entry point for the post-sprint
         steps that the manual sprint path skips
  AC4 — CHANGELOG.md and docs/todo.md AUTO:milestones region are backfilled
         for sprint-viz9001 (#2019–#2025) and sprint-viz9002 (#2026–#2033)
  AC5 — docs/decisions/README.md index surfaces the auto-adopted / provisional
         distinction for Q4–Q13 (currently invisible from the index)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC1: /api/debug/token-usage/by-agent-model ────────────────────────────────

@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient for the full dashboard app."""
    import server as srv
    from fastapi.testclient import TestClient
    return TestClient(srv.app, raise_server_exceptions=True)


def test_token_usage_endpoint_returns_200(client):
    """GET /api/debug/token-usage/by-agent-model returns HTTP 200. (AC1)"""
    r = client.get("/api/debug/token-usage/by-agent-model")
    assert r.status_code == 200


def test_token_usage_response_is_list(client):
    """Response body is a JSON list. (AC1)"""
    r = client.get("/api/debug/token-usage/by-agent-model")
    assert isinstance(r.json(), list)


def test_token_usage_returns_json_content_type(client):
    """Response Content-Type is application/json. (AC1)"""
    r = client.get("/api/debug/token-usage/by-agent-model")
    assert "application/json" in r.headers["content-type"]


def test_token_usage_window_start_param_accepted(client):
    """Optional window_start query param is accepted without error. (AC1)"""
    r = client.get("/api/debug/token-usage/by-agent-model?window_start=2026-01-01T00:00:00")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_token_usage_row_shape_when_data_present(client):
    """When rows exist each entry has the expected keys. (AC1)"""
    import db
    # Insert a test row so the response is non-empty
    try:
        with db.get_conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO token_usage
                   (agent_role, model_name, input_tokens, output_tokens, recorded_at)
                   VALUES ('test_role', 'test_model', 10, 5, '2026-01-01T00:00:00')"""
            )
            conn.commit()
    except Exception:
        pytest.skip("Could not insert test token_usage row")

    r = client.get("/api/debug/token-usage/by-agent-model")
    rows = r.json()
    assert isinstance(rows, list)
    # At least one row should exist from our insert
    if rows:
        row = rows[0]
        assert "agent_role" in row
        assert "model_name" in row
        assert "total_input" in row
        assert "total_output" in row
        assert "total_tokens" in row


def test_existing_debug_endpoint_unaffected(client):
    """Existing /api/debug/sprint-collisions still returns 200. (AC1 — no regression)"""
    r = client.get("/api/debug/sprint-collisions")
    assert r.status_code == 200


# ── AC2: frontend-map.md reflects as-built tab structure ─────────────────────

_MAP_PATH = REPO_ROOT / "docs" / "architecture" / "frontend-map.md"
_MOD_PATH = REPO_ROOT / "docs" / "architecture" / "2.3a-frontend-module-boundaries.md"


def test_frontend_map_lists_failures_tab():
    """frontend-map.md sitemap includes the failures tab. (AC2)"""
    content = _MAP_PATH.read_text()
    sitemap_section = content.split("## Page → API Binding")[0]
    assert "failures" in sitemap_section.lower(), (
        "frontend-map.md sitemap must mention the failures tab"
    )


def test_frontend_map_lists_brain_tab():
    """frontend-map.md sitemap includes the brain tab. (AC2)"""
    content = _MAP_PATH.read_text()
    sitemap_section = content.split("## Page → API Binding")[0]
    assert "brain" in sitemap_section.lower(), (
        "frontend-map.md sitemap must mention the brain tab"
    )


def test_frontend_map_no_longer_lists_logs_as_top_tab():
    """frontend-map.md sitemap does not describe logs as a top-level tab. (AC2)"""
    content = _MAP_PATH.read_text()
    sitemap_section = content.split("## Page → API Binding")[0]
    # "- **`logs`**" was the old tab bullet; it must be gone from the sitemap
    assert "- **`logs`**" not in sitemap_section, (
        "frontend-map.md still lists logs as a top-level tab in the sitemap"
    )


def test_frontend_map_no_longer_lists_metrics_as_top_tab():
    """frontend-map.md sitemap does not describe metrics as a top-level tab. (AC2)"""
    content = _MAP_PATH.read_text()
    sitemap_section = content.split("## Page → API Binding")[0]
    assert "- **`metrics`**" not in sitemap_section, (
        "frontend-map.md still lists metrics as a top-level tab in the sitemap"
    )


def test_frontend_map_no_longer_lists_notes_editor():
    """frontend-map.md does not describe the Notes editor modal. (AC2)"""
    content = _MAP_PATH.read_text()
    sitemap_section = content.split("## Page → API Binding")[0]
    assert "notes editor" not in sitemap_section.lower(), (
        "frontend-map.md sitemap still mentions the deleted Notes editor"
    )


def test_module_boundaries_mentions_failures():
    """2.3a-frontend-module-boundaries.md mentions failures. (AC2)"""
    content = _MOD_PATH.read_text()
    assert "failures" in content.lower(), (
        "2.3a must acknowledge the failures tab"
    )


def test_module_boundaries_mentions_brain():
    """2.3a-frontend-module-boundaries.md mentions brain. (AC2)"""
    content = _MOD_PATH.read_text()
    assert "brain" in content.lower(), (
        "2.3a must acknowledge the brain tab"
    )


def test_module_boundaries_logs_not_in_active_concerns():
    """2.3a extraction backlog no longer lists logs as an active inline concern. (AC2)"""
    content = _MOD_PATH.read_text()
    # The old row was: "| Logs & activity views | inline logs tab | src/logs/ |"
    assert "logs & activity views" not in content.lower(), (
        "2.3a still lists 'Logs & activity views' as an active concern — it was deleted"
    )


def test_module_boundaries_metrics_not_in_active_concerns():
    """2.3a extraction backlog no longer lists metrics/analytics as an active inline concern. (AC2)"""
    content = _MOD_PATH.read_text()
    # The old row was: "| Metrics / analytics tab | inline metrics tab | src/metrics/ |"
    assert "metrics / analytics tab" not in content.lower(), (
        "2.3a still lists 'Metrics / analytics tab' as an active concern — it was deleted"
    )


# ── AC3: scripts/run_post_sprint.py CLI entry point ──────────────────────────

_RUN_PS = REPO_ROOT / "scripts" / "run_post_sprint.py"


def test_run_post_sprint_script_exists():
    """scripts/run_post_sprint.py exists. (AC3)"""
    assert _RUN_PS.exists(), "scripts/run_post_sprint.py must exist"


def test_run_post_sprint_has_sprint_label_arg():
    """run_post_sprint.py --help mentions --sprint-label. (AC3)"""
    result = subprocess.run(
        [sys.executable, str(_RUN_PS), "--help"],
        capture_output=True, text=True,
    )
    assert "--sprint-label" in result.stdout, (
        f"run_post_sprint.py --help did not show --sprint-label:\n{result.stdout}"
    )


def test_run_post_sprint_has_repo_arg():
    """run_post_sprint.py --help mentions --repo. (AC3)"""
    result = subprocess.run(
        [sys.executable, str(_RUN_PS), "--help"],
        capture_output=True, text=True,
    )
    assert "--repo" in result.stdout, (
        f"run_post_sprint.py --help did not show --repo:\n{result.stdout}"
    )


def test_run_post_sprint_has_dry_run_arg():
    """run_post_sprint.py --help mentions --dry-run so it is safe to invoke. (AC3)"""
    result = subprocess.run(
        [sys.executable, str(_RUN_PS), "--help"],
        capture_output=True, text=True,
    )
    assert "--dry-run" in result.stdout, (
        f"run_post_sprint.py --help did not show --dry-run:\n{result.stdout}"
    )


def test_run_post_sprint_documents_skipped_steps():
    """run_post_sprint.py source documents which post-sprint steps are skipped by manual path. (AC3)"""
    src = _RUN_PS.read_text()
    # The script must mention record_agent_finish and the documenter so it's clear what it covers
    assert "record_agent_finish" in src or "documenter" in src.lower(), (
        "run_post_sprint.py must mention the post-sprint steps it covers"
    )


# ── AC4: CHANGELOG.md and docs/todo.md backfill ──────────────────────────────

_CHANGELOG = REPO_ROOT / "CHANGELOG.md"
_TODO = REPO_ROOT / "docs" / "todo.md"


def test_changelog_has_sprint_viz9001_entry():
    """CHANGELOG.md contains a sprint-viz9001 section. (AC4)"""
    content = _CHANGELOG.read_text()
    assert "viz9001" in content.lower(), (
        "CHANGELOG.md is missing the sprint-viz9001 backfill entry"
    )


def test_changelog_has_sprint_viz9002_entry():
    """CHANGELOG.md contains a sprint-viz9002 section. (AC4)"""
    content = _CHANGELOG.read_text()
    assert "viz9002" in content.lower(), (
        "CHANGELOG.md is missing the sprint-viz9002 backfill entry"
    )


def test_changelog_viz9001_mentions_failures_ticket():
    """CHANGELOG.md viz9001 section mentions issue #2019 (failures endpoint). (AC4)"""
    content = _CHANGELOG.read_text()
    assert "#2019" in content, (
        "CHANGELOG.md must reference #2019 (Unified failures endpoint) in the viz9001 backfill"
    )


def test_changelog_viz9002_mentions_brain_ticket():
    """CHANGELOG.md viz9002 section mentions issue #2028 (Brain search + tab). (AC4)"""
    content = _CHANGELOG.read_text()
    assert "#2028" in content, (
        "CHANGELOG.md must reference #2028 (Brain search tab) in the viz9002 backfill"
    )


def test_todo_md_milestones_region_has_viz9001():
    """docs/todo.md AUTO:milestones region contains sprint-viz9001 entry. (AC4)"""
    content = _TODO.read_text()
    start = content.index("<!-- AUTO:milestones START -->")
    end = content.index("<!-- AUTO:milestones END -->")
    region = content[start:end]
    assert "viz9001" in region.lower(), (
        "docs/todo.md AUTO:milestones still reads 'No milestones recorded yet' — "
        "backfill for sprint-viz9001 is missing"
    )


def test_todo_md_milestones_region_has_viz9002():
    """docs/todo.md AUTO:milestones region contains sprint-viz9002 entry. (AC4)"""
    content = _TODO.read_text()
    start = content.index("<!-- AUTO:milestones START -->")
    end = content.index("<!-- AUTO:milestones END -->")
    region = content[start:end]
    assert "viz9002" in region.lower(), (
        "docs/todo.md AUTO:milestones is missing the sprint-viz9002 backfill entry"
    )


# ── AC5: docs/decisions/README.md surfaces auto-adopted distinction ───────────

_ADR_README = REPO_ROOT / "docs" / "decisions" / "README.md"


def test_adr_readme_marks_auto_adopted_entries():
    """README.md index includes an auto-adopted or provisional marker. (AC5)"""
    content = _ADR_README.read_text()
    assert "auto-adopted" in content.lower() or "provisional" in content.lower(), (
        "docs/decisions/README.md does not surface the auto-adopted/provisional "
        "distinction for Q4–Q13 ADRs"
    )


def test_adr_readme_q1_not_marked_auto_adopted():
    """Q1 (deliberated) is NOT marked as auto-adopted in the index. (AC5)"""
    content = _ADR_README.read_text()
    # Find the Q1 line in the index
    q1_line = next(
        (ln for ln in content.splitlines()
         if "2026-07-02-1-delete-planned-state" in ln),
        None,
    )
    assert q1_line is not None, "Q1 ADR entry not found in README.md"
    # Q1 was deliberated, so should NOT carry the auto-adopted marker
    assert "auto-adopted" not in q1_line.lower() and "provisional" not in q1_line.lower(), (
        f"Q1 (deliberated) should not carry auto-adopted marker: {q1_line!r}"
    )


def test_adr_readme_q4_marked_auto_adopted():
    """Q4 (auto-adopted) IS marked as such in the index. (AC5)"""
    content = _ADR_README.read_text()
    q4_line = next(
        (ln for ln in content.splitlines()
         if "2026-07-02-4-consolidate-lineage" in ln),
        None,
    )
    assert q4_line is not None, "Q4 ADR entry not found in README.md"
    assert "auto-adopted" in q4_line.lower() or "provisional" in q4_line.lower(), (
        f"Q4 (auto-adopted) should carry the auto-adopted/provisional marker: {q4_line!r}"
    )
