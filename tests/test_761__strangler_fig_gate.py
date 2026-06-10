"""Tests for issue #761 — strangler-fig enforcement.

Each test is anchored to an acceptance criterion:

  AC1  routers/ package exists with __init__.py that exports routers
  AC2  server.py uses include_router(...) to mount an extracted router
  AC3  one route cluster (backup) extracted into routers/<area>.py with
       service logic in a sibling module
  AC4  extracted endpoint behaves identically (200 + same payload shape)
  AC5  COMMANDER_GATE_MONOLITH gate exists, mirrors GateResult, default on
  AC6  gate FAILS a diff that increases server.py line count
  AC7  gate PASSES a diff that decreases or holds server.py line count
  AC8  gate is env-toggleable via COMMANDER_GATE_MONOLITH=false
  AC9  .claude/agents/coder.md carries the no-new-routes-in-server.py rule
"""
import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(DASHBOARD_DIR))

from sprint_manager import (  # noqa: E402
    GateResult,
    _file_line_count_at_ref,
    _gate_monolith,
    _monolith_gate_enabled,
)


# ── AC1: routers package exists and exports routers ──────────────────────────

class TestRoutersPackage:
    def test_init_exists(self):
        assert (DASHBOARD_DIR / "routers" / "__init__.py").exists()

    def test_init_exports_a_router(self):
        routers = importlib.import_module("routers")
        # at least one APIRouter is exported via __all__
        assert hasattr(routers, "__all__")
        assert len(routers.__all__) >= 1
        exported = getattr(routers, routers.__all__[0])
        from fastapi import APIRouter
        assert isinstance(exported, APIRouter)


# ── AC3: backup cluster extracted, service logic in sibling module ───────────

class TestBackupExtraction:
    def test_router_module_exists(self):
        assert (DASHBOARD_DIR / "routers" / "backup.py").exists()

    def test_sibling_service_module_exists(self):
        assert (DASHBOARD_DIR / "routers" / "backup_service.py").exists()

    def test_router_exposes_apirouter(self):
        mod = importlib.import_module("routers.backup")
        from fastapi import APIRouter
        assert isinstance(mod.router, APIRouter)

    def test_backup_status_route_registered(self):
        mod = importlib.import_module("routers.backup")
        paths = {r.path for r in mod.router.routes}
        assert "/api/backup/status" in paths

    def test_service_logic_lives_in_sibling(self):
        svc = importlib.import_module("routers.backup_service")
        assert hasattr(svc, "get_backup_status")


# ── AC2 + AC4: server mounts router; endpoint behaves identically ────────────

class TestServerMountAndBehavior:
    def test_server_calls_include_router(self):
        src = (DASHBOARD_DIR / "server.py").read_text()
        assert "include_router(" in src

    def test_server_does_not_define_backup_route_inline(self):
        """The route must be gone from server.py — it now lives in the router."""
        src = (DASHBOARD_DIR / "server.py").read_text()
        assert '@app.get("/api/backup/status")' not in src

    def test_endpoint_returns_service_payload(self):
        """AC4: mounting the router yields a 200 with the service payload."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mod = importlib.import_module("routers.backup")
        svc = importlib.import_module("routers.backup_service")

        fake_status = {
            "last_backup_at": None,
            "gist_id": None,
            "gist_url": None,
            "file_count": 0,
            "last_error": None,
        }
        app = FastAPI()
        app.include_router(mod.router)
        client = TestClient(app)
        with patch.object(svc, "get_backup_status", return_value=fake_status):
            resp = client.get("/api/backup/status")
        assert resp.status_code == 200
        assert resp.json() == fake_status

    def test_endpoint_503_when_backup_unavailable(self):
        """Behavior parity: unavailable backup module still yields 503."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        mod = importlib.import_module("routers.backup")
        svc = importlib.import_module("routers.backup_service")

        app = FastAPI()
        app.include_router(mod.router)
        client = TestClient(app)
        with patch.object(svc, "BACKUP_AVAILABLE", False), \
             patch.object(svc, "_backup_module", None):
            resp = client.get("/api/backup/status")
        assert resp.status_code == 503


