"""Tests for issue #672: Fix API Error on Finish Card Submission.

Root cause: GET /api/sprints/{sprint_label}/finish-card returned HTTP 404
for sprints that had never been run (no state.json file). This 404 surfaced
as a network error in the browser DevTools Network tab on every page load.
Additionally, the frontend silently swallowed errors from this endpoint
instead of surfacing them to the user.

AC coverage:
  AC1 — No 4xx or 5xx in Network tab under normal conditions:
         finish-card returns 200 (not 404) when sprint has no state.json
  AC2 — Finish card action returns 2xx from the API
  AC3 — API errors are surfaced to the user, not swallowed silently
  AC4 — Card state correctly updated in UI after successful finish
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
PROJECT_HTML = DASHBOARD_DIR / "static" / "project.html"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_state_json(tmp: Path, sprint_n: int, issues: list[dict] | None = None) -> Path:
    """Write a minimal sprint-N-state.json so the endpoint has data to return."""
    sprints_dir = tmp / ".commander" / "sprints"
    sprints_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "sprint_label": f"sprint-{sprint_n}",
        "sprint_number": sprint_n,
        "project": "owner/repo",
        "start_timestamp": "2026-01-01T10:00:00Z",
        "wall_clock_secs": 600.0,
        "issues": issues or [],
    }
    path = sprints_dir / f"sprint-{sprint_n}-state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return tmp


def _make_client(tmp: Path, repo: str = "owner/repo"):
    """Yield a TestClient with _project_root_path and load_projects patched."""
    for mod in list(sys.modules.keys()):
        if mod == "server" or mod.startswith("server."):
            del sys.modules[mod]
    import server as srv

    slug = repo.split("/")[-1]

    def fake_root(r: str) -> Path:
        return tmp / r.split("/")[-1]

    mock_gh = MagicMock()
    mock_gh.get_repo_for_operation.return_value = repo

    from fastapi.testclient import TestClient
    with (
        patch.object(srv.projects_module, "load_projects", return_value=[{"repo": repo}]),
        patch("server._project_root_path", side_effect=fake_root),
        patch("server._is_sprint_running", return_value=False),
        patch("server._has_rework_tickets", return_value=False),
        patch("server._count_rework_tickets", return_value=0),
    ):
        client = TestClient(srv.app, raise_server_exceptions=False)
        yield client, srv


# ── AC1 + AC2: finish-card returns 200, not 404, for unrun sprints ────────────

class TestFinishCardReturns200:
    """AC1: No 4xx in Network tab for sprints that have never been run.
       AC2: finish-card endpoint returns 2xx response.
    """

    def test_finish_card_returns_200_when_no_state_json(self, tmp_path):
        """No state.json exists → must return 200, not 404."""
        project_dir = tmp_path / "repo"
        project_dir.mkdir(parents=True)

        for client, srv in _make_client(tmp_path):
            resp = client.get(
                "/api/sprints/sprint-99/finish-card",
                params={"project": "owner/repo"},
            )
        assert resp.status_code == 200, (
            f"Expected 200 but got {resp.status_code}. "
            "finish-card must not return 404 for sprints that have never been run."
        )

    def test_finish_card_no_state_json_returns_no_data_state(self, tmp_path):
        """When state.json is absent, response body must signal no-data state."""
        project_dir = tmp_path / "repo"
        project_dir.mkdir(parents=True)

        for client, srv in _make_client(tmp_path):
            resp = client.get(
                "/api/sprints/sprint-99/finish-card",
                params={"project": "owner/repo"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("state") == "no_data", (
            f"Expected state='no_data' but got state={data.get('state')!r}. "
            "Unrun sprint finish-card must return state='no_data' instead of raising 404."
        )

    def test_finish_card_returns_200_with_state_json(self, tmp_path):
        """When state.json exists, finish-card returns 200 with sprint data."""
        project_dir = tmp_path / "repo"
        project_dir.mkdir(parents=True)
        _make_state_json(tmp_path / "repo", 10, issues=[
            {"status": "done", "number": 1, "title": "T1"},
        ])

        for client, srv in _make_client(tmp_path / "repo"):
            with patch("server._project_root_path", return_value=tmp_path / "repo"):
                resp = client.get(
                    "/api/sprints/sprint-10/finish-card",
                    params={"project": "owner/repo"},
                )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("state") in ("completed", "has_rework", "cancelled", "running", "no_data"), (
            f"Unexpected state: {data.get('state')!r}"
        )

    def test_finish_card_invalid_label_returns_400(self, tmp_path):
        """Invalid sprint label must return 400 — existing guard stays intact."""
        for client, srv in _make_client(tmp_path):
            resp = client.get(
                "/api/sprints/not-a-sprint/finish-card",
                params={"project": "owner/repo"},
            )
        assert resp.status_code == 400, (
            "Invalid sprint label must still return 400"
        )

    def test_finish_card_no_data_includes_sprint_label(self, tmp_path):
        """no_data response must include sprint_label and sprint_number."""
        project_dir = tmp_path / "repo"
        project_dir.mkdir(parents=True)

        for client, srv in _make_client(tmp_path):
            resp = client.get(
                "/api/sprints/sprint-77/finish-card",
                params={"project": "owner/repo"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "sprint_label" in data, "no_data response must include sprint_label"
        assert "sprint_number" in data, "no_data response must include sprint_number"
        assert data["sprint_label"] == "sprint-77"
        assert data["sprint_number"] == 77


# ── AC3: errors surfaced to user, not swallowed silently ─────────────────────

class TestFrontendErrorSurfacing:
    """AC3: API errors are surfaced to user rather than failing silently."""

    def test_load_finish_cards_does_not_silently_swallow_errors(self):
        """_smgmtLoadFinishCards must not have a bare `catch (_) { /* silent */ }`.

        The silent catch prevents genuine server errors from being visible.
        It must be replaced with error logging or user-visible feedback.
        """
        html = PROJECT_HTML.read_text(encoding="utf-8")
        fn_start = html.find("async function _smgmtLoadFinishCards(")
        assert fn_start != -1, "_smgmtLoadFinishCards function not found"
        fn_end = html.find("\nasync function ", fn_start + 1)
        fn_body = html[fn_start:fn_end if fn_end > 0 else fn_start + 3000]
        assert 'catch (_) { /* silent */' not in fn_body, (
            "_smgmtLoadFinishCards still has 'catch (_) { /* silent */ }' — "
            "server errors from finish-card are silently swallowed instead of surfaced."
        )
        # Must log or handle errors meaningfully
        assert 'console.warn' in fn_body or 'console.error' in fn_body, (
            "_smgmtLoadFinishCards must log errors (console.warn/error) instead of silently ignoring them."
        )

    def test_fs_confirm_parses_json_error_detail(self):
        """_fsConfirm must extract the 'detail' field from JSON error responses
        so the user sees the actual error message, not raw JSON.
        """
        html = PROJECT_HTML.read_text(encoding="utf-8")
        # The confirmation handler must parse JSON errors
        assert (
            "errorJson.detail" in html or
            "err.detail" in html or
            "json.detail" in html or
            "_parseApiError" in html or
            "parseApiError" in html
        ), (
            "_fsConfirm must parse JSON error bodies to extract 'detail' field "
            "for meaningful user-facing error messages."
        )

    def test_finish_sprint_preview_error_parsing(self):
        """smgmtFinishSprint must extract meaningful message from API error responses."""
        html = PROJECT_HTML.read_text(encoding="utf-8")
        # smgmtFinishSprint shows error to user — must parse detail
        assert (
            "errorJson.detail" in html or
            "err.detail" in html or
            "_parseApiError" in html or
            "parseApiError" in html
        ), (
            "smgmtFinishSprint must parse JSON error body to extract 'detail' field."
        )


# ── AC4: card state updated in UI after successful finish ─────────────────────

class TestCardStateUpdatedAfterFinish:
    """AC4: Card state is correctly updated in the UI after a successful finish."""

    def test_load_finish_cards_called_after_finish_sprint(self):
        """After _fsConfirm succeeds, loadSprintMgmt() is called.
        loadSprintMgmt must call _smgmtLoadFinishCards so finish-card data refreshes.
        """
        html = PROJECT_HTML.read_text(encoding="utf-8")
        # Verify _smgmtLoadFinishCards is called inside loadSprintMgmt
        # Find the loadSprintMgmt function body and check for the call
        load_idx = html.find("async function loadSprintMgmt(")
        assert load_idx != -1, "loadSprintMgmt function not found"
        # Check that _smgmtLoadFinishCards is called in or after loadSprintMgmt
        next_fn_idx = html.find("\nasync function ", load_idx + 1)
        fn_body = html[load_idx:next_fn_idx if next_fn_idx > 0 else load_idx + 5000]
        assert "_smgmtLoadFinishCards" in fn_body, (
            "_smgmtLoadFinishCards() must be called inside loadSprintMgmt() "
            "so finish cards refresh after a sprint is finished."
        )

    def test_finish_card_no_data_state_not_rendered_in_ui(self):
        """When finish-card returns state='no_data', the finish card must not be shown.

        The frontend must check for 'no_data' state and skip rendering.
        """
        html = PROJECT_HTML.read_text(encoding="utf-8")
        # The render function must handle no_data
        assert "no_data" in html, (
            "_smgmtLoadFinishCards or _smgmtRenderFinishCard must check for "
            "state === 'no_data' to avoid rendering empty finish cards."
        )

    def test_post_finish_returns_2xx_on_success(self, tmp_path):
        """POST /api/projects/{owner}/{repo}/sprints/{label}/finish returns 200 on success."""
        project_dir = tmp_path / "repo"
        (project_dir / ".commander" / "sprints").mkdir(parents=True)

        for mod in list(sys.modules.keys()):
            if mod == "server" or mod.startswith("server."):
                del sys.modules[mod]
        import server as srv

        def fake_root(r: str) -> Path:
            return tmp_path / r.split("/")[-1]

        mock_issues = [
            {"number": 1, "title": "T1", "labels": [{"name": "sprint-5"}, {"name": "UAT"}]},
        ]

        from fastapi.testclient import TestClient
        with (
            patch.object(srv.projects_module, "load_projects", return_value=[{"repo": "owner/repo"}]),
            patch("server._project_root_path", side_effect=fake_root),
            patch("server._is_sprint_running", return_value=False),
            patch("server._get_sprint_issues", return_value=mock_issues),
            patch.object(srv.github_client, "close_issue", return_value=None),
            patch.object(srv.github_client, "update_labels", return_value=None),
            patch.object(srv.github_client, "invalidate", return_value=None),
        ):
            client = TestClient(srv.app, raise_server_exceptions=False)
            resp = client.post(
                "/api/projects/owner/repo/sprints/sprint-5/finish",
                json={"confirmed": True, "selected_ticket_numbers": [1]},
            )
        assert 200 <= resp.status_code <= 299, (
            f"POST finish must return 2xx, got {resp.status_code}: {resp.text}"
        )
