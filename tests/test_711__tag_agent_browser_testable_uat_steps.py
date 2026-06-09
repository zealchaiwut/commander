"""Tests for #711: Tag agent-browser-testable UAT steps in BA tickets.

AC items verified:
  AC-1  .claude/agents/ba.md UAT-writing instructions include a rule: tag a step
        [agent-test] when it is fully expressible as navigate -> interact ->
        observe in a desktop browser with no subjective visual judgment, real
        device, or external service dependency; leave all other steps untagged.
  AC-2  Browser-verifiable UAT steps end with [agent-test]; steps needing human
        judgment / native device / third-party logins carry no tag. (Instruction
        wording anchors the trailing-tag placement and selective tagging.)
  AC-3  scripts/create_ticket.py passes the issue body to the GitHub API verbatim
        — [agent-test] tags are not stripped, escaped, or transformed.
  AC-4  .github/ISSUE_TEMPLATE/feature.md UAT section preserves [agent-test] tags
        as literal text (no escaping / entity-encoding) when rendered.
  AC-5  A [agent-test]-tagged step matches the agreed parser contract regex
        ^\\d+\\..*\\[agent-test\\]\\s*$ so tooling can extract tagged steps.
  AC-6  Selective tagging works: an untagged (MANUAL) step does NOT match the
        contract regex, and the BA rule instructs leaving non-qualifying steps
        untagged.
"""
from __future__ import annotations

import re
import sys
import types
import importlib.util
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
BA_MD = REPO_ROOT / ".claude" / "agents" / "ba.md"
FEATURE_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature.md"
CREATE_TICKET = REPO_ROOT / "scripts" / "create_ticket.py"

# Parser contract agreed with the agent-browser test runner (AC-5).
CONTRACT_RE = re.compile(r"^\d+\..*\[agent-test\]\s*$")


def _ba_text() -> str:
    return BA_MD.read_text(encoding="utf-8")


def _template_text() -> str:
    return FEATURE_TEMPLATE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-1: ba.md contains the [agent-test] tagging rule with criteria + exemptions
# ---------------------------------------------------------------------------

class TestAC1BaRuleExists:
    def test_mentions_agent_test_tag(self):
        assert "[agent-test]" in _ba_text(), (
            "ba.md must reference the [agent-test] tag in its UAT-writing rules"
        )

    def test_states_navigate_interact_observe_criterion(self):
        lower = _ba_text().lower()
        for word in ("navigate", "interact", "observe"):
            assert word in lower, (
                f"ba.md [agent-test] rule must state the '{word}' criterion "
                "(navigate -> interact -> observe)"
            )

    def test_restricts_to_desktop_browser(self):
        assert "desktop browser" in _ba_text().lower(), (
            "ba.md [agent-test] rule must restrict to a desktop browser"
        )

    def test_names_subjective_visual_exemption(self):
        lower = _ba_text().lower()
        assert "subjective" in lower, (
            "ba.md must exempt steps needing subjective visual judgment"
        )

    def test_names_real_device_exemption(self):
        lower = _ba_text().lower()
        assert "real device" in lower or "native device" in lower, (
            "ba.md must exempt steps needing a real/native device"
        )

    def test_names_external_service_exemption(self):
        lower = _ba_text().lower()
        assert "external" in lower and (
            "oauth" in lower or "auth" in lower or "third-party" in lower
        ), (
            "ba.md must exempt steps depending on an external service / auth"
        )

    def test_names_email_or_sms_exemption(self):
        lower = _ba_text().lower()
        assert "email" in lower or "sms" in lower, (
            "ba.md must exempt email/SMS flows from [agent-test] tagging"
        )


# ---------------------------------------------------------------------------
# AC-2 / AC-6: trailing-tag placement and selective (leave-untagged) tagging
# ---------------------------------------------------------------------------

class TestAC2TrailingTagAndSelective:
    def test_rule_places_tag_at_end_of_step(self):
        lower = _ba_text().lower()
        assert any(
            phrase in lower
            for phrase in ("ends with", "end of the step", "append", "at the end")
        ), (
            "ba.md must instruct putting [agent-test] at the END of a qualifying step"
        )

    def test_rule_leaves_other_steps_untagged(self):
        lower = _ba_text().lower()
        assert "untagged" in lower or "manual" in lower, (
            "ba.md must instruct leaving non-qualifying steps untagged (MANUAL)"
        )


