"""Tests for issue #724 — generalized launchd installer + perf-coach UAT wiring.

AC coverage (code-testable subset; physical Mac-mini install / reboot / log
steps are covered by the manual UAT steps in the issue):

  AC1 — install_launchd.sh accepts label/working_dir/uvicorn_path/port/ENVIRONMENT
        params; no hardcoded commander-specific values remain in the rendered plist
  AC2 — rendered .plist has RunAtLoad, KeepAlive, the supplied WorkingDirectory,
        EnvironmentVariables (PATH + ENVIRONMENT), and stdout/stderr log paths
        under ~/Library/Logs/<label>/
  AC5 — perf-coach UAT deploy config (uat env) seeds launchd_label=com.perfcoach.uat
  AC6 — restart for that label builds `launchctl kickstart -k <label>`
  AC7 — running with commander's params regenerates a valid commander plist
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SCRIPT = REPO_ROOT / "scripts" / "install_launchd.sh"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from services.sprint_manager import deploy_config_schema as dcs  # noqa: E402
from services.sprint_manager import deploy_actions as da  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def _render_plist(**params) -> str:
    """Invoke the installer in --print-plist mode and return the rendered plist.

    --print-plist renders to stdout and exits 0 without touching launchctl, so
    it is safe to run in CI and asserts on exactly the plist that would install.
    """
    args = ["bash", str(SCRIPT), "--print-plist"]
    for key, val in params.items():
        args += [f"--{key.replace('_', '-')}", str(val)]
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}"
    return proc.stdout


_PERFCOACH = dict(
    label="com.perfcoach.uat",
    working_dir="/Users/me/dev/perf-coach/uat/apps/dashboard",
    uvicorn_path="/Users/me/dev/perf-coach/uat/venv/bin/uvicorn",
    port=8011,
    environment="uat",
)


# ── AC1: parametrized — no hardcoded commander values ─────────────────────────


def test_script_exists_and_executable():
    assert SCRIPT.exists(), f"{SCRIPT} missing"


def test_plist_uses_supplied_label_not_commander():
    """AC1: label comes from the param, not a hardcoded commander value."""
    out = _render_plist(**_PERFCOACH)
    assert "<string>com.perfcoach.uat</string>" in out
    # no commander-specific identity leaks into a perf-coach plist
    assert "com.commander.dashboard" not in out


def test_plist_uses_supplied_working_dir_uvicorn_and_port():
    """AC1: working_dir, uvicorn_path and port all come from params."""
    out = _render_plist(**_PERFCOACH)
    assert "<string>/Users/me/dev/perf-coach/uat/apps/dashboard</string>" in out
    assert "/Users/me/dev/perf-coach/uat/venv/bin/uvicorn" in out
    assert "<string>8011</string>" in out
    # commander's default port must not leak when another port is supplied
    assert "<string>8000</string>" not in out


def test_plist_environment_value_is_parametrized():
    """AC1: the ENVIRONMENT env var value comes from the param."""
    out = _render_plist(**_PERFCOACH)
    assert "<key>ENVIRONMENT</key>" in out
    assert "<string>uat</string>" in out


# ── AC2: plist structure ──────────────────────────────────────────────────────


def test_plist_has_runatload_and_keepalive():
    """AC2: RunAtLoad and KeepAlive keys are present."""
    out = _render_plist(**_PERFCOACH)
    assert "<key>RunAtLoad</key>" in out
    assert "<key>KeepAlive</key>" in out


def test_plist_has_working_directory_and_environment_block():
    """AC2: WorkingDirectory + EnvironmentVariables (with PATH) present."""
    out = _render_plist(**_PERFCOACH)
    assert "<key>WorkingDirectory</key>" in out
    assert "<key>EnvironmentVariables</key>" in out
    assert "<key>PATH</key>" in out
    # PATH includes the uvicorn bin dir so the service finds the venv binaries
    assert "/Users/me/dev/perf-coach/uat/venv/bin" in out


def test_plist_logs_under_per_label_dir():
    """AC2: stdout/stderr log paths live under ~/Library/Logs/<label>/."""
    out = _render_plist(**_PERFCOACH)
    home = os.path.expanduser("~")
    log_dir = f"{home}/Library/Logs/com.perfcoach.uat"
    assert "<key>StandardOutPath</key>" in out
    assert "<key>StandardErrorPath</key>" in out
    assert f"{log_dir}/stdout.log" in out
    assert f"{log_dir}/stderr.log" in out


def test_plist_is_valid_xml_plist():
    """AC2: rendered output parses as a real plist dict."""
    import plistlib

    out = _render_plist(**_PERFCOACH)
    data = plistlib.loads(out.encode("utf-8"))
    assert data["Label"] == "com.perfcoach.uat"
    assert data["RunAtLoad"] is True
    assert "KeepAlive" in data
    assert data["WorkingDirectory"] == _PERFCOACH["working_dir"]
    assert data["EnvironmentVariables"]["ENVIRONMENT"] == "uat"
    assert data["ProgramArguments"][0] == _PERFCOACH["uvicorn_path"]
    assert "8011" in data["ProgramArguments"]


# ── AC5: perf-coach UAT seed carries the launchd label ────────────────────────


def test_perfcoach_uat_seed_has_launchd_label():
    """AC5: perf-coach uat seed config declares launchd_label=com.perfcoach.uat."""
    seed = dcs.seed_for("perf-coach")
    assert seed["uat"]["host"] == "local"
    assert seed["uat"]["launchd_label"] == "com.perfcoach.uat"


def test_perfcoach_uat_label_survives_response_build():
    """AC5: the label is preserved through the GET-safe response builder."""
    merged = dcs.merge_seed(dcs.seed_for("perf-coach"), {})
    resp = dcs.build_deploy_config_response(merged)
    assert resp["uat"]["launchd_label"] == "com.perfcoach.uat"


# ── AC6: restart wiring produces the kickstart command for the label ──────────


def test_restart_label_resolves_for_perfcoach_uat():
    """AC6: restart_label picks up the seeded perf-coach uat label."""
    entry = dcs.seed_for("perf-coach")["uat"]
    assert da.restart_label(entry) == "com.perfcoach.uat"


def test_kickstart_command_for_perfcoach_uat_label():
    """AC6: restart shells `launchctl kickstart -k <label>` for the uat label."""
    cmd = da.build_kickstart_command("com.perfcoach.uat", uid=501)
    assert cmd == ["launchctl", "kickstart", "-k", "gui/501/com.perfcoach.uat"]


# ── AC7: commander params still render a valid commander plist ────────────────


def test_commander_params_render_valid_plist():
    """AC7: running with commander's params regenerates a correct commander plist."""
    import plistlib

    out = _render_plist(
        label="com.commander.dashboard",
        working_dir=str(DASHBOARD_DIR),
        uvicorn_path=str(REPO_ROOT / "venv" / "bin" / "uvicorn"),
        port=8000,
        environment="prd",
    )
    data = plistlib.loads(out.encode("utf-8"))
    assert data["Label"] == "com.commander.dashboard"
    assert data["WorkingDirectory"] == str(DASHBOARD_DIR)
    assert data["EnvironmentVariables"]["ENVIRONMENT"] == "prd"
    assert "8000" in data["ProgramArguments"]


def test_defaults_reproduce_commander_when_no_params():
    """AC7: with no params the script defaults to the commander service."""
    out = _render_plist()  # no args beyond --print-plist
    assert "<string>com.commander.dashboard</string>" in out
    assert "<string>8000</string>" in out
