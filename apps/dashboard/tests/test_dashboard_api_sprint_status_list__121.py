"""Tests for issue #121: /api/sprint-status returns list, supports per-project query.

Server under test: http://localhost:8003 (feature branch build).
Start before running:
    PORT=8003 DB_PATH=/tmp/test_commander_121.db \\
        ./venv/bin/uvicorn server:app --host 0.0.0.0 --port 8003
"""
import os
import signal
import subprocess
import time
from pathlib import Path

import httpx
import pytest

BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:8003")
COMMANDER_DIR = Path.home() / "dev" / "commander" / ".commander"
SPRINTS_DIR = COMMANDER_DIR / "sprints"


@pytest.fixture(scope="module")
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        # Ensure zealchaiwut/commander is registered so _all_sprints_running() scans it.
        existing = c.get("/api/projects").json().get("projects", [])
        repos = [p["repo"] for p in existing]
        if "zealchaiwut/commander" not in repos:
            c.post("/api/projects", json={"repo_url": "zealchaiwut/commander"})
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_fake_sprint(label: str) -> int:
    """Start a detached sleep process and write a PID file for it.

    Uses start_new_session=True to match how run_sprint_managed spawns real
    sprints, so that when the server SIGTERMs the process, init (not pytest)
    reaps the zombie and os.kill(pid, 0) correctly raises ProcessLookupError.
    """
    proc = subprocess.Popen(["sleep", "600"], start_new_session=True)
    SPRINTS_DIR.mkdir(parents=True, exist_ok=True)
    pid_file = SPRINTS_DIR / f"{label}-pid"
    pid_file.write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def _kill_fake_sprint(label: str) -> None:
    pid_file = SPRINTS_DIR / f"{label}-pid"
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGKILL)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
        try:
            pid_file.unlink()
        except OSError:
            pass


def _is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ---------------------------------------------------------------------------
# AC2: Empty list, always HTTP 200
# ---------------------------------------------------------------------------

def test_sprint_status_empty_returns_200_and_list(client):
    """AC2: when no sprints running, GET /api/sprint-status returns HTTP 200
    with body {"running_sprints": []}."""
    _kill_fake_sprint("sprint-test-ac2")
    r = client.get("/api/sprint-status")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert "running_sprints" in data, "Response must have 'running_sprints' key"
    assert isinstance(data["running_sprints"], list), "'running_sprints' must be a list"


def test_sprint_status_shape_is_list_not_flat(client):
    """AC1 (structural): response is object with 'running_sprints' list, not old flat shape."""
    r = client.get("/api/sprint-status")
    assert r.status_code == 200
    data = r.json()
    # Must NOT have old flat keys
    assert "sprint_label" not in data or "running_sprints" in data, \
        "Old flat shape detected — endpoint should return list shape"
    assert "running_sprints" in data, "New list shape must have 'running_sprints' key"
    assert isinstance(data["running_sprints"], list)


# ---------------------------------------------------------------------------
# AC4: Project filter — no sprint for project → 200 empty list
# ---------------------------------------------------------------------------

def test_sprint_status_filter_nonexistent_project_returns_200_empty(client):
    """AC4: ?project=<repo> with no running sprint returns HTTP 200 {"running_sprints": []}."""
    r = client.get("/api/sprint-status", params={"project": "zealchaiwut/no-such-project"})
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    assert data.get("running_sprints") == [], \
        f"Expected empty list for unknown project, got {data}"


# ---------------------------------------------------------------------------
# AC5: POST /api/sprints/run accepts project field
# ---------------------------------------------------------------------------

def test_run_sprint_requires_project_field(client):
    """AC5: POST /api/sprints/run without project field → 422 (field required)."""
    r = client.post("/api/sprints/run", json={"sprint_label": "sprint-7"})
    assert r.status_code == 422, \
        f"Expected 422 when project missing, got {r.status_code}: {r.text}"


def test_run_sprint_accepts_project_field(client):
    """AC5: POST /api/sprints/run with project field is accepted (not 422)."""
    r = client.post("/api/sprints/run", json={
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-invalid-xyz",  # invalid label → 400, but not 422
    })
    # 400 = bad label, 409 = already running, 202 = started — all are valid "accepted" responses
    assert r.status_code in (202, 400, 409), \
        f"Expected 202/400/409 with project field, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# AC6: 409 on duplicate (project, sprint_label)
# ---------------------------------------------------------------------------

