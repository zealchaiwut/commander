"""
Unit tests for the client-side ticket quality linter (issue #574).

Extracts lintTicket() from project.html and runs it via Node.js so the
tests are authoritative against the actual deployed JS, not a Python clone.
"""
import json
import os
import re
import subprocess
import tempfile

import pytest

HTML_PATH = os.path.join(
    os.path.dirname(__file__), "..", "apps", "dashboard", "static", "project.html"
)


def _extract_linter_js():
    """Pull the linter constants and lintTicket function out of project.html."""
    with open(HTML_PATH, encoding="utf-8") as f:
        src = f.read()

    # Grab _LINT_ACTION_VERBS const
    m = re.search(r"(const _LINT_ACTION_VERBS\s*=\s*\[.*?\];)", src, re.DOTALL)
    assert m, "_LINT_ACTION_VERBS not found in project.html"
    verbs = m.group(1)

    # Grab _LINT_ACCEPTANCE_SIGNALS const
    m = re.search(r"(const _LINT_ACCEPTANCE_SIGNALS\s*=\s*\[.*?\];)", src, re.DOTALL)
    assert m, "_LINT_ACCEPTANCE_SIGNALS not found in project.html"
    signals = m.group(1)

    # Grab lintTicket function
    m = re.search(r"(function lintTicket\(title, body\)\s*\{.*?\n\})", src, re.DOTALL)
    assert m, "lintTicket() not found in project.html"
    fn = m.group(1)

    return f"{verbs}\n{signals}\n{fn}\n"


def _run_linter(title: str, body: str) -> list[str]:
    """Execute lintTicket(title, body) in Node.js and return the issues array."""
    js_base = _extract_linter_js()
    script = (
        js_base
        + f"\nconst result = lintTicket({json.dumps(title)}, {json.dumps(body)});\n"
        + "process.stdout.write(JSON.stringify(result));\n"
    )
    with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
        f.write(script)
        tmp = f.name
    try:
        out = subprocess.check_output(["node", tmp], stderr=subprocess.PIPE)
        return json.loads(out)
    finally:
        os.unlink(tmp)


# ── Too-short flag ─────────────────────────────────────────────────────────────

class TestTooShort:
    def test_combined_under_20_is_flagged(self):
        issues = _run_linter("Fix it", "")
        assert any("short" in i.lower() or "20" in i for i in issues)

    def test_combined_exactly_19_is_flagged(self):
        issues = _run_linter("Fix", "x" * 15)  # 3 + 1 (space) + 15 = 19
        assert any("short" in i.lower() or "20" in i for i in issues)

    def test_combined_20_or_more_not_flagged_for_short(self):
        title = "Fix login redirect"   # 18 chars, has verb
        body = "Done when redirect works."  # has acceptance signal
        issues = _run_linter(title, body)
        short_flags = [i for i in issues if "short" in i.lower() or "20" in i]
        assert short_flags == []

    def test_empty_inputs_flagged(self):
        issues = _run_linter("", "")
        assert any("short" in i.lower() or "20" in i for i in issues)


# ── No-action-verb flag ────────────────────────────────────────────────────────

class TestActionVerb:
    def test_title_without_verb_is_flagged(self):
        issues = _run_linter("Dashboard improvements", "So that users see progress. AC: chart renders.")
        assert any("verb" in i.lower() for i in issues)

    def test_title_with_add_is_not_flagged(self):
        issues = _run_linter("Add dark mode toggle", "Done when theme persists across reloads.")
        verb_flags = [i for i in issues if "verb" in i.lower()]
        assert verb_flags == []

    def test_title_with_fix_is_not_flagged(self):
        issues = _run_linter("Fix login redirect loop", "Done when the user lands on the dashboard after OAuth.")
        verb_flags = [i for i in issues if "verb" in i.lower()]
        assert verb_flags == []

    def test_title_with_investigate_is_not_flagged(self):
        issues = _run_linter(
            "Investigate slow query on reports page",
            "Expected: query completes in under 200 ms.",
        )
        verb_flags = [i for i in issues if "verb" in i.lower()]
        assert verb_flags == []

    def test_title_with_implement_is_not_flagged(self):
        issues = _run_linter("Implement CSV export for sprint metrics", "AC:\n- [ ] file downloads")
        verb_flags = [i for i in issues if "verb" in i.lower()]
        assert verb_flags == []

    def test_verb_case_insensitive(self):
        issues = _run_linter("REMOVE stale cron jobs", "Done when no cron warnings in logs.")
        verb_flags = [i for i in issues if "verb" in i.lower()]
        assert verb_flags == []


