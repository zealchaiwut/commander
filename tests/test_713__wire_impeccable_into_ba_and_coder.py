"""Tests for #713: Wire impeccable design skills into BA and coder agents.

AC items verified:
  AC-1  .commander/setup.sh runs `npx impeccable skills install` and fails loudly
        if `.github/skills/impeccable/scripts/context.mjs` is missing afterwards
  AC-2  sprint_manager injects `node .github/skills/impeccable/scripts/context.mjs`
        into every headless `claude -p` dispatch prompt for coder AND tester
        (file-based skill pack, not the Claude Code plugin)
  AC-3  ba.md — frontend scope + mock HTML under references/issue-<N>/: BA reads the
        mock, extracts literal tokens, writes AC with literal values
  AC-4  ba.md — frontend scope + no mock: BA falls back to named impeccable rules
  AC-5  coder.md — frontend: coder loads impeccable skills via context.mjs before
        UI code, treats an attached mock as the pixel target, output passes detect
  AC-6  ba.md forbids generic UI AC language like "follows design system"
  AC-7  impeccable detect baseline is tracked so pass-rate change is measurable
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm

SETUP_SH = REPO_ROOT / ".commander" / "setup.sh"
BA_MD = REPO_ROOT / ".claude" / "agents" / "ba.md"
CODER_MD = REPO_ROOT / ".claude" / "agents" / "coder.md"
CONTEXT_MJS_PATH = ".github/skills/impeccable/scripts/context.mjs"
BASELINE_DOC = REPO_ROOT / "docs" / "impeccable-detect-baseline.md"


# ---------------------------------------------------------------------------
# AC-1: setup.sh installs impeccable skills and fails loudly if missing
# ---------------------------------------------------------------------------

class TestAC1SetupInstallsImpeccable:
    def _text(self) -> str:
        return SETUP_SH.read_text(encoding="utf-8")

    def test_setup_exists(self):
        assert SETUP_SH.exists(), f"{SETUP_SH} must exist"

    def test_runs_npx_impeccable_skills_install(self):
        text = self._text()
        assert "impeccable" in text and "skills install" in text, (
            "setup.sh must run `npx impeccable skills install`"
        )
        assert "npx" in text, "setup.sh must invoke the install via npx"

    def test_references_context_mjs(self):
        assert CONTEXT_MJS_PATH in self._text(), (
            f"setup.sh must verify {CONTEXT_MJS_PATH} exists after install"
        )

    def test_fails_loudly_when_context_mjs_missing(self):
        """A failure guard must `exit 1` (loudly) when context.mjs is absent."""
        text = self._text()
        # There must be a guard that exits non-zero referencing the script path.
        assert "exit 1" in text, "setup.sh must exit non-zero on a missing skill install"
        # The guard must be tied to the context.mjs check, not only the sprint.yaml block.
        idx = text.find(CONTEXT_MJS_PATH)
        assert idx != -1
        # An `exit 1` should appear within the impeccable verification region.
        region = text[idx : idx + 600]
        assert "exit 1" in region, (
            "setup.sh must `exit 1` in the context.mjs verification block"
        )


# ---------------------------------------------------------------------------
# AC-2: sprint_manager injects context.mjs into coder + tester prompts
# ---------------------------------------------------------------------------

class TestAC2ContextInjection:
    def test_helper_exists(self):
        assert hasattr(sm, "_impeccable_context_instruction"), (
            "_impeccable_context_instruction helper must exist in sprint_manager"
        )
        assert callable(sm._impeccable_context_instruction)

    def test_helper_references_context_mjs_via_node(self):
        text = sm._impeccable_context_instruction()
        assert "node " + CONTEXT_MJS_PATH in text, (
            "instruction must run `node .github/skills/impeccable/scripts/context.mjs`"
        )

    def test_helper_not_via_plugin(self):
        text = sm._impeccable_context_instruction().lower()
        assert "plugin" not in text, (
            "instruction must load the file-based skill pack, not the Claude Code plugin"
        )

    def test_coder_dispatch_source_injects_context(self):
        """The coder dispatch function body must inject the impeccable context."""
        import inspect
        src = inspect.getsource(sm._dispatch_coder)
        assert "_impeccable_context_instruction" in src, (
            "_dispatch_coder must append _impeccable_context_instruction to the prompt"
        )

    def test_tester_dispatch_source_injects_context(self):
        import inspect
        src = inspect.getsource(sm._dispatch_tester)
        assert "_impeccable_context_instruction" in src, (
            "_dispatch_tester must append _impeccable_context_instruction to the prompt"
        )


# ---------------------------------------------------------------------------
# AC-3: ba.md mock-extraction path
# ---------------------------------------------------------------------------

class TestAC3BaMockExtraction:
    def _text(self) -> str:
        return BA_MD.read_text(encoding="utf-8")

    def test_references_mock_under_issue_references(self):
        text = self._text()
        assert "references/issue-" in text, (
            "ba.md must reference a mock under references/issue-<N>/"
        )
        assert "mock" in text.lower()

    def test_extracts_literal_tokens(self):
        text = self._text().lower()
        # Must instruct extracting concrete token kinds.
        assert "hex" in text or "#" in text, "ba.md must mention extracting hex colors"
        assert "font-size" in text or "font size" in text, "ba.md must mention font sizes"
        assert "padding" in text or "spacing" in text, "ba.md must mention spacing values"

    def test_literal_value_example_present(self):
        """ba.md must show a literal-value example, not a placeholder/range."""
        import re
        text = self._text()
        assert re.search(r"#[0-9A-Fa-f]{3,6}", text), (
            "ba.md must include a literal hex example (e.g. #1A1A2E)"
        )
        assert re.search(r"\d+\s*px", text) or re.search(r"\d*\.?\d+\s*rem", text), (
            "ba.md must include a literal spacing/size example (e.g. 16px or 0.875rem)"
        )

    def test_cross_checks_impeccable(self):
        assert "impeccable" in self._text().lower(), (
            "ba.md mock path must cross-check against impeccable skills"
        )


# ---------------------------------------------------------------------------
# AC-4: ba.md no-mock fallback to named impeccable rules
# ---------------------------------------------------------------------------

class TestAC4BaNoMockFallback:
    def _text(self) -> str:
        return BA_MD.read_text(encoding="utf-8").lower()

    def test_mentions_no_mock_fallback(self):
        text = self._text()
        assert "no mock" in text or "without a mock" in text or "no mock is attached" in text, (
            "ba.md must describe the no-mock fallback explicitly"
        )

    def test_references_named_impeccable_rules(self):
        text = self._text()
        assert "spacing scale" in text, "ba.md fallback must reference the spacing scale rule"
        assert "contrast" in text, "ba.md fallback must reference contrast ratios"
        assert "breakpoint" in text, "ba.md fallback must reference responsive breakpoints"
        assert "component naming" in text or "component name" in text, (
            "ba.md fallback must reference component naming"
        )


# ---------------------------------------------------------------------------
# AC-5: coder.md frontend path
# ---------------------------------------------------------------------------

class TestAC5CoderFrontend:
    def _text(self) -> str:
        return CODER_MD.read_text(encoding="utf-8")

    def test_loads_context_mjs_before_ui(self):
        text = self._text()
        assert "node " + CONTEXT_MJS_PATH in text, (
            "coder.md must instruct loading impeccable via `node .../context.mjs`"
        )

    def test_mock_is_pixel_target(self):
        text = self._text().lower()
        assert "mock" in text and "pixel" in text, (
            "coder.md must treat an attached mock as the pixel/visual target"
        )

    def test_must_pass_detect_gate(self):
        text = self._text().lower()
        assert "impeccable detect" in text, (
            "coder.md must require passing the `impeccable detect` gate"
        )
        assert "first" in text, "coder.md must require detect passes on the first try"


# ---------------------------------------------------------------------------
# AC-6: ba.md forbids generic UI AC language
# ---------------------------------------------------------------------------

class TestAC6NoGenericLanguage:
    def test_forbids_follows_design_system(self):
        text = BA_MD.read_text(encoding="utf-8").lower()
        assert "follows design system" in text, (
            "ba.md must name 'follows design system' as a forbidden vague phrase"
        )
        # It must be framed as something to avoid, near 'avoid'/'no'/'forbid'/'not'.
        idx = text.find("follows design system")
        window = text[max(0, idx - 200): idx]
        assert any(kw in window for kw in ("avoid", "no ", "not ", "forbid", "never", "vague", "generic")), (
            "ba.md must frame 'follows design system' as forbidden/vague"
        )

    def test_requires_testable_against_value_or_rule(self):
        text = BA_MD.read_text(encoding="utf-8").lower()
        assert "testable" in text and ("value" in text or "rule" in text), (
            "ba.md must require every UI AC item be testable against a value or rule name"
        )


# ---------------------------------------------------------------------------
# AC-7: impeccable detect baseline tracked
# ---------------------------------------------------------------------------

class TestAC7BaselineTracked:
    def test_baseline_doc_exists(self):
        assert BASELINE_DOC.exists(), (
            f"{BASELINE_DOC} must exist to track the impeccable detect baseline"
        )

    def test_baseline_doc_records_metric_and_mechanism(self):
        text = BASELINE_DOC.read_text(encoding="utf-8").lower()
        assert "baseline" in text, "baseline doc must state a baseline"
        assert "impeccable detect" in text, "baseline doc must reference the impeccable detect gate"
        assert "regression" in text or "no regression" in text, (
            "baseline doc must state the no-regression expectation"
        )
