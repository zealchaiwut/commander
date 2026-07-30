"""Tests for issue #2022: Reasoning view endpoint.

AC1: GET /api/runs/{agent_run_id}/reasoning with a valid run id returns 200 and
     contains final_message, transcript_path, and log_tail fields.
AC2: GET /api/runs/{agent_run_id}/reasoning with a non-existent run id returns 404.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Add dashboard dir to path for importing server and db
DASHBOARD_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(DASHBOARD_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """TestClient with database and projects stubbed."""
    monkeypatch.setenv("COMMANDER_BASE", str(tmp_path))

    import server

    fake_project = {"repo": "zealchaiwut/commander", "name": "Commander"}
    import projects as projects_module
    monkeypatch.setattr(projects_module, "load_projects", lambda: [fake_project])
    monkeypatch.setattr(
        "server.projects_module.load_projects", lambda: [fake_project]
    )

    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


class TestReasoningEndpoint:
    """AC1/AC2: GET /api/runs/{agent_run_id}/reasoning behavior."""

    def test_reasoning_endpoint_valid_run_returns_200_with_fields(
        self, client, tmp_path
    ):
        """AC1: Valid run id returns 200 with final_message, transcript_path, log_tail."""
        import db

        # Create a test log file
        log_file = tmp_path / "test_agent.log"
        log_file.write_text("Initial setup\nAgent running\nFinal output line\n")

        # Record a start event
        run_id = db.record_agent_start(
            issue_number=2022,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file),
        )
        assert run_id is not None, "record_agent_start should return a run_id"

        # Record a finish event with transcript_path
        # Note: final_message is populated automatically from log_path via _read_log_tail
        db.record_agent_finish(
            issue_number=2022,
            sprint_label="sprint-test",
            agent="coder",
            outcome="success",
            transcript_path="/path/to/transcript.jsonl",
            run_id=run_id,
        )

        # Now GET the reasoning endpoint
        response = client.get(f"/api/runs/{run_id}/reasoning")

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()

        # Assert the required fields are present
        assert "final_message" in data
        assert "transcript_path" in data
        assert "log_tail" in data

        # Assert field values
        # final_message is auto-populated from log tail by _read_log_tail
        assert data["final_message"] is not None
        assert "Final output line" in data["final_message"]
        assert data["transcript_path"] == "/path/to/transcript.jsonl"
        # log_tail should contain the last part of the log file
        assert data["log_tail"] is not None
        assert "Final output line" in data["log_tail"]

    def test_reasoning_endpoint_nonexistent_run_returns_404(self, client):
        """AC2: Non-existent run id returns 404 with detail message."""
        # Request a very high run_id that definitely doesn't exist
        response = client.get("/api/runs/999999999/reasoning")

        assert response.status_code == 404, f"Expected 404, got {response.status_code}"
        data = response.json()

        # Verify error message format
        assert "detail" in data
        assert "999999999" in data["detail"]
        assert "not found" in data["detail"].lower()

    def test_reasoning_endpoint_with_null_fields(self, client, tmp_path):
        """AC1 edge case: Null final_message and transcript_path should still be returned."""
        import db

        # Create a test log file
        log_file = tmp_path / "test_agent.log"
        log_file.write_text("Some log content\n")

        # Record a start event
        run_id = db.record_agent_start(
            issue_number=2022,
            sprint_label="sprint-test",
            agent="coder",
            log_path=str(log_file),
        )
        assert run_id is not None

        # Record a finish event with no final_message or transcript_path
        # (they default to None)
        db.record_agent_finish(
            issue_number=2022,
            sprint_label="sprint-test",
            agent="coder",
            outcome="success",
            run_id=run_id,
        )

        # GET the reasoning endpoint
        response = client.get(f"/api/runs/{run_id}/reasoning")

        assert response.status_code == 200
        data = response.json()

        # Fields should be present even if null
        assert "final_message" in data
        assert "transcript_path" in data
        assert "log_tail" in data
        # Some may be None but the keys should exist
        assert data.get("final_message") is None or isinstance(
            data["final_message"], str
        )
        assert data.get("transcript_path") is None or isinstance(
            data["transcript_path"], str
        )
