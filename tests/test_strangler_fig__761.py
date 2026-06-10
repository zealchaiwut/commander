"""Tester AC verification for issue #761 — strangler-fig enforcement.

This is the Tester-authored companion to the coder's
``test_761__strangler_fig_gate.py``. One function per acceptance criterion,
independently re-derived. The feature is fully structural/in-process (a routers
package, an extracted backup endpoint, and a line-count gate in
sprint_manager.py), so every check runs in-process — none hit a live HTTP
server.
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
    _gate_monolith,
    _monolith_gate_enabled,
)


# AC1: routers/ package exists with __init__.py that exports routers
def test_strangler_fig__routers_package_exports_router():
    assert (DASHBOARD_DIR / "routers" / "__init__.py").exists()
    routers = importlib.import_module("routers")
    from fastapi import APIRouter
    assert routers.__all__, "routers.__all__ must export at least one router"
    exported = getattr(routers, routers.__all__[0])
    assert isinstance(exported, APIRouter)


# AC2: server.py mounts an extracted router via include_router(...)
def test_strangler_fig__server_includes_router():
    src = (DASHBOARD_DIR / "server.py").read_text()
    assert "include_router(" in src
    assert "from routers import backup_router" in src


# AC3: backup cluster extracted into routers/<area>.py with sibling service
def test_strangler_fig__backup_cluster_extracted_with_sibling_service():
    assert (DASHBOARD_DIR / "routers" / "backup.py").exists()
    assert (DASHBOARD_DIR / "routers" / "backup_service.py").exists()
    router_mod = importlib.import_module("routers.backup")
    svc = importlib.import_module("routers.backup_service")
    paths = {r.path for r in router_mod.router.routes}
    assert "/api/backup/status" in paths
    assert hasattr(svc, "get_backup_status")


# AC4: extracted endpoint behaves identically (200 + payload; 503 when down)
def test_strangler_fig__endpoint_behavior_preserved():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    router_mod = importlib.import_module("routers.backup")
    svc = importlib.import_module("routers.backup_service")
    payload = {
        "last_backup_at": None, "gist_id": None, "gist_url": None,
        "file_count": 0, "last_error": None,
    }
    app = FastAPI()
    app.include_router(router_mod.router)
    client = TestClient(app)

    with patch.object(svc, "get_backup_status", return_value=payload):
        ok = client.get("/api/backup/status")
    assert ok.status_code == 200
    assert ok.json() == payload

    # route is gone from the monolith
    assert '@app.get("/api/backup/status")' not in (DASHBOARD_DIR / "server.py").read_text()

    with patch.object(svc, "BACKUP_AVAILABLE", False), patch.object(svc, "_backup_module", None):
        down = client.get("/api/backup/status")
    assert down.status_code == 503


# AC5: COMMANDER_GATE_MONOLITH gate exists, mirrors GateResult, default on
def test_strangler_fig__gate_exists_and_default_on(tmp_path, monkeypatch):
    monkeypatch.delenv("COMMANDER_GATE_MONOLITH", raising=False)
    assert _monolith_gate_enabled() is True
    res = _gate_monolith(761, tmp_path, skip=True)
    assert isinstance(res, GateResult)
    assert res.gate == "monolith"


# AC6: gate FAILS any diff that increases server.py line count
def test_strangler_fig__gate_fails_on_growth(tmp_path):
    def fake_count(ref, rel_path, cwd):
        return 120 if ref == "HEAD" else 100

    with patch("sprint_manager._file_line_count_at_ref", side_effect=fake_count), \
         patch("sprint_manager._revert_to_sit") as mock_revert:
        res = _gate_monolith(761, tmp_path, skip=False, base_branch="develop")
    assert res.passed is False
    mock_revert.assert_called_once()


# AC7: gate PASSES any diff that decreases or holds server.py line count
@pytest.mark.parametrize("head,base", [(80, 100), (100, 100)])
def test_strangler_fig__gate_passes_on_shrink_or_hold(tmp_path, head, base):
    def fake_count(ref, rel_path, cwd):
        return head if ref == "HEAD" else base

    with patch("sprint_manager._file_line_count_at_ref", side_effect=fake_count):
        res = _gate_monolith(761, tmp_path, skip=False, base_branch="develop")
    assert res.passed is True
    assert res.skipped is False


# AC8: gate is env-toggleable via COMMANDER_GATE_MONOLITH=false
@pytest.mark.parametrize("val,expected", [("false", False), ("0", False), ("off", False), ("1", True)])
def test_strangler_fig__gate_env_toggle(monkeypatch, val, expected):
    monkeypatch.setenv("COMMANDER_GATE_MONOLITH", val)
    assert _monolith_gate_enabled() is expected


# AC9: coder.md carries the no-new-routes-in-server.py rule
def test_strangler_fig__coder_md_rule_present():
    low = (REPO_ROOT / ".claude" / "agents" / "coder.md").read_text().lower()
    assert "routers/" in low
    assert "server.py" in low
    assert "forbidden" in low or "must not" in low or "do not add" in low
