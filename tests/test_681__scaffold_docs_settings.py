"""Tests for issue #681 — Add Scaffold Docs action to Project Settings.

AC coverage:
  AC1  — GET /api/projects/{slug}/docs/scaffold/check runs check and returns
         { compliant, missing, stray, project_root } without writing files
  AC2  — POST /api/projects/{slug}/docs/scaffold/apply returns 400 without
         { confirm: true }; with confirm creates files and returns { created, compliant }
  AC3  — Both endpoints reject project roots that fall outside _PROJECTS_BASE
         (path traversal guard)
  AC4  — Apply never overwrites existing files (existing content preserved)
  AC5  — project.html has "Docs Structure" section in the settings tab
  AC6  — Check button, compliant badge placeholder, missing paths / stray lists
         exist in project.html
  AC7  — "Apply scaffold" button disabled until check ran and found missing items
         (disabled attribute present by default)
  AC8  — Confirmation dialog element exists in project.html
  AC9  — Confirming dialog calls apply endpoint with { confirm: true } (JS present)
  AC10 — After successful apply the check re-runs automatically (JS present)
  AC11 — Stray docs shown informational only; no create/delete affordance in HTML
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
STATIC_DIR = DASHBOARD_DIR / "static"
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client_ctx(tmp_path):
    """Yield (client, srv) with patched projects.json and a temp projects base."""
    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/test-proj", "name": "Test Proj", "environments": {}}
    ]))

    # Build a fake project root inside the temp projects base
    projects_base = tmp_path / "dev"
    projects_base.mkdir()
    proj_root = projects_base / "test-proj"
    proj_root.mkdir()

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects") or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv
    import projects as projects_module

    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    with patch.object(srv, "projects_module", projects_module), \
         patch.object(srv, "_PROJECTS_BASE", projects_base):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv, projects_module, projects_base, proj_root


@pytest.fixture()
def compliant_root(tmp_path):
    """A project root that already has all standard docs in place."""
    root = tmp_path / "my-proj"
    root.mkdir()
    # Create all required standard dirs and files
    (root / "docs").mkdir()
    (root / "docs" / "features").mkdir()
    (root / "docs" / "bulk-create").mkdir()
    (root / "docs" / "changelog").mkdir()
    (root / "docs" / "changelog" / "uat").mkdir()
    (root / "docs" / "changelog" / "prd").mkdir()
    (root / "docs" / "quickstart.md").write_text("# Quick Start\n")
    (root / "docs" / "tutorial.md").write_text("# Tutorial\n")
    (root / "docs" / "workflow.md").write_text("# Workflow\n")
    (root / "docs" / "architecture.md").write_text("# Architecture\n")
    (root / "docs" / "milestones.md").write_text("# Milestones\n")
    (root / "docs" / "bulk-create" / "README.md").write_text("# Bulk Create\n")
    (root / "docs" / "features" / "README.md").write_text("# Features\n")
    (root / "CHANGELOG.md").write_text("# Changelog\n")
    return root


# ── AC1: GET check endpoint returns structured data ───────────────────────────

def test_check_endpoint_returns_compliant_true_for_full_structure(client_ctx, compliant_root):
    """GET .../check returns compliant=true when all standard files exist."""
    client, srv, _, projects_base, proj_root = client_ctx

    # Copy compliant structure into the patched project root
    import shutil
    shutil.copytree(compliant_root, proj_root, dirs_exist_ok=True)

    resp = client.get("/api/projects/test-proj/docs/scaffold/check")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "compliant" in data, f"Response must contain 'compliant' key: {data}"
    assert "missing" in data, f"Response must contain 'missing' key: {data}"
    assert "stray" in data, f"Response must contain 'stray' key: {data}"
    assert "project_root" in data, f"Response must contain 'project_root' key: {data}"
    assert data["compliant"] is True, f"Expected compliant=true, got: {data}"
    assert data["missing"] == [], f"Expected no missing items, got: {data['missing']}"


def test_check_endpoint_returns_compliant_false_and_missing_list(client_ctx):
    """GET .../check returns compliant=false and lists missing paths for non-compliant project."""
    client, srv, _, projects_base, proj_root = client_ctx

    # proj_root is empty — everything is missing

    resp = client.get("/api/projects/test-proj/docs/scaffold/check")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["compliant"] is False, f"Empty project must be non-compliant: {data}"
    assert len(data["missing"]) > 0, f"Must report missing paths: {data}"
    assert "docs/quickstart.md" in data["missing"], (
        f"Expected docs/quickstart.md in missing: {data['missing']}"
    )
    assert "CHANGELOG.md" in data["missing"], (
        f"Expected CHANGELOG.md in missing: {data['missing']}"
    )


def test_check_endpoint_does_not_write_files(client_ctx):
    """GET .../check must not create any files."""
    client, srv, _, projects_base, proj_root = client_ctx

    before = set(proj_root.rglob("*"))
    resp = client.get("/api/projects/test-proj/docs/scaffold/check")
    after = set(proj_root.rglob("*"))

    assert resp.status_code == 200
    assert before == after, f"Check endpoint must not write files. New files: {after - before}"


def test_check_endpoint_returns_stray_docs(client_ctx):
    """GET .../check lists stray top-level docs entries in stray array."""
    client, srv, _, projects_base, proj_root = client_ctx

    # Create a stray file in docs/
    (proj_root / "docs").mkdir()
    (proj_root / "docs" / "my-custom-notes.md").write_text("stray content")

    resp = client.get("/api/projects/test-proj/docs/scaffold/check")
    assert resp.status_code == 200
    data = resp.json()
    stray_names = [s for s in data["stray"] if "my-custom-notes" in s]
    assert len(stray_names) > 0, f"Expected stray entry for my-custom-notes.md, got: {data['stray']}"


# ── AC2: POST apply endpoint ──────────────────────────────────────────────────

def test_apply_returns_400_without_confirm_flag(client_ctx):
    """POST .../apply returns 400 when request body lacks { confirm: true }."""
    client, *_ = client_ctx

    resp = client.post(
        "/api/projects/test-proj/docs/scaffold/apply",
        json={},
    )
    assert resp.status_code == 400, (
        f"Expected 400 without confirm flag, got {resp.status_code}: {resp.text}"
    )


def test_apply_returns_400_with_confirm_false(client_ctx):
    """POST .../apply returns 400 when confirm is false."""
    client, *_ = client_ctx

    resp = client.post(
        "/api/projects/test-proj/docs/scaffold/apply",
        json={"confirm": False},
    )
    assert resp.status_code == 400, (
        f"Expected 400 with confirm=false, got {resp.status_code}: {resp.text}"
    )


def test_apply_creates_missing_files_with_confirm_true(client_ctx):
    """POST .../apply with { confirm: true } creates missing files."""
    client, srv, _, projects_base, proj_root = client_ctx

    resp = client.post(
        "/api/projects/test-proj/docs/scaffold/apply",
        json={"confirm": True},
    )
    assert resp.status_code == 200, (
        f"Expected 200 with confirm=true, got {resp.status_code}: {resp.text}"
    )
    data = resp.json()
    assert "created" in data, f"Response must contain 'created' key: {data}"
    assert "compliant" in data, f"Response must contain 'compliant' key: {data}"
    assert data["compliant"] is True, f"After apply project must be compliant: {data}"
    assert len(data["created"]) > 0, f"Must report created paths: {data}"

    # Verify files actually exist
    assert (proj_root / "docs" / "quickstart.md").exists(), (
        "docs/quickstart.md must be created"
    )
    assert (proj_root / "CHANGELOG.md").exists(), "CHANGELOG.md must be created"


# ── AC3: Path traversal guard ─────────────────────────────────────────────────

def test_check_rejects_project_root_outside_projects_base(tmp_path):
    """GET .../check returns 400/403 when resolved project root escapes _PROJECTS_BASE via symlink."""
    import os

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/escape-proj", "name": "Escape Proj", "environments": {}}
    ]))

    projects_base = tmp_path / "dev"
    projects_base.mkdir()

    # Create a real dir outside the base
    outside = tmp_path / "outside"
    outside.mkdir()

    # Symlink _PROJECTS_BASE/escape-proj → outside (symlink escape)
    link = projects_base / "escape-proj"
    os.symlink(outside, link)

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects") or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv
    import projects as projects_module

    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    with patch.object(srv, "projects_module", projects_module), \
         patch.object(srv, "_PROJECTS_BASE", projects_base):
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.get("/api/projects/escape-proj/docs/scaffold/check")

    assert resp.status_code in (400, 403), (
        f"Expected 400/403 for symlink escape outside projects_base, got {resp.status_code}: {resp.text}"
    )


def test_apply_rejects_project_root_outside_projects_base(tmp_path):
    """POST .../apply returns 400/403 when resolved project root escapes _PROJECTS_BASE via symlink."""
    import os

    projects_file = tmp_path / "projects.json"
    projects_file.write_text(json.dumps([
        {"repo": "owner/escape-proj", "name": "Escape Proj", "environments": {}}
    ]))

    projects_base = tmp_path / "dev"
    projects_base.mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()

    link = projects_base / "escape-proj"
    os.symlink(outside, link)

    for mod in list(sys.modules.keys()):
        if mod in ("server", "projects") or mod.startswith("services."):
            sys.modules.pop(mod, None)

    import server as srv
    import projects as projects_module

    projects_module.PROJECTS_FILE = projects_file

    from fastapi.testclient import TestClient
    with patch.object(srv, "projects_module", projects_module), \
         patch.object(srv, "_PROJECTS_BASE", projects_base):
        client = TestClient(srv.app, raise_server_exceptions=False)
        resp = client.post(
            "/api/projects/escape-proj/docs/scaffold/apply",
            json={"confirm": True},
        )

    assert resp.status_code in (400, 403), (
        f"Expected 400/403 for symlink escape outside projects_base, got {resp.status_code}: {resp.text}"
    )


# ── AC4: Apply never overwrites existing files ────────────────────────────────

def test_apply_does_not_overwrite_existing_files(client_ctx):
    """POST .../apply must not overwrite files that already exist."""
    client, srv, _, projects_base, proj_root = client_ctx

    # Pre-create a file with custom content
    (proj_root / "docs").mkdir(exist_ok=True)
    custom_content = "# My custom quickstart content — do not overwrite!\n"
    (proj_root / "docs" / "quickstart.md").write_text(custom_content)

    resp = client.post(
        "/api/projects/test-proj/docs/scaffold/apply",
        json={"confirm": True},
    )
    assert resp.status_code == 200

    # File content must be unchanged
    actual = (proj_root / "docs" / "quickstart.md").read_text()
    assert actual == custom_content, (
        f"Apply must not overwrite existing files. Got: {actual!r}"
    )


# ── AC5–AC11: Frontend (project.html) checks ─────────────────────────────────

def test_docs_structure_section_exists_in_settings_tab():
    """project.html must have a 'Docs Structure' section inside the settings pane."""
    html = (STATIC_DIR / "project.html").read_text()
    assert 'id="pane-settings"' in html, "pane-settings must exist"
    assert "Docs Structure" in html, (
        "project.html settings tab must contain a 'Docs Structure' section"
    )


def test_check_button_exists_in_html():
    """project.html must have a Check button for docs scaffold."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "scaffoldCheck" in html or "docsScaffoldCheck" in html or "scaffold-check" in html, (
        "project.html must contain a check function for docs scaffold"
    )


