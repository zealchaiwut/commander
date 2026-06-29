"""Tests for issue #1574 — remove stale eslint /* global */ entry for the
now-local `_smgmtNextUpLabel` in board-render.js.

Each test maps to one acceptance criterion from the issue.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BOARD_RENDER = (
    REPO_ROOT
    / "apps"
    / "dashboard"
    / "static"
    / "src"
    / "sprint-board"
    / "board-render.js"
)


def _global_directive_entries():
    """Parse the file-level `/* global ... */` eslint directive into a set of
    bare names (stripping any `:writable` / `:readonly` qualifier)."""
    text = BOARD_RENDER.read_text()
    match = re.search(r"/\*\s*global\s+(.*?)\*/", text, re.DOTALL)
    assert match, "could not find a /* global */ directive in board-render.js"
    entries = set()
    for raw in match.group(1).split(","):
        name = raw.strip()
        if not name:
            continue
        name = name.split(":", 1)[0].strip()
        if name:
            entries.add(name)
    return entries


def test_ac1_directive_no_longer_lists_smgmtnextuplabel():
    """AC1: the /* global */ directive no longer contains _smgmtNextUpLabel."""
    assert "_smgmtNextUpLabel" not in _global_directive_entries()


def test_ac2_local_declaration_remains_untouched():
    """AC2: the local declaration `let _smgmtNextUpLabel = null;` (~line 413)
    remains present."""
    text = BOARD_RENDER.read_text()
    assert "let _smgmtNextUpLabel = null;" in text


def test_ac3_no_other_directive_names_changed():
    """AC3: no other names are removed from or added to the directive.

    Only _smgmtNextUpLabel should differ from the original set. We assert the
    set of entries equals the known original set minus that single name — this
    catches both accidental removals (e.g. the adjacent _smgmtNextChildLabel)
    and any stray additions.
    """
    original = {
        "_blApplyFilters", "_blBacklogAll", "_blSyncFilterPills",
        "_blUpdateActions", "_smgmtEnsureCapData", "_smgmtLoadMiniRail",
        "_smgmtMiniRailRestoreCached", "_smgmtRenderAllCapBars",
        "_smgmtUpdateSubnav", "_cachedFullRepo", "_estDataCache", "_slug",
        "_smgmtActiveAgentsHtml", "_smgmtAgentTagClass", "_smgmtApplySort",
        "_smgmtBacklogTicketDragStart", "_smgmtBulkEstimate", "_smgmtBySprint",
        "_smgmtCancelBannerHtml", "_smgmtCapacityInputHtml",
        "_smgmtCheckEstimatorHealth", "_smgmtCloseIssueOpen",
        "_smgmtConflictsByIssue", "_smgmtCtxMenuOpen", "_smgmtDagDataCache",
        "_smgmtData", "_smgmtDeactivatedLabels", "_smgmtDepOrderByIssue",
        "_smgmtDragLeave", "_smgmtDragOver", "_smgmtDropOnSprint",
        "_smgmtEstimateBadgeHtml", "_smgmtEstimatorAvailable",
        "_smgmtFilterApply", "_smgmtFinishCards", "_smgmtFinishedLabels",
        "_smgmtHasCompletedTickets", "_smgmtInitCapacityGauges",
        "_smgmtInjectOutcomeBand", "_smgmtIsCancelled", "_smgmtKbRestoreFocus",
        "_smgmtLabelColors", "_smgmtLabelFilterToggle",
        "_smgmtLabelFilterToggleExpand", "_smgmtLastLabelIssues",
        "_smgmtLevelsHtml", "_smgmtLiveAgentBadgesHtml", "_smgmtLiveCache",
        "_smgmtLiveCacheRepo", "_smgmtLiveLogLinesHtml", "_smgmtLivePollRestart",
        "_smgmtLingerRestore", "_smgmtLingerStart", "_smgmtIsLinger",
        "_smgmtLingerLive", "_smgmtNextChildLabel", "_smgmtNextUpLabel",
        "_smgmtOutcomeCache", "_smgmtOutcomeLogHtml", "_smgmtPrimaryRunningLabel",
        "_smgmtReEstimate", "_smgmtRepo", "_smgmtRiskFlagIconsHtml",
        "_smgmtRowClick", "_smgmtRowMenuOpen", "_smgmtRunningViewUpdate",
        "_smgmtSchedDepHtml", "_smgmtSelectedIssues", "_smgmtSetSprintTokenEl",
        "_smgmtStateMeta", "_smgmtTicketDragEnd", "_smgmtTicketDragStart",
        "_smgmtTicketReorderDragLeave", "_smgmtTicketReorderDragOver",
        "_smgmtTicketReorderDrop", "_smgmtTicketToSprint", "_smgmtToggleSelect",
        "_smgmtUpdateCapacityGauge", "_smgmtUpdateCleanupBtn",
        "_smgmtUpdateConflictBadge", "_smgmtUpdateDepOrderBadge",
        "_smgmtUpdateEstimateBadge", "_smgmtUpdateSelectionUI",
        "_smgmtSchedToggleHtml", "_smgmtHydrateSchedToggles", "escHtml",
        "sprintLabelDisplay", "colorizeLogLine", "_smgmtAnySprintRunning",
        "_smgmtOrderedLabels", "_smgmtRunningLabels",
    }
    expected = original - {"_smgmtNextUpLabel"}
    assert _global_directive_entries() == expected


def test_ac4_esbuild_build_succeeds():
    """AC4: `npm run build` (esbuild) completes without errors after the change.

    Skipped where the esbuild toolchain is unavailable (e.g. the coder clone
    has no node_modules installed); the build is then verified manually in a
    clone that has the toolchain, per the project workflow.
    """
    dashboard_dir = REPO_ROOT / "apps" / "dashboard"
    local_esbuild = dashboard_dir / "node_modules" / ".bin" / "esbuild"
    if shutil.which("npm") is None:
        pytest.skip("npm not available in this environment")
    if not local_esbuild.exists() and shutil.which("esbuild") is None:
        pytest.skip("esbuild toolchain not installed (no node_modules)")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=dashboard_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"npm run build failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
