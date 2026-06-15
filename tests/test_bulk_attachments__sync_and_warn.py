"""Bulk create attachment pre-commit: cache sync + user-visible warnings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


def _import_server():
    import sys

    root = Path(__file__).resolve().parents[1] / "apps" / "dashboard"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import server as srv  # noqa: WPS433

    return srv


def test_sync_attachments_branch_ref_updates_local_head(tmp_path):
    srv = _import_server()
    cache_dir = tmp_path / "bare"
    cache_dir.mkdir()

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            return MagicMock(returncode=0, stdout="abc123\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        srv._sync_attachments_branch_ref(cache_dir)

    assert ["git", "fetch", "origin", srv._ATTACHMENTS_BRANCH] in calls
    update_cmds = [
        c for c in calls
        if c[:2] == ["git", "update-ref"]
        and c[2] == f"refs/heads/{srv._ATTACHMENTS_BRANCH}"
    ]
    assert len(update_cmds) == 1
    assert update_cmds[0][3] == f"refs/remotes/origin/{srv._ATTACHMENTS_BRANCH}"


def test_ticket_has_attachment_assignment_all_and_index():
    srv = _import_server()
    assignments = [
        {"filename": "mock.html", "assignment": "all"},
        {"filename": "other.png", "assignment": 1},
    ]
    assert srv._ticket_has_attachment_assignment(assignments, 0) is True
    assert srv._ticket_has_attachment_assignment(assignments, 1) is True
    # assignment "all" applies to every ticket index
    assert srv._ticket_has_attachment_assignment(assignments, 2) is True
    assert srv._ticket_has_attachment_assignment(
        [{"filename": "only-one.png", "assignment": 1}], 0
    ) is False


def test_init_attachment_cache_syncs_existing_bare_clone(tmp_path):
    srv = _import_server()
    cache_dir = tmp_path / "owner-repo"
    cache_dir.mkdir()
    with (
        patch.object(srv, "_get_attachment_cache_dir", return_value=cache_dir),
        patch.object(srv, "_sync_attachments_branch_ref") as sync,
    ):
        result = srv._init_attachment_cache("owner/repo")
        sync.assert_called_once_with(cache_dir)
        assert result == cache_dir


def test_apply_bulk_attachment_warning_sets_job_and_tickets():
    srv = _import_server()
    job = {
        "image_assignments": [
            {"filename": "a.html", "assignment": 0},
            {"filename": "b.png", "assignment": 1},
        ],
        "tickets": [
            {"index": 0, "attachment_warning": None},
            {"index": 1, "attachment_warning": None},
            {"index": 2, "attachment_warning": None},
        ],
    }
    msg = "Attachments were not uploaded"
    srv._apply_bulk_attachment_warning(job, msg)
    assert job["attachment_error"] == msg
    assert job["tickets"][0]["attachment_warning"] == msg
    assert job["tickets"][1]["attachment_warning"] == msg
    assert job["tickets"][2]["attachment_warning"] is None