# ---------------------------------------------------------------------------
# AC-3: create_ticket.py passes the body to gh verbatim
# ---------------------------------------------------------------------------

def _load_create_ticket():
    """Import scripts/create_ticket.py with github_client stubbed."""
    stub = types.ModuleType("github_client")
    stub.repo = lambda: "zealchaiwut/commander"
    sys.modules["github_client"] = stub

    spec = importlib.util.spec_from_file_location("_create_ticket_711", CREATE_TICKET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_main_capture(body: str, extra_argv=None):
    """Run create_ticket.main() with a captured fake gh; return the gh argv list."""
    mod = _load_create_ticket()
    captured = {}

    def fake_run(cmd, *a, **kw):
        # The issue-create invocation is the one we assert on.
        if "issue" in cmd and "create" in cmd:
            captured["cmd"] = cmd
            return mock.Mock(returncode=0, stdout="https://github.com/zealchaiwut/commander/issues/999\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    argv = ["create_ticket.py", "--title", "T", "--body", body] + (extra_argv or [])
    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run), \
         mock.patch.object(sys, "argv", argv):
        mod.main()
    return captured["cmd"]


class TestAC3CreateTicketVerbatim:
    def test_body_passed_to_gh_unchanged(self):
        body = "## UAT Test Steps\n\n1. Open the page and click Save [agent-test]\n   **Expected:** Toast shows"
        cmd = _run_main_capture(body)
        idx = cmd.index("--body")
        assert cmd[idx + 1] == body, (
            "create_ticket.py must pass the body to gh verbatim"
        )

    def test_agent_test_tag_present_in_payload(self):
        body = "1. Click Save [agent-test]"
        cmd = _run_main_capture(body)
        joined = " ".join(cmd)
        assert "[agent-test]" in joined, (
            "[agent-test] must survive unchanged in the gh payload"
        )

    def test_tag_not_html_escaped(self):
        body = "1. Click Save [agent-test]"
        cmd = _run_main_capture(body)
        idx = cmd.index("--body")
        payload = cmd[idx + 1]
        assert "&#91;" not in payload and "&lt;" not in payload, (
            "[agent-test] must not be HTML-encoded"
        )
        assert "\\[agent-test\\]" not in payload, (
            "[agent-test] must not be backslash-escaped"
        )


# ---------------------------------------------------------------------------
# AC-4: feature.md template preserves [agent-test] as literal text
# ---------------------------------------------------------------------------

class TestAC4TemplatePreservesTag:
    def test_template_documents_tag_literally(self):
        text = _template_text()
        assert "[agent-test]" in text, (
            "feature.md UAT section must reference the [agent-test] tag as guidance"
        )

    def test_template_tag_not_escaped(self):
        text = _template_text()
        assert "\\[agent-test\\]" not in text, "tag must not be backslash-escaped"
        assert "&#91;" not in text and "&lbrack;" not in text, (
            "tag must not be entity-encoded in the template"
        )

    def test_template_tag_in_uat_section(self):
        text = _template_text()
        uat_idx = text.find("## UAT Test Steps")
        assert uat_idx != -1, "feature.md must keep the UAT Test Steps section"
        tag_idx = text.find("[agent-test]", uat_idx)
        assert tag_idx != -1, (
            "[agent-test] guidance must live within the UAT Test Steps section"
        )


# ---------------------------------------------------------------------------
# AC-5 / AC-6: parser contract regex matches tagged, rejects untagged
# ---------------------------------------------------------------------------

class TestAC5ParserContract:
    def test_tagged_step_matches_contract(self):
        step = "3. Navigate to /dashboard and click Refresh [agent-test]"
        assert CONTRACT_RE.match(step), (
            "a [agent-test]-tagged step must match the agreed parser contract regex"
        )

    def test_tagged_step_with_trailing_space_matches(self):
        step = "3. Click Save [agent-test] "
        assert CONTRACT_RE.match(step), (
            "trailing whitespace after the tag must still match the contract"
        )

    def test_untagged_step_does_not_match(self):
        step = "4. Visually confirm the chart colors look balanced"
        assert not CONTRACT_RE.match(step), (
            "an untagged MANUAL step must NOT match the contract regex"
        )

    def test_externally_dependent_step_untagged_and_rejected(self):
        step = "5. Complete the Google OAuth login and return to the app"
        assert "[agent-test]" not in step
        assert not CONTRACT_RE.match(step), (
            "an external-service step must be untagged and rejected by the contract"
        )
