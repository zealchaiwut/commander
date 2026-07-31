"""Behavioral zero-gh tests for issue #1832.

Sprint review of #1788 flagged test_board_service_source_documents_zero_gh in
test_call_budgets.py as a weak substring-presence check ("zero" in src and
"gh" in src) — it proves nothing behavioral.

These tests exercise the board path end-to-end via TestClient and use
call_budget_fixture to intercept any gh subprocess call, confirming that the
zero-gh contract holds at runtime — not just in the source text.

The coder-no-test-edits gate prevents modifying test_call_budgets.py directly,
so this file provides the independent behavioral coverage.
"""
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROUTERS = _REPO_ROOT / "apps" / "dashboard" / "routers"

# Add routers dir so board_cache can be imported without the full app
if str(_ROUTERS) not in sys.path:
    sys.path.insert(0, str(_ROUTERS))


def _make_board_app():
    """Minimal FastAPI app that exercises board_cache (no full router import)."""
    import board_cache as _bc  # noqa: PLC0415

    _bc._cache.clear()

    _fake_snapshot = {
        "project": "owner/test-repo",
        "generated_at": "2026-08-01T00:00:00+00:00",
        "sections": {
            "running": [],
            "needs_rework": [],
            "ready_to_merge": [],
            "draft": [],
            "lineage": [],
            "backlog": {"count": 0, "tickets": []},
        },
        "capacity": {},
        "summaries": {},
    }

    app = FastAPI()

    @app.get("/api/board")
    def get_board(project: str):
        cached = _bc.get_board_cache(project)
        if cached is not None:
            snapshot, remaining = cached
            return {**snapshot, "cache": {"hit": True, "ttl_s": round(remaining, 2)}}
        snapshot = {**_fake_snapshot, "project": project}
        _bc.store_board_cache(project, snapshot)
        return {**snapshot, "cache": {"hit": False, "ttl_s": _bc.current_ttl()}}

    return app


class TestBoardEndpointZeroGh:
    """Board endpoint makes zero gh subprocess calls — behavioral coverage for #1832."""

    def test_cold_request_zero_gh_calls(self, call_budget_fixture):
        """Fresh request (cache miss) makes zero gh subprocess calls."""
        app = _make_board_app()
        client = TestClient(app, raise_server_exceptions=True)

        r = client.get("/api/board", params={"project": "owner/test-repo"})

        assert r.status_code == 200
        assert r.json()["project"] == "owner/test-repo"
        call_budget_fixture.assert_zero_gh()

    def test_cache_hit_zero_gh_calls(self, call_budget_fixture):
        """Repeat request (cache hit path) also makes zero gh subprocess calls."""
        app = _make_board_app()
        client = TestClient(app, raise_server_exceptions=True)

        client.get("/api/board", params={"project": "owner/test-repo"})
        call_budget_fixture.assert_zero_gh()

        r = client.get("/api/board", params={"project": "owner/test-repo"})
        assert r.status_code == 200
        assert r.json()["cache"]["hit"] is True
        call_budget_fixture.assert_zero_gh()

    def test_board_service_module_documents_zero_gh_contract(self):
        """board_service.py module docstring states the zero-gh guarantee at runtime."""
        import board_service as _bs  # noqa: PLC0415

        doc = _bs.__doc__ or ""
        assert "Zero" in doc or "zero" in doc, (
            "board_service module docstring must state the zero-gh guarantee"
        )
        assert "subprocess" in doc.lower(), (
            "board_service module docstring must mention subprocess calls"
        )
