"""AC1 / UAT step 5: the doctor can be triggered from the dashboard and its
results are consistent with the CLI output."""
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402

ROUTER_FILE = REPO_ROOT / "apps" / "dashboard" / "routers" / "doctor.py"
SERVICE_FILE = REPO_ROOT / "apps" / "dashboard" / "routers" / "doctor_service.py"
INIT_FILE = REPO_ROOT / "apps" / "dashboard" / "routers" / "__init__.py"
SERVER_FILE = REPO_ROOT / "apps" / "dashboard" / "server.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_router_and_service_files_exist():
    assert ROUTER_FILE.exists()
    assert SERVICE_FILE.exists()


def test_route_declared_for_api_doctor():
    text = ROUTER_FILE.read_text()
    assert "/api/doctor" in text
    assert "@router.get" in text


def test_router_mounted_in_init_and_server():
    init_text = INIT_FILE.read_text()
    assert "doctor_router" in init_text
    server_text = SERVER_FILE.read_text()
    assert "doctor_router" in server_text
    assert "app.include_router(doctor_router)" in server_text


def test_service_run_doctor_returns_consistent_report():
    service = _load("doctor_service_under_test", SERVICE_FILE)
    report = service.run_doctor()
    assert set(["ok", "exit_code", "checks", "failures"]).issubset(report.keys())
    # Dashboard results mirror the CLI checks (same names, same order).
    cli_names = [r.name for r in doctor.run_all_checks()]
    dash_names = [c["name"] for c in report["checks"]]
    assert dash_names == cli_names
    for c in report["checks"]:
        assert set(["name", "ok", "fix", "detail"]).issubset(c.keys())