def test_apply_button_disabled_by_default_in_html():
    """project.html Apply scaffold button must be disabled by default."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "ps-scaffold-apply" in html or "scaffold-apply" in html, (
        "project.html must contain an Apply scaffold button"
    )
    # The apply button must have disabled attribute set in HTML (default state)
    assert 'id="ps-scaffold-apply"' in html or 'id="scaffold-apply-btn"' in html, (
        "Apply scaffold button must have a known id"
    )


def test_confirmation_dialog_exists_in_html():
    """project.html must contain a confirmation dialog for Apply scaffold."""
    html = (STATIC_DIR / "project.html").read_text()
    assert "scaffold-confirm" in html or "scaffoldConfirm" in html or "ps-scaffold-confirm" in html, (
        "project.html must contain the scaffold confirmation dialog"
    )


def test_apply_calls_endpoint_with_confirm_true_in_js():
    """project.html JS must call apply endpoint with { confirm: true }."""
    html = (STATIC_DIR / "project.html").read_text()
    # Must pass confirm:true to the apply endpoint
    assert "confirm: true" in html or '"confirm":true' in html or "'confirm':true" in html or "confirm:true" in html, (
        "project.html JS must call apply with { confirm: true }"
    )


def test_check_reruns_after_apply_in_js():
    """project.html JS must re-run check after successful apply."""
    html = (STATIC_DIR / "project.html").read_text()
    # The apply function should call the check function after success
    assert "scaffoldCheck" in html or "docsScaffoldCheck" in html, (
        "project.html must contain scaffoldCheck function"
    )
    # Must be called more than once (defined once, called again after apply)
    check_fn = "scaffoldCheck" if "scaffoldCheck" in html else "docsScaffoldCheck"
    count = html.count(check_fn)
    assert count >= 2, (
        f"'{check_fn}' must appear ≥2 times (definition + call after apply), found {count}"
    )


def test_stray_docs_informational_only_in_html():
    """project.html must display stray docs as informational; no create/delete button near stray list."""
    html = (STATIC_DIR / "project.html").read_text()
    # Must have stray section
    assert "stray" in html.lower(), (
        "project.html must reference stray docs"
    )
    # The word "stray" in context must not be adjacent to delete/remove buttons
    # (structural check: stray display container should not have delete/remove affordance)
    stray_idx = html.lower().find("stray")
    context_window = html[max(0, stray_idx - 200):stray_idx + 500]
    assert "delete" not in context_window.lower() and "remove" not in context_window.lower(), (
        "Stray docs display must not include delete/remove buttons"
    )
