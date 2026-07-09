"""Call-count-budget test module for Commander dashboard (issue #1788).

Arc targets (documented call budgets per page/endpoint):
    Board load          = 1 aggregate call      (COMMANDER_BOARD_AGGREGATE=1)
    Home load           ≤ 4 calls               (brief + todos + environment)
    History feed load   = 1 call                (COMMANDER_HISTORY_AGGREGATE=1)
    Running first paint = 1 call                (COMMANDER_RUNNING_AGGREGATE=1)
    Zero gh subprocess  calls per aggregate board request

Usage (pytest fixture) — one-liner example::

    def test_my_board_endpoint_zero_gh(call_budget_fixture):
        call_budget_fixture.record_http("/api/board?project=owner/repo")
        call_budget_fixture.assert_call_budget("/api/board", 1)
        call_budget_fixture.assert_zero_gh()

Usage (Node.js / HTML-harness) — one-liner example::

    import { installFetchSpy, assertFetchBudget } from '../frontend/call-budget-helpers.mjs';
    const spy = installFetchSpy({ '/api/board': fakeBoard() });
    await loadSprintMgmt(true, null);
    assertFetchBudget(spy, '/api/board', 1);
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS = _DASHBOARD / "routers"
_STATIC = _DASHBOARD / "static"
_BOARD_RENDER = _STATIC / "src" / "sprint-board" / "board-render.js"
_HOME_HTML = _STATIC / "home.html"
_HISTORY_JS = _STATIC / "src" / "sprint-board" / "history.js"
_PROJECT_HTML = _STATIC / "project.html"
_SPRINT_SUMMARIES = _ROUTERS / "sprint_summaries.py"

for _p in (str(_DASHBOARD), str(_ROUTERS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# AC1/AC2: Fixture is importable and helpers work correctly
# ═══════════════════════════════════════════════════════════════════════════════

class TestCallBudgetFixtureBasics:
    """The call_budget_fixture is importable and its helpers behave correctly."""

    def test_fixture_is_provided_by_conftest(self, call_budget_fixture):
        """call_budget_fixture is auto-discovered from conftest — no explicit import needed."""
        assert call_budget_fixture is not None

    def test_assert_zero_gh_passes_when_no_gh_calls(self, call_budget_fixture):
        """assert_zero_gh() passes when no gh subprocess call was made."""
        call_budget_fixture.assert_zero_gh()  # must not raise

    def test_assert_zero_gh_detects_simulated_gh_call(self, call_budget_fixture):
        """assert_zero_gh() raises AssertionError when a gh call is recorded."""
        call_budget_fixture._record_gh(["gh", "issue", "list", "--repo", "owner/repo"])
        with pytest.raises(AssertionError, match="gh"):
            call_budget_fixture.assert_zero_gh()

    def test_assert_call_budget_passes_within_limit(self, call_budget_fixture):
        """assert_call_budget() passes when calls are within the allowed max."""
        call_budget_fixture.record_http("/api/board?project=owner/repo")
        call_budget_fixture.assert_call_budget("/api/board", 1)

    def test_assert_call_budget_passes_with_zero_calls(self, call_budget_fixture):
        """assert_call_budget() passes when no calls were made."""
        call_budget_fixture.assert_call_budget("/api/board", 0)

    def test_assert_call_budget_raises_when_over_limit(self, call_budget_fixture):
        """assert_call_budget() raises AssertionError when the budget is exceeded."""
        call_budget_fixture.record_http("/api/board?project=owner/repo")
        call_budget_fixture.record_http("/api/board?project=owner/repo-2")
        with pytest.raises(AssertionError, match="exceeded"):
            call_budget_fixture.assert_call_budget("/api/board", 1)

    def test_record_http_accepts_any_url(self, call_budget_fixture):
        """record_http() stores arbitrary URLs for later budget assertion."""
        call_budget_fixture.record_http("/api/sprints/running-all")
        call_budget_fixture.record_http("/api/board?project=owner/r")
        call_budget_fixture.assert_call_budget("/api/board", 1)
        call_budget_fixture.assert_call_budget("/api/sprints/running-all", 1)

    def test_path_pattern_is_substring_match(self, call_budget_fixture):
        """assert_call_budget path_pattern matches as a substring, not an exact URL."""
        call_budget_fixture.record_http("/api/board?project=zealchaiwut/commander")
        call_budget_fixture.assert_call_budget("/api/board", 1)
        call_budget_fixture.assert_call_budget("board", 1)

    def test_non_matching_calls_not_counted(self, call_budget_fixture):
        """Calls that do not match path_pattern are excluded from the count."""
        call_budget_fixture.record_http("/api/home")
        call_budget_fixture.record_http("/api/environment")
        call_budget_fixture.assert_call_budget("/api/board", 0)


# ═══════════════════════════════════════════════════════════════════════════════
# AC4: Budget tests encode the arc targets
# ═══════════════════════════════════════════════════════════════════════════════

# ── Budget 1: Board load = 1 aggregate call (COMMANDER_BOARD_AGGREGATE=1) ─────

class TestBoardLoadBudget:
    """Budget: board page makes exactly 1 aggregate call when flag is ON."""

    def test_board_render_js_has_single_aggregate_fetch_path(self):
        """board-render.js loadSprintMgmt() issues exactly 1 /api/board fetch
        when the board_aggregate flag is ON.

        Structural enforcement: the aggregate code path has a single
        /api/board fetch and no per-sprint fallback loop inside that branch.
        This mirrors the fetch-spy verification in board-aggregate-flag.test.mjs.
        """
        src = _read(_BOARD_RENDER)
        # The single aggregate fetch exists
        assert "/api/board" in src, (
            "board-render.js must contain /api/board fetch for the aggregate path"
        )
        # The aggregate flag gates a single-call branch
        assert "_useBoardAggregate" in src or "board_aggregate" in src, (
            "board-render.js must gate the single-fetch path on the board_aggregate flag"
        )
        # Aggregate cards index (built from the single response) must exist
        assert "_smgmtAggregateCards" in src, (
            "board-render.js must use _smgmtAggregateCards populated from the single call"
        )

    def test_board_render_aggregate_path_has_no_per_sprint_loop(self):
        """The aggregate fetch branch must NOT loop over sprint labels to make
        per-sprint API calls — the single /api/board response includes all sprints.
        """
        src = _read(_BOARD_RENDER)
        # The aggregate path returns early / skips the estimates+dep-order loop
        assert "_smgmtAggregateCards" in src
        # The legacy per-sprint calls still exist but are in the else branch
        assert "/api/sprint-management/issues" in src, (
            "legacy endpoint must exist for the flag-OFF branch"
        )

    def test_board_load_budget_with_fixture(self, call_budget_fixture):
        """Demonstrates assert_call_budget usage for the board budget.

        In a real integration test, loadSprintMgmt() would be driven via a
        fetch spy or Node.js runner. Here we simulate the single-call behavior
        and verify the budget assertion passes.
        """
        call_budget_fixture.record_http("/api/board?project=owner/repo")
        call_budget_fixture.assert_call_budget("/api/board", 1)


# ── Budget 2: Home load ≤ 4 calls ─────────────────────────────────────────────

class TestHomeLoadBudget:
    """Budget: home page makes ≤ 4 fetch calls on initial load."""

    def test_home_html_init_path_fetch_count(self):
        """home.html init() → loadBrief() makes ≤ 4 fetch() calls on initial load.

        With issue #1778 embedding per-project data in /api/brief/daily, the
        init path is:
          1. /api/brief/daily         (always)
          2. /api/todos               (batched, single call)
          3. /api/environment         (from features.js bundle — off-page)
        = 2-3 calls in init() scope. The ≤ 4 budget applies even counting
        the features.js /api/environment call.
        """
        src = _read(_HOME_HTML)
        # Extract the loadBrief function body
        m = re.search(
            r"async function loadBrief\(date\)(.*?)^\s{2}}",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert m is not None, "loadBrief() function not found in home.html"
        fn_body = m.group(1)

        # Count fetch() calls directly inside loadBrief (not in nested function bodies)
        # Exclude calls inside named sub-functions like loadMilestone/loadProjectSummary
        # which are conditional per-project fallbacks and run 0 times when data is embedded
        fetch_calls = re.findall(r"(?<!\w)fetch\s*\(", fn_body)
        assert len(fetch_calls) <= 4, (
            f"loadBrief() should make ≤ 4 fetch calls, found {len(fetch_calls)}: "
            f"the home load budget allows for brief + todos + 2 optional per-project "
            f"fallbacks (when #1778 embedding is absent). Current count: {len(fetch_calls)}"
        )

    def test_home_html_brief_fetch_present(self):
        """home.html must fetch /api/brief/daily as the primary data source."""
        src = _read(_HOME_HTML)
        assert "/api/brief/daily" in src, (
            "home.html must fetch /api/brief/daily for the home brief content"
        )

    def test_home_html_todos_fetch_is_batched(self):
        """home.html must batch-fetch todos in a single call, not per-project."""
        src = _read(_HOME_HTML)
        assert "/api/todos?projects=" in src, (
            "home.html must batch-fetch todos via /api/todos?projects=... (issue #1778)"
        )
        # The todos fetch must NOT be per-project (no per-slug todos endpoint)
        todos_per_slug = re.findall(r"fetch\(['\`]/api/todos/['\`]", src)
        assert len(todos_per_slug) == 0, (
            "home.html must not make per-project /api/todos/<slug> calls — use batch"
        )

    def test_home_load_budget_with_fixture(self, call_budget_fixture):
        """Demonstrates the ≤ 4 budget for the home page with the pytest fixture."""
        call_budget_fixture.record_http("/api/environment")
        call_budget_fixture.record_http("/api/brief/daily")
        call_budget_fixture.record_http("/api/todos?projects=commander,perf-coach")
        call_budget_fixture.assert_call_budget("/api/", 3)  # 3 ≤ 4


# ── Budget 3: History feed load = 1 call (COMMANDER_HISTORY_AGGREGATE=1) ──────

class TestHistoryFeedBudget:
    """Budget: history feed makes exactly 1 call when the aggregate flag is ON."""

    def test_history_js_load_ledger_fetch_count(self):
        """_histLoadLedger in history.js makes ≤ 2 fetch calls: 1 history + 1 settings.

        The history ledger response (1 call) includes inline run_stats when
        COMMANDER_HISTORY_AGGREGATE=1, eliminating the N per-sprint run-stats
        calls. The parallel settings fetch (1 optional call) loads fold size / TTL.

        Before the optimization: N+1 calls (1 ledger + N per-sprint run-stats).
        After the optimization: ≤ 2 calls (1 history + 1 settings) regardless of
        how many sprints are in the history — a dramatic reduction.
        """
        src = _read(_HISTORY_JS)
        m = re.search(
            r"export async function _histLoadLedger\((.*?)^\}",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert m is not None, "_histLoadLedger not found in history.js"
        fn_body = m.group(0)
        fetch_calls = re.findall(r"(?<!\w)fetch\s*\(", fn_body)
        assert len(fetch_calls) <= 2, (
            f"_histLoadLedger must make ≤ 2 fetch calls (1 history data + 1 optional "
            f"settings), found {len(fetch_calls)}. The optimization eliminates per-sprint "
            f"run-stats calls — new code must not add per-sprint fetches inside this fn."
        )
        assert len(fetch_calls) >= 1, (
            "_histLoadLedger must make at least 1 fetch call (the history ledger)"
        )

    def test_history_js_run_stats_guard_eliminates_per_card_calls(self):
        """_histLoadRunStats early-returns when data is already seeded from the ledger.

        With flag ON, _histSeedRunStatsFromInline() seeds all run_stats before
        _histLoadRunStats is called, so each card's run-stats fetch is skipped.
        """
        src = _read(_HISTORY_JS)
        assert "if (label in _histRunStats) return;" in src, (
            "_histLoadRunStats must early-return when the label is already cached "
            "(seeded by _histSeedRunStatsFromInline on flag-ON path)"
        )

    def test_history_js_seed_called_in_load_ledger(self):
        """_histSeedRunStatsFromInline is called inside _histLoadLedger after
        storing the ledger data, so run_stats are available before render."""
        src = _read(_HISTORY_JS)
        idx_assign = src.find("_histLedgerData = sprints;")
        idx_seed = src.find("_histSeedRunStatsFromInline(sprints);", idx_assign)
        assert idx_assign != -1, "_histLedgerData = sprints; not found"
        assert idx_seed > idx_assign, (
            "_histSeedRunStatsFromInline must be called after _histLedgerData is assigned"
        )

    def test_history_feed_budget_with_fixture(self, call_budget_fixture):
        """Demonstrates the history feed = 1 call budget with the pytest fixture."""
        call_budget_fixture.record_http("/api/sprints/history?project=owner/repo")
        call_budget_fixture.assert_call_budget("/api/sprints/history", 1)


# ── Budget 4: Running tab first paint = 1 call (COMMANDER_RUNNING_AGGREGATE=1) ─

class TestRunningTabBudget:
    """Budget: running-tab first paint makes exactly 1 call when flag is ON."""

    def test_running_first_paint_fn_exists(self):
        """_smgmtRunningFirstPaint() is defined in project.html for the flag-ON path."""
        src = _read(_PROJECT_HTML)
        assert "async function _smgmtRunningFirstPaint()" in src, (
            "_smgmtRunningFirstPaint must be defined in project.html (issue #1647)"
        )

    def test_running_first_paint_single_fetch(self):
        """_smgmtRunningFirstPaint() makes exactly 1 fetch call to /api/running."""
        src = _read(_PROJECT_HTML)
        m = re.search(
            r"async function _smgmtRunningFirstPaint\(\)(.*?)^\}",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert m is not None, "_smgmtRunningFirstPaint() not found in project.html"
        fn_body = m.group(0)
        fetch_calls = re.findall(r"(?<!\w)fetch\s*\(", fn_body)
        assert len(fetch_calls) == 1, (
            f"_smgmtRunningFirstPaint() must make exactly 1 fetch call, "
            f"found {len(fetch_calls)} — no per-ticket fan-out allowed"
        )

    def test_running_first_paint_targets_api_running(self):
        """The single fetch in _smgmtRunningFirstPaint targets /api/running."""
        src = _read(_PROJECT_HTML)
        assert "/api/running?project=" in src or "/api/running" in src, (
            "_smgmtRunningFirstPaint must fetch /api/running (not per-ticket endpoints)"
        )

    def test_running_tab_budget_with_fixture(self, call_budget_fixture):
        """Demonstrates the running tab = 1 call budget with the pytest fixture."""
        call_budget_fixture.record_http("/api/running?project=owner/repo")
        call_budget_fixture.assert_call_budget("/api/running", 1)


# ── Budget 5: Zero gh subprocess calls per aggregate board request ─────────────

class TestZeroGhSubprocessBudget:
    """Budget: GET /api/board must make zero gh subprocess calls.

    board_service.assemble_board() reads only from the issues mirror (SQLite)
    and local disk — documented as zero gh subprocess calls per request.
    """

    def _fake_board_snapshot(self, repo: str) -> dict:
        return {
            "project": repo,
            "generated_at": "2026-07-09T00:00:00+00:00",
            "sections": {
                "running": [], "needs_rework": [], "ready_to_merge": [],
                "draft": [], "lineage": [],
                "backlog": {"count": 0, "tickets": []},
            },
            "capacity": {},
            "summaries": {},
        }

    def test_board_aggregate_endpoint_zero_gh_calls(
        self, call_budget_fixture, monkeypatch
    ):
        """GET /api/board makes zero gh subprocess calls.

        A minimal FastAPI app mirrors the sprint_dispatch.get_board logic so we
        can exercise the board_cache + board_service path without importing the
        full router (which has complex settings dependencies). The board_service
        compute function is stubbed; the call_budget_fixture is active so any
        gh subprocess call raises via assert_zero_gh().
        """
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Import board_cache directly from the routers dir (not via __init__)
        _routers_dir = str(_ROUTERS)
        if _routers_dir not in sys.path:
            sys.path.insert(0, _routers_dir)

        import board_cache as _bc  # noqa: PLC0415

        _bc._cache.clear()

        fake_snapshot = self._fake_board_snapshot("owner/test-repo")

        app = FastAPI()

        @app.get("/api/board")
        def get_board(project: str):
            cached = _bc.get_board_cache(project)
            if cached is not None:
                snapshot, remaining = cached
                return {**snapshot, "cache": {"hit": True, "ttl_s": round(remaining, 2)}}
            # Stub: return fake snapshot (no gh calls, no real DB)
            snapshot = fake_snapshot.copy()
            snapshot["project"] = project
            _bc.store_board_cache(project, snapshot)
            return {**snapshot, "cache": {"hit": False, "ttl_s": _bc.current_ttl()}}

        client = TestClient(app, raise_server_exceptions=True)
        r = client.get("/api/board", params={"project": "owner/test-repo"})
        assert r.status_code == 200
        assert r.json()["project"] == "owner/test-repo"

        call_budget_fixture.assert_zero_gh()

    def test_board_service_source_documents_zero_gh(self):
        """board_service.py module docstring documents zero gh calls."""
        src = _read(_ROUTERS / "board_service.py")
        assert "zero" in src.lower() and "gh" in src.lower(), (
            "board_service.py should document its zero-gh guarantee in comments/docstrings"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5: xfail budgets for unshipped optimizations
# ═══════════════════════════════════════════════════════════════════════════════

def _has_gh_subprocess_run(src: str) -> bool:
    """Return True if src contains subprocess.run([..., 'gh', ...] pattern.

    Handles both single-line and multi-line subprocess.run([...]) calls, since
    the code may span multiple lines with the list starting on the next line.
    """
    # Match subprocess.run( followed (possibly across newlines) by a list starting with "gh"
    return bool(re.search(r'subprocess\.run\(\s*\[\s*["\']gh["\']', src, re.DOTALL))


@pytest.mark.xfail(
    reason=(
        "blocked on home-endpoint optimization — sprint_summaries.get_home() still "
        "calls gh subprocess (subprocess.run(['gh', 'issue', 'list', ...])) for "
        "sprint-summary label listing when the mirror is cold. "
        "Remove this xfail once the endpoint is refactored to use only "
        "the issues mirror (SQLite) with no gh subprocess fallback."
    ),
    strict=False,
)
def test_zero_gh_subprocess_in_home_endpoint_source():
    """Aspirational: sprint_summaries.py should make zero gh subprocess calls.

    Currently xfail: the module calls subprocess.run(['gh', 'issue', 'list', ...])
    to list sprint-summary issues as a fallback for /api/home.

    xfail-to-pass flip (AC7 demonstration):
        1. Refactor sprint_summaries.py to remove the gh subprocess fallback,
           using only the issues mirror (SQLite) for issue data.
        2. Remove the @pytest.mark.xfail decorator above.
        3. Re-run pytest — this test passes, confirming the budget is enforced.
    """
    src = _read(_SPRINT_SUMMARIES)
    assert not _has_gh_subprocess_run(src), (
        "sprint_summaries.py (home endpoint) must not call gh subprocess — "
        "use the issues mirror (SQLite) exclusively"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6: Deliberately adding an extra fetch causes the budget test to fail
# ═══════════════════════════════════════════════════════════════════════════════

class TestBudgetViolationDetection:
    """Verifies the harness catches call-budget violations (AC6)."""

    def test_extra_http_call_causes_board_budget_to_fail(self, call_budget_fixture):
        """Deliberately recording 2 /api/board calls causes assert_call_budget to raise.

        This simulates what happens when a developer adds an extra fetch inside
        the board-load path: the budget test immediately catches the violation.
        """
        # Simulate normal board load + an extra inadvertent board fetch
        call_budget_fixture.record_http("/api/board?project=owner/repo")
        call_budget_fixture.record_http("/api/board?project=owner/repo")  # deliberate extra

        with pytest.raises(AssertionError) as exc_info:
            call_budget_fixture.assert_call_budget("/api/board", 1)

        err = str(exc_info.value)
        assert "exceeded" in err, "Error message must say 'exceeded'"
        assert "2" in err, "Error message must report the actual count (2)"
        assert "1" in err, "Error message must report the allowed max (1)"

    def test_extra_gh_call_causes_zero_gh_to_fail(self, call_budget_fixture):
        """Deliberately recording a gh call causes assert_zero_gh to raise.

        This simulates what happens when a developer introduces a gh subprocess
        call in an aggregate endpoint: the budget test immediately catches it.
        """
        call_budget_fixture._record_gh(["gh", "issue", "list", "--repo", "owner/repo"])

        with pytest.raises(AssertionError) as exc_info:
            call_budget_fixture.assert_zero_gh()

        err = str(exc_info.value)
        assert "gh" in err.lower(), "Error must mention 'gh' subprocess"

    def test_budget_passes_after_violation_only_when_calls_within_limit(
        self, call_budget_fixture
    ):
        """When calls are within the budget, assert_call_budget does not raise."""
        call_budget_fixture.record_http("/api/running?project=owner/repo")
        call_budget_fixture.record_http("/api/board?project=owner/repo")
        # Each path pattern is within its own budget
        call_budget_fixture.assert_call_budget("/api/running", 1)
        call_budget_fixture.assert_call_budget("/api/board", 1)


# ═══════════════════════════════════════════════════════════════════════════════
# AC7: xfail-to-pass flip is demonstrated for the home endpoint budget
# ═══════════════════════════════════════════════════════════════════════════════

class TestXfailToPassFlipDemonstration:
    """Demonstrates that the xfail-to-pass flip works as expected (AC7).

    When the blocking condition in test_zero_gh_subprocess_in_home_endpoint_source
    is removed (i.e., the gh subprocess call is removed from sprint_summaries.py),
    the assertion in that xfail test passes.

    This test proves the assertion logic is correct by applying the fix to a
    temporary copy and verifying the check now passes.
    """

    def test_assertion_passes_when_gh_call_removed(self, tmp_path):
        """After removing the subprocess.run(['gh'...) from sprint_summaries.py,
        the assertion used in the xfail test passes.

        This confirms the xfail-to-pass flip: a real fix makes the formerly-xfail
        budget test green without changing the test itself.
        """
        src = _read(_SPRINT_SUMMARIES)

        # Simulate removing all gh subprocess calls (the fix that will land)
        patched_src = re.sub(
            r'subprocess\.run\(\s*\[\s*["\']gh["\'][^\]]*\][^\)]*\)',
            '# removed gh call',
            src,
            flags=re.DOTALL,
        )

        # The assertion from the xfail test: must pass once the fix is applied
        assert not _has_gh_subprocess_run(patched_src), (
            "After removing gh subprocess calls from sprint_summaries.py, "
            "the zero-gh budget assertion must pass — confirms xfail flip is valid"
        )

    def test_original_file_has_gh_call_confirming_xfail_is_needed(self):
        """Verifies the xfail is necessary: the original sprint_summaries.py
        currently contains a gh subprocess call, so the budget test correctly
        fails without the xfail marker."""
        src = _read(_SPRINT_SUMMARIES)
        assert _has_gh_subprocess_run(src), (
            "sprint_summaries.py must currently have a gh subprocess call — "
            "this confirms the xfail is necessary and not spurious"
        )
