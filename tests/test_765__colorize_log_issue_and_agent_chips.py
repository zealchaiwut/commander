"""Tests for issue #765: Colorize issue numbers and agent names in log viewer.

AC-1: Issue refs matching /#\\d+/ render as styled, clickable chips in the live
      log panel, Activity tab log rows, and post-run summary log excerpts;
      clicking opens the corresponding GitHub issue.
AC-2: Agent names (coder, tester, reviewer, documenter, estimator, BA) are
      matched word-bounded, case-insensitively, and rendered in stable per-agent
      color chips (coder=blue, tester=green, reviewer=purple, documenter=amber,
      estimator=teal, BA=pink).
AC-3: Colors are defined as CSS classes, are dark-mode aware, and pass the
      impeccable contrast gate.
AC-4: Log content containing HTML chars is HTML-escaped before tokenization; no
      injected markup is ever executed in the browser.
AC-5: No measurable scroll/rendering perf regression on a 5 000-line log.
AC-6: All three surfaces apply identical chip rendering (same helper).

The colorization logic lives in a standalone, node-testable module
(`apps/dashboard/static/log-colorize.js`) so each acceptance criterion can be
exercised against the real implementation rather than asserted on source text.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"
MODULE = STATIC_DIR / "log-colorize.js"
PROJECT_HTML_PATH = STATIC_DIR / "project.html"

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def _colorize(text: str, repo: str = "") -> str:
    """Invoke the real colorizeLogLine() in node and return its HTML output."""
    script = (
        "const m=require(process.argv[1]);"
        "process.stdout.write(m.colorizeLogLine(process.argv[2], process.argv[3]));"
    )
    out = subprocess.run(
        [NODE, "-e", script, str(MODULE), text, repo],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


@pytest.fixture(scope="module")
def project_html() -> str:
    return PROJECT_HTML_PATH.read_text()


def test_module_exists():
    assert MODULE.exists(), "log-colorize.js module must exist"


# ── AC-1: issue refs → clickable GitHub chips ─────────────────────────────────

@needs_node
class TestIssueChips:
    def test_issue_ref_becomes_chip(self):
        html = _colorize("fixing #42 now", "owner/repo")
        assert 'class="log-chip log-chip--issue"' in html
        assert ">#42<" in html

    def test_chip_links_to_github_issue(self):
        html = _colorize("see #42", "owner/repo")
        assert 'href="https://github.com/owner/repo/issues/42"' in html

    def test_chip_opens_new_tab_safely(self):
        html = _colorize("see #42", "owner/repo")
        assert 'target="_blank"' in html
        assert 'rel="noopener"' in html

    def test_multiple_issue_refs(self):
        html = _colorize("#1 and #2 and #3", "o/r")
        assert html.count('log-chip--issue') == 3

    def test_no_repo_falls_back_to_hash_href(self):
        html = _colorize("#7", "")
        assert 'href="#"' in html
        assert ">#7<" in html


# ── AC-2: agent names → per-agent color chips ─────────────────────────────────

@needs_node
class TestAgentChips:
    @pytest.mark.parametrize(
        "agent,cls",
        [
            ("coder", "log-agent-coder"),
            ("tester", "log-agent-tester"),
            ("reviewer", "log-agent-reviewer"),
            ("documenter", "log-agent-documenter"),
            ("estimator", "log-agent-estimator"),
            ("BA", "log-agent-ba"),
        ],
    )
    def test_each_agent_gets_its_chip_class(self, agent, cls):
        html = _colorize(f"the {agent} ran", "o/r")
        assert "log-chip--agent" in html
        assert cls in html

    def test_case_insensitive_match(self):
        html = _colorize("CODER and Tester and reViewer", "o/r")
        assert "log-agent-coder" in html
        assert "log-agent-tester" in html
        assert "log-agent-reviewer" in html

    def test_word_bounded_no_false_positive(self):
        # "encoder" contains "coder" but must NOT be chipped.
        html = _colorize("the encoder finished", "o/r")
        assert "log-agent-coder" not in html
        assert "log-chip--agent" not in html

    def test_word_bounded_estimator_not_in_plural_run(self):
        # "estimators" must not match the bare "estimator" token.
        html = _colorize("estimators are slow", "o/r")
        assert "log-chip--agent" not in html

    def test_unknown_agent_not_chipped(self):
        html = _colorize("the architect ran", "o/r")
        assert "log-chip--agent" not in html


# ── AC-2 / AC-3: per-agent color mapping defined as dark-mode-aware CSS ────────

class TestColorMappingCss:
    EXPECTED = {
        "coder": "blue",
        "tester": "green",
        "reviewer": "purple",
        "documenter": "amber",
        "estimator": "teal",
        "ba": "pink",
    }

    def test_chip_classes_use_color_vars_not_hardcoded_hex(self, project_html):
        for agent, color in self.EXPECTED.items():
            # Each agent chip class must reference the color and its -bg var.
            assert f".log-agent-{agent}" in project_html, f"missing .log-agent-{agent}"
            assert f"var(--{color})" in project_html
            assert f"var(--{color}-bg)" in project_html

    def test_teal_and_pink_vars_defined_light_and_dark(self, project_html):
        # AC-2 introduces teal (estimator) + pink (BA) which were not tokens yet.
        for var in ("--teal", "--teal-bg", "--pink", "--pink-bg"):
            # Must appear at least twice: once in :root (light), once in dark.
            assert project_html.count(f"{var}:") >= 2, f"{var} missing in a theme"

    def test_chip_base_class_present(self, project_html):
        assert ".log-chip" in project_html


# ── AC-4: HTML escaped before tokenization, no markup injection ───────────────

@needs_node
class TestHtmlEscaping:
    def test_script_tag_is_escaped(self):
        html = _colorize("coder processed <script>alert(1)</script> for #99", "o/r")
        assert "&lt;script&gt;" in html
        assert "<script>" not in html

    def test_chips_still_render_around_escaped_html(self):
        html = _colorize("coder processed <script>alert(1)</script> for #99", "o/r")
        assert "log-agent-coder" in html
        assert "log-chip--issue" in html
        assert ">#99<" in html

    def test_ampersand_and_quotes_escaped(self):
        html = _colorize('a & b "c"', "o/r")
        assert "&amp;" in html
        assert "&quot;" in html


# ── AC-5: performance on a 5 000-line log ─────────────────────────────────────

@needs_node
class TestPerformance:
    def test_tokenizes_5000_lines_quickly(self):
        line = "coder dispatched tester for #1234 — reviewer queued"
        big = "\\n".join([line] * 5000)
        start = time.perf_counter()
        html = _colorize(big, "owner/repo")
        elapsed = time.perf_counter() - start
        # Includes node startup; the tokenization itself is trivial. A regression
        # to anything pathological (e.g. re-scanning output) would blow past 5s.
        assert elapsed < 5.0, f"5000-line tokenize too slow: {elapsed:.2f}s"
        assert html.count("log-chip--issue") == 5000


# ── AC-1 / AC-6: all three surfaces use the identical shared helper ───────────

class TestSurfacesWired:
    def test_script_included_in_page(self, project_html):
        assert "log-colorize.js" in project_html

    def test_live_log_panel_uses_helper(self, project_html):
        # _smgmtLiveLogLinesHtml renders recent_log_lines for the live panel.
        idx = project_html.index("function _smgmtLiveLogLinesHtml")
        body = project_html[idx: idx + 600]
        assert "colorizeLogLine" in body, "live log panel must use shared helper"

    def test_post_run_summary_uses_helper(self, project_html):
        # _smgmtMaybeLoadLog renders the archived run-log excerpt.
        idx = project_html.index("async function _smgmtMaybeLoadLog")
        body = project_html[idx: idx + 1200]
        assert "colorizeLogLine" in body, "post-run summary must use shared helper"

    def test_activity_tab_uses_helper(self, project_html):
        # _logsEventsHtml renders the expandable Activity-tab run rows.
        idx = project_html.index("function _logsEventsHtml")
        body = project_html[idx: idx + 600]
        assert "colorizeLogLine" in body, "Activity tab rows must use shared helper"
