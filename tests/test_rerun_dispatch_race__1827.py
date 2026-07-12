"""Regression tests for issue #1827: rerun label-move → sprint_manager dispatch race.

AC coverage:
- AC-1: Rerun endpoint writes a rerun-manifest file and passes --rerun-manifest
        to sprint_manager argv so the exact moved ticket list is always used.
- AC-2: sprint_manager with --rerun-manifest dispatches all N tickets from the
        manifest, bypassing list_backlog_issues (simulates GitHub label-read lag
        where only a subset appears under the new label).
- AC-3: When the rerun manifest says all_moved=True but sprint_manager resolves
        0 dispatchable tickets (all decisions had action=skip), the run is treated
        as an error, not a quiet draft.
"""
from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

import server as srv  # noqa: E402

_FAKE_REPO = "owner/test-proj"


def _issue(number: int, *label_names: str, title: str = "") -> dict:
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": "",
        "labels": [{"name": n} for n in label_names],
    }


@pytest.fixture()
def client():
    return TestClient(srv.app)


# ── Shared rerun-endpoint helper ───────────────────────────────────────────────

def _post_rerun_capture_spawn(tmp_path, sprint_label, issues, confirmed_moved=None):
    """Post a rerun request and capture the spawn argv and manifest file path."""
    spawn_calls = []

    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.wait.side_effect = subprocess.TimeoutExpired("cmd", 2.0)

    def fake_popen(cmd, **kwargs):
        spawn_calls.append(list(cmd))
        return mock_proc

    sm_path = tmp_path / "sprint_manager.py"
    sm_path.touch()

    all_nums = {i["number"] for i in issues}
    if confirmed_moved is None:
        confirmed_moved = all_nums  # default: all confirmed

    with (
        patch.object(srv, "_is_sprint_running", return_value=False),
        patch.object(srv, "_project_root_path", return_value=tmp_path),
        patch.object(srv, "_coder_clone_path", return_value=tmp_path),
        patch.object(srv, "SPRINT_MANAGER_PATH", sm_path),
        patch.object(
            srv.github_client, "list_open_issues_with_body", return_value=issues
        ),
        patch.object(srv.github_client, "list_labels", return_value=[]),
        patch.object(srv.github_client, "get_label_color", return_value="0075ca"),
        patch.object(srv.github_client, "create_label", return_value=None),
        patch.object(srv.github_client, "update_labels", return_value=None),
        patch.object(srv.github_client, "invalidate", return_value=None),
        patch.object(srv, "_await_rerun_relabel", return_value=confirmed_moved),
        patch.object(srv, "_emit_dashboard_event", return_value=None),
        patch("subprocess.Popen", side_effect=fake_popen),
    ):
        client = TestClient(srv.app)
        resp = client.post(
            f"/api/sprints/{sprint_label}/rerun",
            params={"project": _FAKE_REPO},
            json={"auto_run": True},
        )

    return resp, spawn_calls, tmp_path


# ── AC-1: rerun endpoint writes manifest and passes --rerun-manifest ──────────

