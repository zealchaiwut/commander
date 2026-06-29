"""Tests for issue #747: subprocess timeout guards on deploy/restart calls.

AC coverage:
  AC1 — git fetch (pull) call has timeout= ≥ 30 s in deploy_environment
  AC2 — git rev-parse HEAD call has timeout= ≥ 10 s in deploy_environment
  AC3 — launchctl kickstart call has timeout= ≥ 30 s in _restart_environment
  AC4 — stop/start script calls each have timeout= ≥ 30 s in _restart_environment
  AC5 — subprocess.TimeoutExpired → HTTP 504 with JSON identifying the command
  AC6 — Non-timeout subprocess failures still return existing 500 response, not 504
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ok(stdout=""):
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail(stderr="command failed", returncode=1):
    r = MagicMock()
    r.returncode = returncode
    r.stdout = ""
    r.stderr = stderr
    return r


# ── Fixtures ───────────────────────────────────────────────────────────────────

FAKE_ENTRY_DEPLOY = {
    "host": "local",
    "working_dir": "/tmp/fake-repo",
    "branch": "develop",
    "launchd_label": "com.test.myservice",
}

FAKE_ENTRY_KICKSTART = {
    "host": "local",
    "launchd_label": "com.test.myservice",
}

FAKE_ENTRY_SCRIPTS = {
    "host": "local",
    "stop_script": "echo stop",
    "start_script": "echo start",
}


@pytest.fixture()
def env_client():
    """TestClient with project/config prereqs patched out of the way."""
    for mod in list(sys.modules.keys()):
        if mod == "server" or mod.startswith("routers") or mod.startswith("services"):
            sys.modules.pop(mod, None)

    sys.path.insert(0, str(DASHBOARD_DIR))
    import server as srv  # noqa: F401  (registers routes)
    import routers.environments as env_mod

    from fastapi.testclient import TestClient
    client = TestClient(srv.app, raise_server_exceptions=False)

    yield client, env_mod


# ── AC1 + AC5a: git fetch timeout → 504 ──────────────────────────────────────

def _timeout_near(src, marker, window=400):
    """Return the first timeout= value found within `window` chars after `marker` in `src`."""
    import re
    idx = src.find(marker)
    if idx == -1:
        return None
    chunk = src[idx: idx + window]
    m = re.search(r"timeout\s*=\s*(\d+)", chunk)
    return int(m.group(1)) if m else None


def test_fetch_has_timeout_param():
    """AC1: environments.py subprocess.run call for git fetch has timeout= ≥ 30 s."""
    src = (DASHBOARD_DIR / "routers" / "environments.py").read_text()
    val = _timeout_near(src, "build_fetch_command")
    assert val is not None, (
        "subprocess.run calling build_fetch_command must have a timeout= kwarg"
    )
    assert val >= 30, f"git fetch timeout must be ≥ 30 s; found timeout={val}"


def test_fetch_timeout_returns_504(env_client):
    """AC5a: when git fetch raises TimeoutExpired the deploy endpoint returns 504."""
    client, env_mod = env_client

    def _side_effect(cmd, **kwargs):
        if isinstance(cmd, list) and "fetch" in cmd:
            raise subprocess.TimeoutExpired(cmd, 30)
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config", return_value={"prd": FAKE_ENTRY_DEPLOY}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry", return_value=FAKE_ENTRY_DEPLOY),
        patch.object(env_mod._deploy_actions, "check_deploy_readiness", return_value=(True, [])),
        patch.object(env_mod._deploy_actions, "require_deploy_target",
                     return_value=("/tmp/fake-repo", "develop")),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/deploy")

    assert resp.status_code == 504, (
        f"Expected 504 when git fetch times out, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", "")
    assert "timed out" in detail.lower() or "timeout" in detail.lower(), (
        f"504 body must mention timeout; got: {detail!r}"
    )


# ── AC2 + AC5b: git rev-parse HEAD timeout → 504 ─────────────────────────────

def test_head_sha_has_timeout_param():
    """AC2: subprocess.run for git rev-parse HEAD has timeout= ≥ 10 s."""
    src = (DASHBOARD_DIR / "routers" / "environments.py").read_text()
    val = _timeout_near(src, "build_head_sha_command")
    assert val is not None, (
        "subprocess.run calling build_head_sha_command must have a timeout= kwarg"
    )
    assert val >= 10, f"git rev-parse HEAD timeout must be ≥ 10 s; found timeout={val}"


def test_head_sha_timeout_returns_504(env_client):
    """AC5b: when git rev-parse HEAD raises TimeoutExpired the deploy endpoint returns 504."""
    client, env_mod = env_client

    def _side_effect(cmd, **kwargs):
        if isinstance(cmd, list) and "rev-parse" in cmd and "HEAD" in cmd:
            raise subprocess.TimeoutExpired(cmd, 10)
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config", return_value={"prd": FAKE_ENTRY_DEPLOY}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry", return_value=FAKE_ENTRY_DEPLOY),
        patch.object(env_mod._deploy_actions, "check_deploy_readiness", return_value=(True, [])),
        patch.object(env_mod._deploy_actions, "require_deploy_target",
                     return_value=("/tmp/fake-repo", "develop")),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/deploy")

    assert resp.status_code == 504, (
        f"Expected 504 when git rev-parse HEAD times out, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", "")
    assert "timed out" in detail.lower() or "timeout" in detail.lower(), (
        f"504 body must mention timeout; got: {detail!r}"
    )


# ── AC3 + AC5c: launchctl kickstart timeout → 504 ───────────────────────────

def test_kickstart_has_timeout_param():
    """AC3: subprocess.run for launchctl kickstart has timeout= ≥ 30 s."""
    src = (DASHBOARD_DIR / "routers" / "environments.py").read_text()
    val = _timeout_near(src, "build_kickstart_command")
    assert val is not None, (
        "subprocess.run calling build_kickstart_command must have a timeout= kwarg"
    )
    assert val >= 30, f"kickstart timeout must be ≥ 30 s; found timeout={val}"


def test_kickstart_timeout_returns_504(env_client):
    """AC5c: when launchctl kickstart raises TimeoutExpired the restart endpoint returns 504."""
    client, env_mod = env_client

    def _side_effect(cmd, **kwargs):
        if isinstance(cmd, list) and "kickstart" in cmd:
            raise subprocess.TimeoutExpired(cmd, 30)
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config", return_value={"prd": FAKE_ENTRY_KICKSTART}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry", return_value=FAKE_ENTRY_KICKSTART),
        patch.object(env_mod._deploy_actions, "check_restart_readiness", return_value=(True, [])),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch.object(env_mod._deploy_actions, "is_self_restart", return_value=False),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/restart")

    assert resp.status_code == 504, (
        f"Expected 504 when kickstart times out, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", "")
    assert "timed out" in detail.lower() or "timeout" in detail.lower(), (
        f"504 body must mention timeout; got: {detail!r}"
    )


# ── AC4 source inspection: stop/start script timeout ─────────────────────────

def test_stop_start_scripts_have_timeout_param():
    """AC4: the stop/start loop in _restart_environment has timeout= ≥ 30 s.

    The scripts are called with **run_kw, so we look for 'timeout' being added
    to run_kw (or passed directly) in the section of environments.py that runs
    stop/start scripts.
    """
    src = (DASHBOARD_DIR / "routers" / "environments.py").read_text()
    # Find 'sh', '-c', script — the stop/start invocation
    idx = src.find('"sh", "-c", script')
    assert idx != -1, 'environments.py must contain subprocess.run(["sh", "-c", script], ...)'
    # Look backwards for the run_kw dict definition (within 600 chars)
    block = src[max(0, idx - 600): idx + 300]
    assert "timeout" in block, (
        "The stop/start subprocess.run block must include timeout= (directly or via run_kw)"
    )


# ── AC4 + AC5d: stop script timeout → 504 ────────────────────────────────────

def test_stop_script_timeout_returns_504(env_client):
    """AC4+AC5d: when the stop script raises TimeoutExpired, restart returns 504."""
    client, env_mod = env_client
    call_count = {"n": 0}

    def _side_effect(cmd, **kwargs):
        call_count["n"] += 1
        # First subprocess.run in the stop+start loop is the stop script
        if call_count["n"] == 1:
            raise subprocess.TimeoutExpired(cmd, 30)
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config",
                     return_value={"prd": FAKE_ENTRY_SCRIPTS}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry",
                     return_value=FAKE_ENTRY_SCRIPTS),
        patch.object(env_mod._deploy_actions, "check_restart_readiness",
                     return_value=(True, [])),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch.object(env_mod._deploy_actions, "restart_label", return_value=None),
        patch.object(env_mod._deploy_actions, "is_script_self_restart", return_value=False),
        patch.object(env_mod._deploy_actions, "stop_start_scripts",
                     return_value=("echo stop", "echo start")),
        patch.object(env_mod._deploy_actions, "build_restart_env", return_value=None),
        patch.object(env_mod._deploy_actions, "script_working_dir", return_value=None),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/restart")

    assert resp.status_code == 504, (
        f"Expected 504 when stop script times out, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", "")
    assert "timed out" in detail.lower() or "timeout" in detail.lower(), (
        f"504 body must mention timeout; got: {detail!r}"
    )


# ── AC4 + AC5e: start script timeout → 504 ───────────────────────────────────

def test_start_script_timeout_returns_504(env_client):
    """AC4+AC5e: when the start script raises TimeoutExpired, restart returns 504."""
    client, env_mod = env_client
    call_count = {"n": 0}

    def _side_effect(cmd, **kwargs):
        call_count["n"] += 1
        # Second subprocess.run is the start script
        if call_count["n"] == 2:
            raise subprocess.TimeoutExpired(cmd, 30)
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config",
                     return_value={"prd": FAKE_ENTRY_SCRIPTS}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry",
                     return_value=FAKE_ENTRY_SCRIPTS),
        patch.object(env_mod._deploy_actions, "check_restart_readiness",
                     return_value=(True, [])),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch.object(env_mod._deploy_actions, "restart_label", return_value=None),
        patch.object(env_mod._deploy_actions, "is_script_self_restart", return_value=False),
        patch.object(env_mod._deploy_actions, "stop_start_scripts",
                     return_value=("echo stop", "echo start")),
        patch.object(env_mod._deploy_actions, "build_restart_env", return_value=None),
        patch.object(env_mod._deploy_actions, "script_working_dir", return_value=None),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/restart")

    assert resp.status_code == 504, (
        f"Expected 504 when start script times out, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    detail = body.get("detail", "")
    assert "timed out" in detail.lower() or "timeout" in detail.lower(), (
        f"504 body must mention timeout; got: {detail!r}"
    )


# ── AC6: non-timeout failures still return 500, not 504 ──────────────────────

def test_fetch_failure_returns_500_not_504(env_client):
    """AC6: a non-zero exit from git fetch returns 500 (existing behavior), not 504."""
    client, env_mod = env_client

    def _side_effect(cmd, **kwargs):
        if isinstance(cmd, list) and "fetch" in cmd:
            return _fail(stderr="fatal: unable to reach origin")
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config", return_value={"prd": FAKE_ENTRY_DEPLOY}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry", return_value=FAKE_ENTRY_DEPLOY),
        patch.object(env_mod._deploy_actions, "check_deploy_readiness", return_value=(True, [])),
        patch.object(env_mod._deploy_actions, "require_deploy_target",
                     return_value=("/tmp/fake-repo", "develop")),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/deploy")

    assert resp.status_code == 500, (
        f"Expected 500 for non-timeout fetch failure, got {resp.status_code}: {resp.text}"
    )
    assert resp.status_code != 504, "Non-timeout failure must not return 504"


def test_kickstart_failure_returns_500_not_504(env_client):
    """AC6: a non-zero exit from kickstart returns 500, not 504."""
    client, env_mod = env_client

    def _side_effect(cmd, **kwargs):
        if isinstance(cmd, list) and "kickstart" in cmd:
            return _fail(stderr="kickstart: service not found")
        return _ok()

    with (
        patch.object(env_mod, "_resolve_project_slug", return_value="owner/test"),
        patch.object(env_mod, "_merged_deploy_config",
                     return_value={"prd": FAKE_ENTRY_KICKSTART}),
        patch.object(env_mod, "_enrich_local_working_dirs"),
        patch.object(env_mod._deploy_actions, "get_env_entry",
                     return_value=FAKE_ENTRY_KICKSTART),
        patch.object(env_mod._deploy_actions, "check_restart_readiness",
                     return_value=(True, [])),
        patch.object(env_mod._render_actions, "is_render_host", return_value=False),
        patch.object(env_mod._deploy_actions, "is_self_restart", return_value=False),
        patch("routers.environments.subprocess.run", side_effect=_side_effect),
    ):
        resp = client.post("/api/projects/test/environments/prd/restart")

    assert resp.status_code == 500, (
        f"Expected 500 for non-timeout kickstart failure, got {resp.status_code}: {resp.text}"
    )
    assert resp.status_code != 504, "Non-timeout failure must not return 504"