# ── No-acceptance-signal flag ─────────────────────────────────────────────────

class TestAcceptanceSignal:
    def test_body_without_signal_is_flagged(self):
        issues = _run_linter("Fix broken layout", "The sidebar overlaps the main content area on mobile.")
        assert any("acceptance" in i.lower() or "ac:" in i.lower() or "signal" in i.lower() for i in issues)

    def test_body_with_so_that_is_not_flagged(self):
        issues = _run_linter("Fix login redirect loop", "So that the user lands on the dashboard after OAuth.")
        signal_flags = [i for i in issues if "acceptance" in i.lower() or "signal" in i.lower()]
        assert signal_flags == []

    def test_body_with_done_when_is_not_flagged(self):
        issues = _run_linter("Fix login redirect loop", "Done when the user lands on the dashboard after OAuth.")
        signal_flags = [i for i in issues if "acceptance" in i.lower() or "signal" in i.lower()]
        assert signal_flags == []

    def test_body_with_checkbox_list_is_not_flagged(self):
        issues = _run_linter("Add export button", "- [ ] CSV downloads\n- [ ] filename includes date")
        signal_flags = [i for i in issues if "acceptance" in i.lower() or "signal" in i.lower()]
        assert signal_flags == []

    def test_body_with_ac_colon_is_not_flagged(self):
        issues = _run_linter("Refactor auth module", "AC:\n- token validated on every request")
        signal_flags = [i for i in issues if "acceptance" in i.lower() or "signal" in i.lower()]
        assert signal_flags == []

    def test_body_with_expected_is_not_flagged(self):
        issues = _run_linter(
            "Investigate slow query on reports page",
            "Expected: query completes in under 200 ms on the reports endpoint.",
        )
        signal_flags = [i for i in issues if "acceptance" in i.lower() or "signal" in i.lower()]
        assert signal_flags == []

    def test_body_with_acceptance_criteria_is_not_flagged(self):
        issues = _run_linter("Update favicon", "## Acceptance Criteria\nFavicon shows in browser tab.")
        signal_flags = [i for i in issues if "acceptance" in i.lower() or "signal" in i.lower()]
        assert signal_flags == []


# ── No false positives on valid ticket ────────────────────────────────────────

class TestNoFalsePositives:
    def test_valid_ticket_produces_no_warnings(self):
        """AC#11: a clear title with action verb and acceptance signal must return []."""
        title = "Fix login redirect loop"
        body = "Done when the user lands on the dashboard after OAuth."
        assert _run_linter(title, body) == []

    def test_valid_ticket_with_checklist(self):
        title = "Add dark mode toggle to settings"
        body = (
            "## Acceptance Criteria\n"
            "- [ ] Toggle appears in user settings panel\n"
            "- [ ] Theme persists after page reload\n"
        )
        assert _run_linter(title, body) == []

    def test_verb_flag_not_raised_when_verb_present(self):
        """UAT step 6: only acceptance-signal flag when verb present but no signal."""
        title = "Investigate slow query on reports page"
        body = "The reports page loads slowly."
        issues = _run_linter(title, body)
        assert all("verb" not in i.lower() for i in issues)
        assert any("acceptance" in i.lower() or "signal" in i.lower() for i in issues)

    def test_exactly_three_flags_on_vague_ticket(self):
        """UAT step 1 scenario: very short title, no verb, no signal → 3 flags."""
        issues = _run_linter("thing", "")
        assert len(issues) == 3