# ── AC5: gate exists, mirrors GateResult, default on ─────────────────────────

class TestMonolithGateExists:
    def test_gate_returns_gateresult(self, tmp_path):
        result = _gate_monolith(1, tmp_path, skip=True)
        assert isinstance(result, GateResult)
        assert result.gate == "monolith"

    def test_skip_flag(self, tmp_path):
        result = _gate_monolith(1, tmp_path, skip=True)
        assert result == GateResult(gate="monolith", passed=True, skipped=True)

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("COMMANDER_GATE_MONOLITH", raising=False)
        assert _monolith_gate_enabled() is True


# ── AC6: gate FAILS when server.py grows ─────────────────────────────────────

class TestMonolithGateFailsOnGrowth:
    def test_fail_on_increase(self, tmp_path):
        def fake_count(ref, rel_path, cwd):
            return 100 if ref == "HEAD" else 90  # HEAD bigger than base

        with patch("sprint_manager._file_line_count_at_ref", side_effect=fake_count):
            with patch("sprint_manager._revert_to_sit") as mock_revert:
                result = _gate_monolith(1, tmp_path, skip=False, base_branch="develop")
        assert result.passed is False
        mock_revert.assert_called_once()


# ── AC7: gate PASSES when server.py shrinks or holds ─────────────────────────

class TestMonolithGatePassesOnNonGrowth:
    def test_pass_on_decrease(self, tmp_path):
        def fake_count(ref, rel_path, cwd):
            return 80 if ref == "HEAD" else 90  # HEAD smaller

        with patch("sprint_manager._file_line_count_at_ref", side_effect=fake_count):
            result = _gate_monolith(1, tmp_path, skip=False, base_branch="develop")
        assert result.passed is True
        assert result.skipped is False

    def test_pass_on_hold(self, tmp_path):
        def fake_count(ref, rel_path, cwd):
            return 90  # identical

        with patch("sprint_manager._file_line_count_at_ref", side_effect=fake_count):
            result = _gate_monolith(1, tmp_path, skip=False, base_branch="develop")
        assert result.passed is True

    def test_skip_gracefully_when_file_missing(self, tmp_path):
        def fake_count(ref, rel_path, cwd):
            return None  # file not present at ref

        with patch("sprint_manager._file_line_count_at_ref", side_effect=fake_count):
            result = _gate_monolith(1, tmp_path, skip=False, base_branch="develop")
        assert result.passed is True
        assert result.skipped is True


# ── AC8: env toggle ───────────────────────────────────────────────────────────

class TestMonolithGateEnvToggle:
    def test_false_disables(self, monkeypatch):
        monkeypatch.setenv("COMMANDER_GATE_MONOLITH", "false")
        assert _monolith_gate_enabled() is False

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("COMMANDER_GATE_MONOLITH", "0")
        assert _monolith_gate_enabled() is False

    def test_one_enables(self, monkeypatch):
        monkeypatch.setenv("COMMANDER_GATE_MONOLITH", "1")
        assert _monolith_gate_enabled() is True


# ── _file_line_count_at_ref real git behavior ────────────────────────────────

class TestFileLineCountHelper:
    def test_returns_none_outside_repo(self, tmp_path):
        assert _file_line_count_at_ref("HEAD", "nope.py", cwd=tmp_path) is None


# ── AC9: coder.md rule present ────────────────────────────────────────────────

class TestCoderAgentRule:
    def test_rule_present(self):
        coder_md = (REPO_ROOT / ".claude" / "agents" / "coder.md").read_text()
        assert "routers/" in coder_md
        low = coder_md.lower()
        assert "server.py" in low
        # the rule must forbid adding routes to server.py
        assert "forbidden" in low or "must not" in low or "do not add" in low
