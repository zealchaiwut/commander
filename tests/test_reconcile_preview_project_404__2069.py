"""Behavioral tests for issue #2069.

reconcile-preview cannot distinguish 'sprint not started' from 'you mistyped the project'.

Acceptance Criteria:
  AC1 — Unknown/unresolvable project → 404 with typed error naming the project,
         not 200 {"exists": false}.
  AC2 — exists: false is reserved for a genuinely resolved project with no matching sprint.
  AC3 — Sibling endpoint POST /api/sprints/{label}/reconcile shares the same fix.
  AC4 — Fix uses project_resolver.resolve_project_path (from #2064) to raise the 404.
  AC5 — Behavioral: unknown project → 404 with project named;
         known project + unknown sprint → 200 exists:false;
         known project + known sprint → normal preview payload (exists:true).

All tests exercise real code paths (CLAUDE.md #1746 — no source-regex checks).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
ROUTERS_DIR = DASHBOARD_DIR / "routers"

for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2069.db")
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")
os.environ.setdefault("COMMANDER_DISABLE_AUTO_RECONCILE", "1")

# ── Module loader (avoids routers/__init__ heavy imports) ────────────────────

def _load_module(name: str, path: Path):
    if "routers" not in sys.modules:
        stub = types.ModuleType("routers")
        stub.__path__ = [str(ROUTERS_DIR)]  # type: ignore[attr-defined]
        sys.modules["routers"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── Load modules once for the session ────────────────────────────────────────

_router_mod = _load_module("routers.sprint_history", ROUTERS_DIR / "sprint_history.py")
import db as db_module  # noqa: E402
import project_resolver as _pr_module  # noqa: E402

# ── Shared fixtures ────────────────────────────────────────────────────────────

FAKE_PROJECTS = [
    {"repo": "zealchaiwut/commander", "name": "Commander"},
]

KNOWN_PROJECT = "zealchaiwut/commander"
UNKNOWN_PROJECT = "nonexistent-xyz"
SPRINT_LABEL = "sprint-2069"


@pytest.fixture()
def fresh_db(tmp_path):
    """Isolated SQLite DB for the duration of a test."""
    db_file = tmp_path / "test_2069.db"
    original = db_module.DB_PATH
    db_module.DB_PATH = db_file
    db_module.init_db()
    yield db_module
    db_module.DB_PATH = original


@pytest.fixture()
def client(tmp_path):
    """TestClient with patched project resolution (projects.load_projects)."""
    app = FastAPI()
    app.include_router(_router_mod.router)
    with (
        patch("projects.load_projects", return_value=FAKE_PROJECTS),
        patch.object(_pr_module, "_PROJECTS_BASE", tmp_path),
    ):
        # Create the project directory so resolve_project_path finds it
        (tmp_path / "commander").mkdir(parents=True, exist_ok=True)
        yield TestClient(app, raise_server_exceptions=False)


# ── AC1 / AC5 ─────────────────────────────────────────────────────────────────


class TestUnknownProjectReturns404:
    """AC1 / AC5: An unknown project MUST return 404, not 200 {"exists": false}."""

    def test_reconcile_preview_unknown_project_returns_404(self, client, fresh_db):
        """GET reconcile-preview with unknown project → 404 with project named (AC1/AC5)."""
        resp = client.get(
            f"/api/sprints/{SPRINT_LABEL}/reconcile-preview",
            params={"project": UNKNOWN_PROJECT},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown project, got {resp.status_code}: {resp.text}"
        )

    def test_reconcile_preview_404_detail_names_project(self, client, fresh_db):
        """AC1: 404 response body must name the unrecognised project."""
        resp = client.get(
            f"/api/sprints/{SPRINT_LABEL}/reconcile-preview",
            params={"project": UNKNOWN_PROJECT},
        )
        assert resp.status_code == 404
        detail = resp.json().get("detail", "")
        assert UNKNOWN_PROJECT in detail, (
            f"404 detail must name the project '{UNKNOWN_PROJECT}'; got: {detail!r}"
        )

    def test_reconcile_apply_unknown_project_returns_404(self, client, fresh_db):
        """AC3/AC5: POST reconcile with unknown project → 404 (sibling endpoint fix)."""
        resp = client.post(
            f"/api/sprints/{SPRINT_LABEL}/reconcile",
            json={"project": UNKNOWN_PROJECT, "confirmed": True},
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown project on POST reconcile, got {resp.status_code}: {resp.text}"
        )

    def test_reconcile_apply_404_detail_names_project(self, client, fresh_db):
        """AC3: POST reconcile 404 detail must name the unrecognised project."""
        resp = client.post(
            f"/api/sprints/{SPRINT_LABEL}/reconcile",
            json={"project": UNKNOWN_PROJECT, "confirmed": True},
        )
        assert resp.status_code == 404
        detail = resp.json().get("detail", "")
        assert UNKNOWN_PROJECT in detail, (
            f"POST reconcile 404 detail must name the project '{UNKNOWN_PROJECT}'; got: {detail!r}"
        )


# ── AC2 / AC5 ─────────────────────────────────────────────────────────────────


class TestKnownProjectUnknownSprint:
    """AC2 / AC5: exists:false is reserved for a resolved project with no matching sprint."""

    def test_known_project_missing_sprint_returns_200_exists_false(self, client, fresh_db):
        """AC2/AC5: known project + sprint not in DB → 200 with exists:false (not 404)."""
        resp = client.get(
            f"/api/sprints/{SPRINT_LABEL}/reconcile-preview",
            params={"project": KNOWN_PROJECT},
        )
        assert resp.status_code == 200, (
            f"Expected 200 for known project/unknown sprint, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("exists") is False, (
            f"Expected exists:false for missing sprint; got: {body}"
        )

    def test_known_project_missing_sprint_label_matches(self, client, fresh_db):
        """AC2: the 200 response includes the sprint label that was queried."""
        resp = client.get(
            f"/api/sprints/{SPRINT_LABEL}/reconcile-preview",
            params={"project": KNOWN_PROJECT},
        )
        assert resp.status_code == 200
        assert resp.json().get("label") == SPRINT_LABEL


# ── AC5 — known project + known sprint returns normal payload ─────────────────


class TestKnownProjectKnownSprint:
    """AC5: known project + known sprint → normal preview payload with exists:true."""

    def _insert_sprint(self, db, label: str, project: str):
        with db.get_conn() as conn:
            db._create_sprint_lifecycle_tables(conn)
            conn.execute(
                "INSERT INTO sprints (label, project, state) VALUES (?, ?, ?)",
                (label, project, "completed"),
            )
            conn.commit()

    def test_known_project_known_sprint_returns_exists_true(self, client, fresh_db, tmp_path):
        """AC5: when both project and sprint exist in DB, exists is True."""
        self._insert_sprint(fresh_db, SPRINT_LABEL, KNOWN_PROJECT)

        # Stub out the GitHub-reconcile and reconciliation calls to avoid real I/O
        import routers.sprint_reconcile_service as svc_mod  # noqa: PLC0415
        with (
            patch.object(svc_mod, "_github_reconcile_row", return_value=None),
            patch(
                "services.sprint_manager.reconciliation.run_reconciliation",
                return_value={"checks": [], "all_clear": True},
            ),
        ):
            resp = client.get(
                f"/api/sprints/{SPRINT_LABEL}/reconcile-preview",
                params={"project": KNOWN_PROJECT},
            )

        assert resp.status_code == 200, (
            f"Expected 200 for known project+sprint, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("exists") is True, (
            f"Expected exists:true for known sprint; got: {body}"
        )
        assert body.get("label") == SPRINT_LABEL


# ── AC4 — resolution uses project_resolver ────────────────────────────────────


class TestProjectResolverIsUsed:
    """AC4: the 404 is raised via project_resolver.resolve_project_path, not ad-hoc."""

    def test_resolve_project_path_called_and_raises_for_unknown(self, client, fresh_db):
        """AC4: resolve_project_path must be invoked; an unknown project reaches its 404 branch."""
        import project_resolver as pr  # noqa: PLC0415
        call_log = []

        original_fn = pr.resolve_project_path

        def _spy(project):
            call_log.append(project)
            return original_fn(project)

        with patch("project_resolver.resolve_project_path", side_effect=_spy):
            resp = client.get(
                f"/api/sprints/{SPRINT_LABEL}/reconcile-preview",
                params={"project": UNKNOWN_PROJECT},
            )

        assert resp.status_code == 404
        assert UNKNOWN_PROJECT in call_log, (
            f"resolve_project_path was not called with '{UNKNOWN_PROJECT}'; calls: {call_log}"
        )