class TestRerunManifestWrittenAndPassed:
    """AC-1: endpoint writes manifest file and includes --rerun-manifest in argv."""

    def test_rerun_manifest_file_created(self, tmp_path):
        """Manifest file is written in the sprints directory for the sub-label."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, _, root = _post_rerun_capture_spawn(tmp_path, "sprint-4", issues)
        assert resp.status_code == 200
        sprints_dir = root / ".commander" / "sprints"
        sub_label = resp.json()["sub_label"]
        manifest_path = sprints_dir / f"{sub_label}-rerun-manifest.json"
        assert manifest_path.exists(), (
            f"rerun-manifest file must be written at {manifest_path}"
        )

    def test_rerun_manifest_contains_moved_decisions(self, tmp_path):
        """Manifest decisions include both moved tickets with non-skip actions."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, _, root = _post_rerun_capture_spawn(tmp_path, "sprint-4", issues)
        assert resp.status_code == 200
        sub_label = resp.json()["sub_label"]
        manifest_path = root / ".commander" / "sprints" / f"{sub_label}-rerun-manifest.json"
        manifest = json.loads(manifest_path.read_text())

        issue_nums = {d["issue_num"] for d in manifest["decisions"]}
        assert issue_nums == {49, 50}, (
            "Manifest must contain decisions for all moved tickets"
        )
        for d in manifest["decisions"]:
            assert d["action"] != "skip", (
                "Moved tickets must have non-skip action in manifest"
            )

    def test_rerun_argv_contains_rerun_manifest_flag(self, tmp_path):
        """--rerun-manifest flag is present in the sprint_manager spawn argv."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, spawn_calls, _ = _post_rerun_capture_spawn(
            tmp_path, "sprint-4", issues
        )
        assert resp.status_code == 200
        assert spawn_calls, "sprint_manager must be spawned"
        argv = spawn_calls[0]
        assert "--rerun-manifest" in argv, (
            f"--rerun-manifest must be in spawn argv, got: {argv}"
        )

    def test_rerun_manifest_path_in_argv_exists(self, tmp_path):
        """The --rerun-manifest path passed in argv points to the written file."""
        issues = [_issue(49, "sprint-4", "needs-rework")]
        resp, spawn_calls, _ = _post_rerun_capture_spawn(
            tmp_path, "sprint-4", issues
        )
        assert resp.status_code == 200
        argv = spawn_calls[0]
        idx = argv.index("--rerun-manifest")
        manifest_path = Path(argv[idx + 1])
        assert manifest_path.exists(), (
            "Path passed to --rerun-manifest must point to the written file"
        )

    def test_rerun_manifest_includes_all_moved_flag(self, tmp_path):
        """Manifest carries all_moved=True when all tickets were confirmed moved."""
        issues = [
            _issue(49, "sprint-4", "needs-rework"),
            _issue(50, "sprint-4", "needs-rework"),
        ]
        resp, _, root = _post_rerun_capture_spawn(tmp_path, "sprint-4", issues)
        assert resp.status_code == 200
        sub_label = resp.json()["sub_label"]
        manifest_path = root / ".commander" / "sprints" / f"{sub_label}-rerun-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        # _await_rerun_relabel returns {49, 50} = all confirmed
        assert manifest.get("all_moved") is True

    def test_rerun_manifest_not_passed_when_auto_run_false(self, tmp_path):
        """When auto_run=False, sprint_manager is not spawned so manifest path is
        irrelevant — but the manifest file should still be written for future use."""
        issues = [_issue(49, "sprint-4", "needs-rework")]
        sm_path = tmp_path / "sprint_manager.py"
        sm_path.touch()

        with (
            patch.object(srv, "_is_sprint_running", return_value=False),
            patch.object(srv, "_project_root_path", return_value=tmp_path),
            patch.object(srv, "_coder_clone_path", return_value=tmp_path),
            patch.object(srv, "SPRINT_MANAGER_PATH", sm_path),
            patch.object(
                srv.github_client, "list_open_issues_with_body", return_value=issues
            ),
            patch.object(srv.github_client, "list_labels", return_value=[]),
            patch.object(srv.github_client, "get_label_color", return_value="0075ca"),
            patch.object(srv.github_client, "create_label", return_value=None),
            patch.object(srv.github_client, "update_labels", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
            patch.object(srv, "_await_rerun_relabel", return_value={49}),
            patch.object(srv, "_emit_dashboard_event", return_value=None),
        ):
            client = TestClient(srv.app)
            resp = client.post(
                "/api/sprints/sprint-4/rerun",
                params={"project": _FAKE_REPO},
                json={"auto_run": False},
            )

        assert resp.status_code == 200
        assert resp.json().get("queued") is True


# ── AC-2: sprint_manager preflight uses manifest, not stale GitHub list ────────

class TestManifestBypassesGitHubLabelSearch:
    """AC-2: run_sprint_preflight with rerun_manifest uses manifest decisions,
    NOT list_backlog_issues (which may return a stale GitHub subset)."""

    def _preflight_stack(self, stack, tmp_path, list_backlog_mock):
        """Enter all patches needed for run_sprint_preflight with dry_run."""
        sm = "services.sprint_manager.sprint_manager"
        stack.enter_context(patch(f"{sm}.list_backlog_issues", list_backlog_mock))
        stack.enter_context(patch(f"{sm}.get_effective_llm_provider", return_value="anthropic"))
        stack.enter_context(patch(f"{sm}._post_sprint_status"))
        stack.enter_context(patch(f"{sm}._warn_file_conflicts"))
        stack.enter_context(patch(f"{sm}._load_sprint_plan", return_value=None))
        stack.enter_context(patch(f"{sm}._build_sprint_dag_layers", return_value=None))
        stack.enter_context(patch(
            f"{sm}._compute_dispatch_levels",
            side_effect=lambda issues, *_a, **_k: [list(issues)],
        ))
        stack.enter_context(patch(f"{sm}.structured_log"))
        stack.enter_context(patch(f"{sm}.mint_run_id", return_value="run-test-id"))

    def test_manifest_overrides_stale_github_list(self, tmp_path):
        """When list_backlog_issues returns only 1 of 2 tickets (GitHub label-read
        lag), the manifest ensures both are dispatched."""
        from services.sprint_manager.sprint_manager import run_sprint_preflight
        from services.sprint_manager.alerts import AlertMode
        from services.sprint_manager.config import SprintConfig

        cfg = SprintConfig(
            repo_name=_FAKE_REPO,
            sprints_dir=tmp_path,
            worktree_coder=tmp_path,
            worktree_tester=tmp_path,
        )

        # GitHub only sees ticket 50 (49 hasn't appeared yet — label lag)
        stale_gh_issue = {"number": 50, "title": "Issue 50", "labels": [
            {"name": "sprint-4.1"}, {"name": "needs-rework"}
        ]}

        manifest = {
            "decisions": [
                {"issue_num": 49, "issue_title": "Issue 49", "action": "coder"},
                {"issue_num": 50, "issue_title": "Issue 50", "action": "coder"},
            ],
            "all_moved": True,
        }

        list_backlog_mock = MagicMock(return_value=[stale_gh_issue])
        with contextlib.ExitStack() as stack:
            self._preflight_stack(stack, tmp_path, list_backlog_mock)
            result = run_sprint_preflight(
                label="sprint-4.1",
                alert_modes=[AlertMode.DASHBOARD_BANNER],
                repo_name=_FAKE_REPO,
                cfg=cfg,
                rerun_manifest=manifest,
                dry_run=True,
            )

        assert not result.early_exit, (
            "Preflight should not early-exit with a valid manifest"
        )
        issue_nums = {iss.number for iss in result.state.issues}
        assert issue_nums == {49, 50}, (
            f"Both manifest tickets must be in state.issues, got: {issue_nums}. "
            f"list_backlog_issues was stale and only had ticket 50."
        )
        # The key AC-2 invariant: GitHub list was NOT consulted
        list_backlog_mock.assert_not_called()

    def test_manifest_with_no_github_tickets_dispatches_all(self, tmp_path):
        """Even if list_backlog_issues returns [] (full label-read lag), manifest
        ensures all tickets are dispatched — not a 'no dispatchable tickets' draft."""
        from services.sprint_manager.sprint_manager import run_sprint_preflight
        from services.sprint_manager.alerts import AlertMode
        from services.sprint_manager.config import SprintConfig

        cfg = SprintConfig(
            repo_name=_FAKE_REPO,
            sprints_dir=tmp_path,
            worktree_coder=tmp_path,
            worktree_tester=tmp_path,
        )

        manifest = {
            "decisions": [
                {"issue_num": 53, "issue_title": "Issue 53", "action": "coder"},
                {"issue_num": 54, "issue_title": "Issue 54", "action": "coder"},
                {"issue_num": 55, "issue_title": "Issue 55", "action": "coder"},
            ],
            "all_moved": True,
        }

        list_backlog_mock = MagicMock(return_value=[])  # GitHub returns nothing
        with contextlib.ExitStack() as stack:
            self._preflight_stack(stack, tmp_path, list_backlog_mock)
            result = run_sprint_preflight(
                label="sprint-5.1",
                alert_modes=[AlertMode.DASHBOARD_BANNER],
                repo_name=_FAKE_REPO,
                cfg=cfg,
                rerun_manifest=manifest,
                dry_run=True,
            )

        assert not result.early_exit, (
            "Must not early-exit when manifest provides 3 tickets even though "
            "GitHub returns empty (simulating full label-search lag)"
        )
        issue_nums = {iss.number for iss in result.state.issues}
        assert issue_nums == {53, 54, 55}, (
            f"All 3 manifest tickets must be in state, got: {issue_nums}"
        )
        list_backlog_mock.assert_not_called()


# ── AC-3: all_moved=True + 0 dispatchable tickets → loud error ────────────────

class TestAllMovedNoTicketsIsError:
    """AC-3: When manifest has all_moved=True but all decisions are skip (0
    dispatchable), the preflight logs an ERROR message and sets rerun_dispatch_error
    on the result — not the quiet 'all skipped' message."""

    def test_all_moved_with_all_skip_decisions_logs_error(self, tmp_path, capsys):
        """When all manifest decisions have action=skip and all_moved=True,
        output must contain ERROR and name the missing ticket."""
        from services.sprint_manager.sprint_manager import run_sprint_preflight
        from services.sprint_manager.alerts import AlertMode
        from services.sprint_manager.config import SprintConfig

        cfg = SprintConfig(
            repo_name=_FAKE_REPO,
            sprints_dir=tmp_path,
            worktree_coder=tmp_path,
            worktree_tester=tmp_path,
        )

        manifest = {
            "all_moved": True,
            "decisions": [
                {"issue_num": 49, "issue_title": "Issue 49", "action": "skip"},
            ],
        }

        sm = "services.sprint_manager.sprint_manager"
        with (
            patch(f"{sm}.list_backlog_issues", return_value=[]),
            patch(f"{sm}.get_effective_llm_provider", return_value="anthropic"),
            patch(f"{sm}.structured_log"),
            patch(f"{sm}.mint_run_id", return_value="run-test"),
        ):
            result = run_sprint_preflight(
                label="sprint-4.1",
                alert_modes=[AlertMode.DASHBOARD_BANNER],
                repo_name=_FAKE_REPO,
                cfg=cfg,
                rerun_manifest=manifest,
                dry_run=True,
            )

        captured = capsys.readouterr()
        assert result.early_exit, "Should early-exit when no dispatchable tickets"
        assert "ERROR" in captured.out, (
            "When all_moved=True manifest leads to 0 dispatchable tickets, "
            "output must contain 'ERROR' (not just a quiet 'all skipped' message). "
            f"Got stdout: {captured.out!r}"
        )
        assert "#49" in captured.out, (
            f"Error must name the missing ticket number #49. Got: {captured.out!r}"
        )

    def test_all_moved_with_all_skip_sets_rerun_dispatch_error(self, tmp_path):
        """The early-exit result has rerun_dispatch_error=True for downstream
        callers (e.g. main()) to set plan.json to needs_rework not draft."""
        from services.sprint_manager.sprint_manager import run_sprint_preflight
        from services.sprint_manager.alerts import AlertMode
        from services.sprint_manager.config import SprintConfig

        cfg = SprintConfig(
            repo_name=_FAKE_REPO,
            sprints_dir=tmp_path,
            worktree_coder=tmp_path,
            worktree_tester=tmp_path,
        )

        manifest = {
            "all_moved": True,
            "decisions": [
                {"issue_num": 49, "issue_title": "Issue 49", "action": "skip"},
            ],
        }

        sm = "services.sprint_manager.sprint_manager"
        with (
            patch(f"{sm}.list_backlog_issues", return_value=[]),
            patch(f"{sm}.get_effective_llm_provider", return_value="anthropic"),
            patch(f"{sm}.structured_log"),
            patch(f"{sm}.mint_run_id", return_value="run-test"),
        ):
            result = run_sprint_preflight(
                label="sprint-4.1",
                alert_modes=[AlertMode.DASHBOARD_BANNER],
                repo_name=_FAKE_REPO,
                cfg=cfg,
                rerun_manifest=manifest,
                dry_run=True,
            )

        assert result.early_exit
        assert getattr(result, "rerun_dispatch_error", False), (
            "Early-exit result must carry rerun_dispatch_error=True when "
            "all_moved manifest produces 0 dispatchable tickets. "
            "This signals main() to set plan state to needs_rework, not draft."
        )
