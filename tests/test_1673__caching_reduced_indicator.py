"""Tests for issue #1673: Show 'caching: reduced' indicator when provider is ICA.

AC items verified:
  AC1  When provider === 'ICA', the run/cost view data includes provider='ICA'
       so the frontend can render a `caching: reduced` badge; informational only
  AC2  The indicator (provider field) is absent / None when provider is native
       Anthropic or any non-ICA provider
  AC3  The tooltip text "Prompt caching is unavailable through the ICA proxy"
       is present in the run_browser.html template
  AC4  record_agent_start() accepts provider= as an optional kwarg; existing
       call sites that omit it continue to work (backward compat / no loop changes)
  AC5  provider is included in both list_runs() (history view) and IssueState
       (active-run live snapshot)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SM_DIR = REPO_ROOT / "services" / "sprint_manager"
STATIC_DIR = DASHBOARD_DIR / "static"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(SM_DIR))

os.environ.setdefault("DB_PATH", str(REPO_ROOT / "commander.db"))
os.environ.setdefault("COMMANDER_DISABLE_NEON", "1")


# ── AC4: backward-compat — record_agent_start() accepts provider as optional kwarg ──

class TestAC4BackwardCompat:
    """record_agent_start() must accept provider= without breaking existing callers."""

    def test_record_agent_start_accepts_provider_kwarg(self, tmp_path):
        """AC4: record_agent_start(provider='ICA') works without raising."""
        import db as _db
        db_file = tmp_path / "test.db"
        import importlib
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            run_id = _db.record_agent_start(
                issue_number=1,
                sprint_label="sprint-99",
                agent="coder",
                provider="ICA",
            )
            assert run_id is not None
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)

    def test_record_agent_start_without_provider_still_works(self, tmp_path):
        """AC4: existing callers that omit provider= are not broken."""
        import db as _db
        import importlib
        db_file = tmp_path / "test.db"
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            run_id = _db.record_agent_start(
                issue_number=2,
                sprint_label="sprint-99",
                agent="tester",
            )
            assert run_id is not None
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)


# ── AC1: provider='ICA' is stored in DB and returned by list_runs ────────────

class TestAC1ProviderStoredAndReturned:
    """provider='ICA' is persisted in agent_runs and appears in list_runs() response."""

    def _make_db_with_run(self, db_file: Path, provider: str | None = None) -> None:
        import db as _db
        import importlib
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            _db.record_agent_start(
                issue_number=10,
                sprint_label="sprint-test-ica",
                agent="coder",
                provider=provider,
            )
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)

    def test_provider_ica_returned_in_list_runs(self, tmp_path):
        """AC1: list_runs() includes provider='ICA' for an ICA-dispatched agent run."""
        import db as _db
        import importlib
        import importlib.util
        db_file = tmp_path / "ica.db"
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            _db.record_agent_start(
                issue_number=10,
                sprint_label="sprint-test-ica",
                agent="coder",
                provider="ICA",
            )
            # Import runs_service directly (avoid __init__.py chain)
            spec = importlib.util.spec_from_file_location(
                "runs_service",
                DASHBOARD_DIR / "routers" / "runs_service.py",
            )
            rs = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rs)
            runs = rs.list_runs()
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)

        assert len(runs) == 1
        sprint = runs[0]
        assert sprint["sprint"] == "sprint-test-ica"
        tickets = sprint["tickets"]
        assert len(tickets) == 1
        agents = tickets[0]["agents"]
        assert len(agents) == 1
        assert agents[0]["provider"] == "ICA", (
            f"Expected provider='ICA' in agent data, got: {agents[0].get('provider')!r}"
        )

    def test_provider_stored_in_agent_runs_column(self, tmp_path):
        """AC1: the 'provider' column in agent_runs actually stores the value."""
        import db as _db
        import importlib
        db_file = tmp_path / "col.db"
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            _db.record_agent_start(
                issue_number=11,
                sprint_label="sprint-col",
                agent="coder",
                provider="ICA",
            )
            with _db.get_conn() as conn:
                row = conn.execute(
                    "SELECT provider FROM agent_runs WHERE sprint_label='sprint-col'"
                ).fetchone()
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)

        assert row is not None, "agent_runs row not found"
        assert row["provider"] == "ICA", (
            f"Expected 'ICA' stored in provider column, got: {row['provider']!r}"
        )


# ── AC2: non-ICA provider → provider is null / non-ICA in list_runs ──────────

class TestAC2NonICAProviderAbsent:
    """When provider is native Anthropic or None, list_runs() returns None for provider."""

    def _list_runs_from_db(self, tmp_path_label, tmp_path):
        """Helper: load list_runs() directly from file to avoid package import chain."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            f"runs_service_{tmp_path_label}",
            DASHBOARD_DIR / "routers" / "runs_service.py",
        )
        rs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rs)
        return rs.list_runs()

    def test_provider_none_when_not_set(self, tmp_path):
        """AC2: provider field is None when no provider is passed to record_agent_start."""
        import db as _db
        import importlib
        db_file = tmp_path / "native.db"
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            _db.record_agent_start(
                issue_number=20,
                sprint_label="sprint-native",
                agent="coder",
            )
            runs = self._list_runs_from_db("native", tmp_path)
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)

        agents = runs[0]["tickets"][0]["agents"]
        assert agents[0].get("provider") is None, (
            f"Expected provider=None for native run, got: {agents[0].get('provider')!r}"
        )

    def test_provider_anthropic_not_shown_as_ica(self, tmp_path):
        """AC2: provider='anthropic' is returned as-is (not ICA), no caching badge needed."""
        import db as _db
        import importlib
        db_file = tmp_path / "anthro.db"
        original_path = os.environ.get("DB_PATH")
        try:
            os.environ["DB_PATH"] = str(db_file)
            importlib.reload(_db)
            _db.record_agent_start(
                issue_number=21,
                sprint_label="sprint-anthro",
                agent="coder",
                provider="anthropic",
            )
            runs = self._list_runs_from_db("anthro", tmp_path)
        finally:
            if original_path is not None:
                os.environ["DB_PATH"] = original_path
            importlib.reload(_db)

        agents = runs[0]["tickets"][0]["agents"]
        prov = agents[0].get("provider")
        assert prov != "ICA", (
            f"Native anthropic provider must not appear as 'ICA'; got: {prov!r}"
        )


