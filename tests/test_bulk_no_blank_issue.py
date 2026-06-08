"""Regression: retrying a bulk draft with no body must NOT create a blank issue.

Bug: _post_ticket_body_to_github (the /retry-with-body, /retry-with-image,
/retry-all path) derived an "Untitled ticket" title from an empty body and
posted it, creating a blank GitHub issue. It now refuses and leaves the ticket
failed.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


@pytest.fixture()
def srv():
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv  # noqa: WPS433
    return srv


def _job(srv, title=None):
    job = {
        "job_id": "j1", "repo": "owner/repo", "status": "running",
        "default_labels": [],
        "tickets": [{"index": 0, "state": "failed", "title": title, "prompt": "p"}],
    }
    srv._bulk_jobs["j1"] = job
    return job


def test_empty_body_does_not_create_issue(srv):
    job = _job(srv, title=None)
    with patch.object(srv.github_client, "create_issue") as ci, \
         patch.object(srv, "_persist_bulk_job"), \
         patch.object(srv, "_broadcast_bulk_event", new=AsyncMock()):
        asyncio.run(srv._post_ticket_body_to_github("j1", 0, "   \n  "))
    ci.assert_not_called()
    assert job["tickets"][0]["state"] == "failed"
    assert "empty" in (job["tickets"][0]["error"] or "").lower()


def test_real_body_still_posts(srv):
    job = _job(srv, title=None)
    with patch.object(srv.github_client, "create_issue", return_value=(604, "http://x/604")) as ci, \
         patch.object(srv, "_persist_bulk_job"), \
         patch.object(srv, "_broadcast_bulk_event", new=AsyncMock()):
        asyncio.run(srv._post_ticket_body_to_github("j1", 0, "# Real title\n\nSome body."))
    ci.assert_called_once()
    assert job["tickets"][0]["state"] == "created"
    assert job["tickets"][0]["issue_num"] == 604
