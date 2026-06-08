"""Tests for bulk-create sprint assignment.

Verifies the label/sprint resolution helpers that back the new "assign a sprint
when bulk creating" feature:

- _compose_ticket_labels: a chosen sprint replaces the implicit "backlog" label
  (mirrors assign_sprint moving a ticket out of the backlog); no sprint → backlog.
  Any stray sprint-N in the per-ticket labels is dropped so the chosen sprint wins.
- _resolve_bulk_sprint_label: "" stays backlog, a concrete "sprint-N" passes
  through, and "NEW" creates the next sprint number (max existing + 1) via
  github_client.ensure_sprint_label, falling back to backlog on error.

Helpers implemented in apps/dashboard/server.py.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


@pytest.fixture()
def srv():
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv  # noqa: WPS433
    return srv


# ── _compose_ticket_labels ────────────────────────────────────────────────────

def test_no_sprint_keeps_backlog(srv):
    assert srv._compose_ticket_labels("", ["enhancement"]) == ["backlog", "enhancement"]


def test_no_sprint_empty_labels(srv):
    assert srv._compose_ticket_labels("", []) == ["backlog"]


def test_sprint_replaces_backlog(srv):
    # A sprint-assigned ticket must NOT also carry "backlog".
    labels = srv._compose_ticket_labels("sprint-7", ["enhancement", "frontend"])
    assert labels == ["sprint-7", "enhancement", "frontend"]
    assert "backlog" not in labels


def test_sprint_drops_stray_sprint_labels(srv):
    # If the per-ticket labels somehow include a sprint label, the chosen one wins
    # and no second sprint label leaks through.
    labels = srv._compose_ticket_labels("sprint-9", ["sprint-3", "bug"])
    assert labels == ["sprint-9", "bug"]


def test_blank_labels_filtered(srv):
    assert srv._compose_ticket_labels("sprint-2", ["", "  ".strip()]) == ["sprint-2"]


# ── _resolve_bulk_sprint_label ────────────────────────────────────────────────

def test_resolve_empty_is_backlog(srv):
    assert srv._resolve_bulk_sprint_label("", "owner/repo") == ""


def test_resolve_concrete_passthrough(srv):
    # A concrete sprint label is used as-is; no GitHub calls needed.
    with patch.object(srv.github_client, "list_sprints") as ls, \
         patch.object(srv.github_client, "ensure_sprint_label") as es:
        assert srv._resolve_bulk_sprint_label("sprint-15", "owner/repo") == "sprint-15"
        ls.assert_not_called()
        es.assert_not_called()


def test_resolve_new_creates_next_sprint(srv):
    with patch.object(srv.github_client, "list_sprints", return_value=[1, 2, 43]) as ls, \
         patch.object(srv.github_client, "ensure_sprint_label") as es:
        result = srv._resolve_bulk_sprint_label("NEW", "owner/repo")
    assert result == "sprint-44"
    ls.assert_called_once_with(repo_name="owner/repo")
    es.assert_called_once_with(44, repo_name="owner/repo")


def test_resolve_new_with_no_sprints_starts_at_one(srv):
    with patch.object(srv.github_client, "list_sprints", return_value=[]), \
         patch.object(srv.github_client, "ensure_sprint_label") as es:
        assert srv._resolve_bulk_sprint_label("NEW", "owner/repo") == "sprint-1"
        es.assert_called_once_with(1, repo_name="owner/repo")


def test_resolve_new_falls_back_to_backlog_on_error(srv):
    with patch.object(srv.github_client, "list_sprints", side_effect=RuntimeError("gh down")):
        assert srv._resolve_bulk_sprint_label("NEW", "owner/repo") == ""