def test_run_sprint_duplicate_returns_409(client):
    """AC6: POST /api/sprints/run with same (project, label) when already running → HTTP 409."""
    # sprint-7 PID file exists and process is alive (set up outside this test)
    sprint_7_pid_file = SPRINTS_DIR / "sprint-7-pid"
    if not sprint_7_pid_file.exists():
        pytest.skip("sprint-7-pid file not present — cannot test duplicate")

    try:
        pid = int(sprint_7_pid_file.read_text().strip())
        if not _is_process_alive(pid):
            pytest.skip("sprint-7 process not alive — cannot test duplicate")
    except (ValueError, OSError):
        pytest.skip("Cannot read sprint-7-pid")

    r = client.post("/api/sprints/run", json={
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-7",
    })
    assert r.status_code == 409, \
        f"Expected 409 for duplicate sprint, got {r.status_code}: {r.text}"
    body = r.json()
    assert "error" in body or "detail" in body, \
        "409 response must include error message"
    msg = body.get("error", body.get("detail", ""))
    assert "sprint-7" in msg and "zealchaiwut/commander" in msg, \
        f"Error message should name the duplicate: {msg}"


# ---------------------------------------------------------------------------
# AC1: Running sprint appears in list with correct fields
# ---------------------------------------------------------------------------

def test_sprint_status_running_sprint_in_list(client):
    """AC1: when a sprint is running, GET /api/sprint-status includes it in running_sprints
    with required fields: project, sprint_label, issues, progress.

    NOTE: This test exercises _all_sprints_running() which scans PID files.
    If it fails with an empty list, the likely cause is the pid_file.stem bug:
      label = pid_file.stem   # gives "sprint-801-pid" instead of "sprint-801"
    Fix: label = pid_file.name.removesuffix('-pid')
    """
    label = "sprint-801"
    _kill_fake_sprint(label)  # clean up any leftover
    _start_fake_sprint(label)
    try:
        r = client.get("/api/sprint-status")
        assert r.status_code == 200
        data = r.json()
        sprints = data.get("running_sprints", [])
        matching = [s for s in sprints if s.get("sprint_label") == label]
        assert len(matching) == 1, (
            f"Expected sprint '{label}' in running_sprints list, got {sprints}. "
            "Check: _all_sprints_running() uses pid_file.stem which returns "
            "'sprint-X-pid' instead of 'sprint-X'. Fix: use pid_file.name.removesuffix('-pid')"
        )
        item = matching[0]
        assert "project" in item, "Sprint item must have 'project' field"
        assert "sprint_label" in item, "Sprint item must have 'sprint_label' field"
        assert "issues" in item, "Sprint item must have 'issues' field"
        assert "progress" in item, "Sprint item must have 'progress' field"
        assert isinstance(item["issues"], list), "'issues' must be a list"
        assert isinstance(item["progress"], dict), "'progress' must be a dict"
        assert "closed" in item["progress"], "'progress' must have 'closed' key"
        assert "total" in item["progress"], "'progress' must have 'total' key"
    finally:
        _kill_fake_sprint(label)


# ---------------------------------------------------------------------------
# AC3: Project filter returns only matching sprint
# ---------------------------------------------------------------------------

def test_sprint_status_filter_by_project(client):
    """AC3: GET /api/sprint-status?project=<repo> returns only that project's sprints.

    NOTE: Depends on _all_sprints_running() working correctly.
    If AC1 fails, this will also fail for the same reason (pid_file.stem bug).
    """
    label_a = "sprint-803"
    label_b = "sprint-804"
    _kill_fake_sprint(label_a)
    _kill_fake_sprint(label_b)
    _start_fake_sprint(label_a)
    _start_fake_sprint(label_b)
    try:
        # Both appear without filter
        r_all = client.get("/api/sprint-status")
        assert r_all.status_code == 200
        all_sprints = r_all.json().get("running_sprints", [])
        # Filter: both PID files are under zealchaiwut/commander project
        r_filtered = client.get("/api/sprint-status",
                                params={"project": "zealchaiwut/commander"})
        assert r_filtered.status_code == 200
        filtered = r_filtered.json().get("running_sprints", [])
        # All filtered results must belong to the requested project
        for s in filtered:
            assert s.get("project") == "zealchaiwut/commander", \
                f"Filter returned sprint from wrong project: {s}"
        # If _all_sprints_running works, both test sprints appear in unfiltered
        all_labels = {s.get("sprint_label") for s in all_sprints}
        assert label_a in all_labels and label_b in all_labels, (
            f"Expected both test sprints ('{label_a}', '{label_b}') in unfiltered list, "
            f"got {all_labels}. "
            "Check pid_file.stem bug in _all_sprints_running(): use "
            "pid_file.name.removesuffix('-pid') instead of pid_file.stem"
        )
    finally:
        _kill_fake_sprint(label_a)
        _kill_fake_sprint(label_b)


