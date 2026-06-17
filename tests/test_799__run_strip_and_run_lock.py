"""Tests for issue #799 — Add animated run strip and board-wide run lock.

Acceptance criteria tested (each test class maps to one AC):

  AC1 — Board view renders a `.run-strip` / `.run-strip-inner` component for the
        currently running sprint, with a pulse dot, live counts, and a
        "Watch in Running →" link to the Running view.
  AC2 — Animation uses BA Path-A literal tokens: a gradient + a 3.2 s animation
        (mock v5 contract).
  AC3 — `prefers-reduced-motion` falls back to a solid green static frame with
        no animation.
  AC4 — All planned-sprint Run and Delete buttons are disabled (`.btn-run`
        receives a disabled state + a `.runlock-hint`) while `/live` reports an
        active run.
  AC5 — `.runlock-hint` text reads "blocked: S<N> running" where N is the
        running sprint number.
  AC6 — After the active run ends the strip is removed and all Run/Delete
        buttons re-enable on the next Board render (state gated on
        `_smgmtAnySprintRunning`).
  AC7 — `npx impeccable detect` passes with zero violations (runs in the
        CI/tester environment where node/npx is installed).
  AC8 — No regressions to the Board view layout or other sprint controls.

The dashboard frontend is no-build vanilla HTML/JS served from disk, so these
tests assert against the static source of `project.html` (the same approach as
test_798__sprint_tab_rename_and_subnav.py).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_PROJECT_HTML = _REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"


@pytest.fixture(scope="module")
def html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _slice(html: str, start_marker: str, end_marker: str) -> str:
    idx = html.find(start_marker)
    assert idx >= 0, f"marker not found: {start_marker}"
    end = html.find(end_marker, idx)
    assert end > idx, f"end marker {end_marker} must come after {start_marker}"
    return html[idx:end]


def _func_body(html: str, signature: str, span: int = 1600) -> str:
    idx = html.find(signature)
    assert idx >= 0, f"function not found: {signature}"
    return html[idx:idx + span]


# ---------------------------------------------------------------------------
# AC1 — .run-strip / .run-strip-inner component for the running sprint
# ---------------------------------------------------------------------------

class TestRunStripComponent:
    def test_run_strip_builder_exists(self, html):
        assert "function _smgmtRunStripHtml" in html, \
            "A _smgmtRunStripHtml() builder must exist to render the run strip"

    def test_run_strip_and_inner_classes(self, html):
        body = _func_body(html, "function _smgmtRunStripHtml")
        assert 'class="run-strip"' in body, \
            "Run strip must use the .run-strip mock token"
        assert 'class="run-strip-inner"' in body, \
            "Run strip must use the .run-strip-inner mock token"

    def test_pulse_dot_present(self, html):
        body = _func_body(html, "function _smgmtRunStripHtml")
        assert "run-strip-dot" in body, \
            "Run strip must include a pulse dot (.run-strip-dot)"

    def test_live_counts_present(self, html):
        body = _func_body(html, "function _smgmtRunStripHtml")
        assert "run-strip-counts" in body, \
            "Run strip must show live counts (.run-strip-counts)"
        # Counts come from the live cache (done/total), not a static number.
        assert "_smgmtLiveCache" in body, \
            "Run strip counts must read from _smgmtLiveCache"
        assert "total_count" in body and "done_count" in body, \
            "Run strip counts must reflect live done/total counts"

    def test_watch_link_to_running_view(self, html):
        body = _func_body(html, "function _smgmtRunStripHtml")
        assert "Watch in Running" in body, \
            'Run strip must contain a "Watch in Running →" link'
        assert "run-strip-watch" in body, \
            "Watch link must use the .run-strip-watch class"
        assert "_smgmtShowSubView('running')" in body, \
            "Watch link must navigate to the Running sub-view"

    def test_strip_injected_into_board_render(self, html):
        body = _func_body(html, "function _smgmtRender", span=3600)
        assert "_smgmtRunStripHtml()" in body, \
            "_smgmtRender must inject the run strip into the Board sprint list"

    def test_run_strip_css_defined(self, html):
        css = _slice(html, ".run-strip {", "</style>")
        assert ".run-strip-inner" in css, ".run-strip-inner must be styled"


# ---------------------------------------------------------------------------
# AC2 — gradient + 3.2 s animation (BA Path-A literal tokens, mock v5)
# ---------------------------------------------------------------------------

class TestAnimationTokens:
    def test_flow_keyframes_defined(self, html):
        assert "@keyframes smgmt-run-strip-flow" in html, \
            "An animated keyframes for the run strip border must be defined"

    def test_animation_duration_is_3_2s(self, html):
        css = _slice(html, ".run-strip {", "</style>")
        m = re.search(r"animation:\s*smgmt-run-strip-flow\s+([\d.]+)s", css)
        assert m, "Run strip must animate with the smgmt-run-strip-flow keyframes"
        assert m.group(1) == "3.2", \
            f"Animation must use the literal 3.2 s token (got {m.group(1)}s)"

    def test_gradient_border(self, html):
        css = _slice(html, ".run-strip {", ".run-strip-inner")
        assert "linear-gradient" in css, \
            "Run strip must use a gradient (Path-A token) for its animated border"

    def test_no_hardcoded_hex_in_strip(self, html):
        # Theme correctness: the strip must reference CSS variables, never hex.
        css = _slice(html, ".run-strip {", ".runlock-hint")
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", css), \
            "Run strip CSS must use CSS variables, not hardcoded hex colors"


# ---------------------------------------------------------------------------
# AC3 — prefers-reduced-motion falls back to a solid green static frame
# ---------------------------------------------------------------------------

class TestReducedMotion:
    def test_reduced_motion_block_targets_run_strip(self, html):
        # Find a prefers-reduced-motion block that mentions .run-strip.
        blocks = re.findall(
            r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{.*?\}\s*\}",
            html, re.S,
        )
        target = [b for b in blocks if ".run-strip" in b]
        assert target, \
            "A prefers-reduced-motion block must target the .run-strip"
        block = "\n".join(target)
        assert "animation: none" in block, \
            "Reduced motion must disable the run-strip animation"
        assert "var(--green)" in block, \
            "Reduced-motion fallback must be a solid green static frame"


# ---------------------------------------------------------------------------
# AC4 — planned Run + Delete buttons disabled while a sprint runs
# ---------------------------------------------------------------------------

class TestRunLock:
    def test_blocked_run_button_has_btn_run_class(self, html):
        body = _func_body(html, "function _smgmtCardHtml", span=2600)
        # The blocked-run branch fires only when a sprint is running.
        m = re.search(r"_smgmtAnySprintRunning\)\s*\{\s*actionBtn\s*=\s*`(.*?)`", body, re.S)
        assert m, "There must be a blocked-run actionBtn branch keyed on _smgmtAnySprintRunning"
        blocked = m.group(1)
        assert "btn-run" in blocked, \
            "Blocked Run button must carry the .btn-run mock token"
        assert "disabled" in blocked, \
            "Blocked Run button must be disabled while a sprint runs"

    def test_delete_button_disabled_when_running(self, html):
        body = _func_body(html, "function _smgmtCardHtml", span=9200)
        m = re.search(r'class="smgmt-delete-btn"[^>]*?_smgmtAnySprintRunning[^>]*?disabled', body, re.S)
        assert m or re.search(
            r'smgmt-delete-btn.*?_smgmtAnySprintRunning\s*\?\s*[\'"]disabled', body, re.S
        ), "Delete button must receive a disabled state while a sprint runs"

    def test_runlock_hint_rendered_when_running(self, html):
        body = _func_body(html, "function _smgmtCardHtml", span=2600)
        assert "runlock-hint" in body, \
            "A .runlock-hint must be rendered on planned cards while a sprint runs"

    def test_runlock_hint_css_defined(self, html):
        css = _slice(html, ".runlock-hint {", "}")
        assert "color" in css, ".runlock-hint must be styled"


# ---------------------------------------------------------------------------
# AC5 — runlock-hint reads "blocked: S<N> running"
# ---------------------------------------------------------------------------

class TestRunlockHintText:
    def test_running_sprint_number_helper(self, html):
        assert "function _smgmtRunningSprintNum" in html, \
            "A helper resolving the running sprint number (N) must exist"
        body = _func_body(html, "function _smgmtRunningSprintNum", span=400)
        assert "_smgmtRunningLabels" in body, \
            "Running sprint number must be derived from _smgmtRunningLabels"

    def test_hint_text_format(self, html):
        body = _func_body(html, "function _smgmtCardHtml", span=2600)
        # The hint literal must read "blocked: S<N> running".
        m = re.search(r'runlock-hint">blocked:\s*S\$\{[^}]+\}\s*running', body)
        assert m, (
            'runlock-hint text must read "blocked: S<N> running" where N is the '
            "running sprint number"
        )


# ---------------------------------------------------------------------------
# AC6 — strip removed + buttons re-enable after the run ends
# ---------------------------------------------------------------------------

class TestRunEndsReset:
    def test_strip_builder_guards_on_running(self, html):
        body = _func_body(html, "function _smgmtRunStripHtml", span=500)
        # When nothing is running the builder returns an empty string, so the
        # next render drops the strip from the Board.
        assert "_smgmtAnySprintRunning" in body, \
            "Run strip builder must short-circuit when no sprint is running"
        assert "return ''" in body, \
            "Run strip builder must return '' when no sprint is running"

    def test_run_lock_gated_on_running_flag(self, html):
        # The disabled Run/Delete state must be keyed on _smgmtAnySprintRunning,
        # so a subsequent render with the flag cleared re-enables the controls.
        body = _func_body(html, "function _smgmtCardHtml", span=4200)
        assert body.count("_smgmtAnySprintRunning") >= 2, \
            "Run and Delete lock must both be gated on _smgmtAnySprintRunning"


# ---------------------------------------------------------------------------
# AC7 — impeccable detect passes (runs where node/npx is installed)
# ---------------------------------------------------------------------------

class TestImpeccable:
    def test_impeccable_detect_clean(self):
        npx = shutil.which("npx")
        if not npx:
            pytest.skip("npx/node not installed in this environment; "
                        "impeccable detect runs in the CI/tester environment")
        proc = subprocess.run(
            [npx, "impeccable", "detect", str(_PROJECT_HTML)],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        assert proc.returncode == 0, (
            "impeccable detect must pass with zero violations:\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


# ---------------------------------------------------------------------------
# AC8 — no regressions to the Board view layout or other sprint controls
# ---------------------------------------------------------------------------

class TestNoRegressions:
    def test_board_subview_intact(self, html):
        assert 'id="smgmt-subview-board"' in html, \
            "Board sub-view container must remain"
        assert 'id="smgmt-sprint-list"' in html, \
            "Board sprint list container must remain"

    def test_run_and_rerun_handlers_intact(self, html):
        assert "smgmtRunSprint('" in html, "Run Sprint handler must remain"
        assert "smgmtRerunSprint('" in html, "Re-run handler must remain"
        assert "smgmtDeleteSprint('" in html, "Delete handler must remain"

    def test_running_card_still_rendered(self, html):
        # The full running card path (issue #277) must stay intact.
        assert "function _smgmtRunningCardHtml" in html, \
            "Running sprint card builder must remain"