# ── AC3: tooltip text present in the HTML templates ──────────────────────────

class TestAC3TooltipText:
    """The caching-unavailable tooltip text is present in the run_browser.html."""

    def test_run_browser_html_contains_tooltip(self):
        """AC3: run_browser.html has text about ICA proxy prompt caching unavailability."""
        html_path = STATIC_DIR / "run_browser.html"
        assert html_path.exists(), "run_browser.html must exist"
        content = html_path.read_text(encoding="utf-8")
        assert "ICA proxy" in content or "ica proxy" in content.lower(), (
            "run_browser.html should mention 'ICA proxy' in tooltip/note text"
        )
        assert "caching" in content.lower(), (
            "run_browser.html should mention caching in the indicator context"
        )

    def test_project_html_contains_caching_reduced_badge(self):
        """AC3/AC5: project.html has the caching:reduced badge for the active run view."""
        html_path = STATIC_DIR / "project.html"
        assert html_path.exists(), "project.html must exist"
        content = html_path.read_text(encoding="utf-8")
        assert "caching" in content.lower() and "reduced" in content.lower(), (
            "project.html should include caching:reduced indicator for ICA runs"
        )


# ── AC5: IssueState includes coder_provider for active run view ───────────────

class TestAC5IssueStateCarriesProvider:
    """IssueState has coder_provider; the live snapshot exposes it per issue."""

    def test_issue_state_has_coder_provider_field(self):
        """AC5: IssueState dataclass has a coder_provider attribute."""
        from services.sprint_manager.state import IssueState
        iss = IssueState(number=1, title="test")
        assert hasattr(iss, "coder_provider"), (
            "IssueState must have a coder_provider field for the active-run live snapshot"
        )

    def test_issue_state_coder_provider_default_none(self):
        """AC5: coder_provider defaults to None when not set."""
        from services.sprint_manager.state import IssueState
        iss = IssueState(number=1, title="test")
        assert iss.coder_provider is None

    def test_issue_state_to_dict_includes_coder_provider(self):
        """AC5: IssueState.to_dict() includes coder_provider so state.json carries it."""
        from services.sprint_manager.state import IssueState
        iss = IssueState(number=1, title="test")
        iss.coder_provider = "ICA"
        d = iss.to_dict()
        assert "coder_provider" in d, "to_dict() must include coder_provider"
        assert d["coder_provider"] == "ICA"

    def test_issue_state_from_dict_restores_coder_provider(self):
        """AC5: IssueState.from_dict() restores coder_provider from saved state.json."""
        from services.sprint_manager.state import IssueState
        iss = IssueState.from_dict({
            "number": 1,
            "title": "test",
            "coder_provider": "ICA",
        })
        assert iss.coder_provider == "ICA", (
            "from_dict() must restore coder_provider from the serialized dict"
        )

    def test_issue_state_from_dict_missing_key_yields_none(self):
        """AC5: IssueState.from_dict() with no coder_provider key defaults to None."""
        from services.sprint_manager.state import IssueState
        iss = IssueState.from_dict({"number": 1, "title": "test"})
        assert iss.coder_provider is None

    def test_live_snapshot_includes_coder_provider(self):
        """AC5: coder_provider is included in the live-snapshot issue dict.

        The live snapshot is built by sprint_live.py from the state JSON.
        We verify that the key 'coder_provider' appears in the issues_out dict
        by checking that sprint_live.py references it (architectural contract).
        """
        sprint_live_path = DASHBOARD_DIR / "routers" / "sprint_live.py"
        assert sprint_live_path.exists()
        content = sprint_live_path.read_text(encoding="utf-8")
        assert "coder_provider" in content, (
            "sprint_live.py must include coder_provider in the per-issue live snapshot dict"
        )