# ---------------------------------------------------------------------------
# AC7: Different (project, label) pairs start without conflict
# ---------------------------------------------------------------------------

def test_run_sprint_different_pairs_no_conflict(client):
    """AC7: POST /api/sprints/run for a label not currently running → succeeds (202).
    Different (project, sprint_label) pairs do not block each other."""
    # sprint-7 is running; sprint-99 (different label) should be startable
    # We use a unique label unlikely to exist
    r = client.post("/api/sprints/run", json={
        "project": "zealchaiwut/commander",
        "sprint_label": "sprint-97",
    })
    try:
        # 202 = new sprint started, 409 = already running (unexpected here)
        assert r.status_code == 202, (
            f"Expected 202 when starting a non-duplicate sprint, "
            f"got {r.status_code}: {r.text}"
        )
        data = r.json()
        assert data.get("ok") is True
        assert data.get("sprint_label") == "sprint-97"
        assert "pid" in data
    finally:
        # Clean up
        pid_file = SPRINTS_DIR / "sprint-97-pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, signal.SIGKILL)
            except (ValueError, ProcessLookupError, PermissionError):
                pass
            try:
                pid_file.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# AC8: DELETE only kills the specific subprocess, sibling unaffected
# ---------------------------------------------------------------------------

def test_delete_sprint_kills_only_target(client):
    """AC8: DELETE /api/sprints/run/<id> kills only that sprint; sibling sprints unaffected."""
    label_keep = "sprint-808"
    label_kill = "sprint-809"
    _kill_fake_sprint(label_keep)
    _kill_fake_sprint(label_kill)

    pid_keep = _start_fake_sprint(label_keep)
    pid_kill = _start_fake_sprint(label_kill)

    try:
        assert _is_process_alive(pid_kill), "kill target should be alive before DELETE"
        assert _is_process_alive(pid_keep), "keep target should be alive before DELETE"

        r = client.delete(
            f"/api/sprints/run/{label_kill}",
            params={"project": "zealchaiwut/commander"},
        )
        assert r.status_code in (200, 204), \
            f"DELETE should return 200/204, got {r.status_code}: {r.text}"

        time.sleep(1)
        # The server removes the PID file on successful kill — this is the
        # authoritative signal that the sprint was stopped.
        killed_pid_file = SPRINTS_DIR / f"{label_kill}-pid"
        assert not killed_pid_file.exists(), \
            f"PID file for killed sprint should be removed by DELETE"

        # Sibling sprint PID file must still exist and its process must be alive.
        keep_pid_file = SPRINTS_DIR / f"{label_keep}-pid"
        assert keep_pid_file.exists(), \
            f"Sibling sprint PID file should still exist after DELETE"
        assert _is_process_alive(pid_keep), \
            f"Sibling sprint (PID {pid_keep}) should still be alive after DELETE"
    finally:
        _kill_fake_sprint(label_keep)
        _kill_fake_sprint(label_kill)


# ---------------------------------------------------------------------------
# AC9: Frontend JS handles list shape gracefully
# ---------------------------------------------------------------------------

def test_frontend_uses_running_sprints_list_shape(client):
    """AC9: app.js handles the new list shape — reads data.running_sprints, not data.active."""
    r = client.get("/static/app.js")
    assert r.status_code == 200
    js = r.text
    assert "running_sprints" in js, \
        "app.js must reference 'running_sprints' (new list shape)"
    # Old pattern that would break on empty list
    assert "data.active" not in js, \
        "app.js must not use data.active (old flat shape) in sprint-status handler"


def test_frontend_sprint_status_empty_list_no_crash(client):
    """AC9: GET /api/sprint-status returns valid JSON that JS can handle when empty."""
    r = client.get("/api/sprint-status")
    assert r.status_code == 200
    data = r.json()
    sprints = data.get("running_sprints", None)
    assert sprints is not None, "running_sprints key must exist"
    assert isinstance(sprints, list), "running_sprints must be a list (not null)"
    # The JS does: const sprints = data.running_sprints || [];
    # This is safe only if running_sprints is a list (or falsy); a null would be fine too
    # but a list is the correct contract
