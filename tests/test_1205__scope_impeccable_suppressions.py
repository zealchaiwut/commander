"""Acceptance tests for issue #1205 — Scope impeccable rule suppressions.

AC map:
  AC1   The global detector.ignoreRules array is removed from
        .impeccable/config.json (file may be deleted if nothing else remains).
  AC2   Each previously suppressed rule (low-contrast, cramped-padding,
        tiny-text, all-caps-body, single-font, skipped-heading, ai-color-palette,
        dark-glow, em-dash-overuse, layout-transition) is either fixed at the
        source or suppressed via a per-file inline ignore comment scoped to the
        exact pre-existing violation location.
  AC3   Running impeccable detect on the full project surface produces zero
        findings attributable to newly introduced code — any remaining findings
        carry a scoped suppression.
  AC4   The impeccable detect gate rejects a real contrast violation introduced
        in a new component — the gate is still meaningful after removing the
        global mute.
  AC5   No files outside the suppression cleanup scope are modified.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPECCABLE_CONFIG = REPO_ROOT / ".impeccable" / "config.json"
STATIC_DIR = REPO_ROOT / "apps" / "dashboard" / "static"

GLOBALLY_SUPPRESSED_RULES = [
    "low-contrast",
    "tiny-text",
    "all-caps-body",
    "single-font",
    "skipped-heading",
    "ai-color-palette",
    "dark-glow",
    "em-dash-overuse",
    "layout-transition",
    "cramped-padding",
]

# Files that had violations under the globally suppressed rules
FILES_WITH_VIOLATIONS: dict[str, list[str]] = {
    "diagnostics.html": ["low-contrast", "single-font", "em-dash-overuse"],
    "home-preview.html": ["low-contrast", "tiny-text", "single-font", "em-dash-overuse"],
    "project.html": [
        "layout-transition",
        "cramped-padding",
        "tiny-text",
        "all-caps-body",
        "single-font",
        "skipped-heading",
        "ai-color-palette",
        "dark-glow",
        "em-dash-overuse",
    ],
    "run_browser.html": ["single-font"],
}


# ── AC1: no global ignoreRules in .impeccable/config.json ────────────────────


class TestNoGlobalIgnoreRules:
    def test_config_has_no_ignoreRules_key(self):
        """AC1 — config.json must not contain a detector.ignoreRules array."""
        if not IMPECCABLE_CONFIG.exists():
            return  # file deleted entirely is also valid per AC1
        config = json.loads(IMPECCABLE_CONFIG.read_text())
        detector = config.get("detector", {})
        assert "ignoreRules" not in detector, (
            "AC1: .impeccable/config.json must not contain detector.ignoreRules — "
            "global rule muting makes the impeccable gate meaningless"
        )

    def test_config_no_suppressed_rules_present(self):
        """AC1 — none of the previously suppressed rules appear in config.json."""
        if not IMPECCABLE_CONFIG.exists():
            return
        config_text = IMPECCABLE_CONFIG.read_text()
        for rule in GLOBALLY_SUPPRESSED_RULES:
            assert rule not in config_text, (
                f"AC1: rule '{rule}' must not appear in .impeccable/config.json — "
                "it was globally suppressed and must now be scoped or fixed"
            )


# ── AC2: scoped per-file inline suppressions ─────────────────────────────────


class TestScopedInlineSuppressions:
    """Each file that had pre-existing violations must carry an inline
    impeccable-disable comment for each suppressed rule.  The comments
    travel with the file, not the project-level config."""

    @pytest.mark.parametrize("filename,rules", list(FILES_WITH_VIOLATIONS.items()))
    def test_file_has_inline_disable_for_each_rule(self, filename, rules):
        """AC2 — HTML file must have impeccable-disable comment for each affected rule."""
        filepath = STATIC_DIR / filename
        assert filepath.exists(), f"{filename} must exist in static dir"
        content = filepath.read_text(encoding="utf-8")
        for rule in rules:
            has_suppress = (
                f"impeccable-disable {rule}" in content
                or f"impeccable-disable-line {rule}" in content
                or f"impeccable-disable-next-line {rule}" in content
                # multi-rule disable: "impeccable-disable rule1, rule2"
                or any(
                    rule in part
                    for part in content.split("impeccable-disable")
                    if rule in part
                )
            )
            assert has_suppress, (
                f"AC2: {filename} must contain an inline impeccable-disable comment "
                f"for '{rule}' (pre-existing violation needs scoped suppression)"
            )

    def test_project_todo_js_has_broken_image_suppression(self):
        """AC2 — project-todo.js broken-image placeholder img needs scoped suppression."""
        filepath = STATIC_DIR / "project-todo.js"
        assert filepath.exists(), "project-todo.js must exist"
        content = filepath.read_text(encoding="utf-8")
        has_suppress = (
            "impeccable-disable broken-image" in content
            or "impeccable-disable-line broken-image" in content
            or "impeccable-disable-next-line broken-image" in content
        )
        assert has_suppress, (
            "AC2: project-todo.js must contain a scoped impeccable-disable-line "
            "broken-image comment at the placeholder <img> element (line ~130)"
        )

    def test_run_browser_flat_type_hierarchy_suppression(self):
        """AC2 — run_browser.html flat-type-hierarchy needs scoped suppression."""
        filepath = STATIC_DIR / "run_browser.html"
        assert filepath.exists(), "run_browser.html must exist"
        content = filepath.read_text(encoding="utf-8")
        # Accept single-rule or multi-rule disable comments
        has_suppress = any(
            "flat-type-hierarchy" in part
            for part in content.split("impeccable-disable")
            if "flat-type-hierarchy" in part
        )
        assert has_suppress, (
            "AC2: run_browser.html must have an inline impeccable-disable comment "
            "for flat-type-hierarchy (pre-existing violation)"
        )


# ── AC3: impeccable detect passes on the full surface ────────────────────────


@pytest.mark.skipif(
    not shutil.which("npx"),
    reason="npx not available — skip impeccable gate (see memory: coder-clone-no-node.md)",
)
class TestImpeccableDetectPasses:
    def test_detect_exits_zero_on_static_dir(self):
        """AC3 — impeccable detect must exit 0 on apps/dashboard/static/."""
        result = subprocess.run(
            ["npx", "impeccable", "detect", str(STATIC_DIR)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"AC3: impeccable detect found unscoped violations:\n"
            f"{result.stdout}\n{result.stderr}"
        )

    def test_detect_json_has_no_unscoped_findings(self):
        """AC3 — all findings in JSON output must be zero after scoped suppressions."""
        result = subprocess.run(
            ["npx", "impeccable", "detect", str(STATIC_DIR), "--json"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Exit 0 means no findings at all
        assert result.returncode == 0, (
            f"AC3: impeccable detect --json reported findings:\n"
            f"{result.stdout}\n{result.stderr}"
        )


# ── AC4: gate is still meaningful — detects real violations ──────────────────


@pytest.mark.skipif(
    not shutil.which("npx"),
    reason="npx not available — skip impeccable gate (see memory: coder-clone-no-node.md)",
)
class TestGateStillMeaningful:
    def test_low_contrast_violation_in_new_file_is_detected(self):
        """AC4 — a deliberate low-contrast violation in a fresh file is still caught."""
        bad_html = """<!DOCTYPE html>
<html>
<head>
<style>
body { font-family: sans-serif; }
.test-bad-contrast { color: #cccccc; background: #ffffff; font-size: 16px; }
</style>
</head>
<body>
<p class="test-bad-contrast">This text has insufficient contrast for WCAG AA.</p>
</body>
</html>
"""
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", delete=False, prefix="impeccable_test_"
        ) as f:
            f.write(bad_html)
            tmp_path = Path(f.name)

        try:
            result = subprocess.run(
                ["npx", "impeccable", "detect", str(tmp_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            # Must find the low-contrast violation (exit 2 means findings)
            output = result.stdout + result.stderr
            assert result.returncode != 0, (
                "AC4: impeccable detect failed to catch a deliberate low-contrast "
                "violation in a new component — the gate is no longer meaningful. "
                "This means the low-contrast rule is still being globally suppressed."
            )
            assert "low-contrast" in output or "contrast" in output.lower(), (
                "AC4: impeccable detect output must reference 'low-contrast' when "
                "a real contrast violation exists in a new file"
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_global_config_does_not_suppress_new_violations(self):
        """AC4 — running with the project config must still catch violations in new code."""
        bad_html = """<!DOCTYPE html>
<html>
<head>
<style>
body { font-family: sans-serif; }
.new-component { color: #d0d0d0; background: #ffffff; padding: 4px; }
</style>
</head>
<body>
<p class="new-component">Low contrast new component text.</p>
</body>
</html>
"""
        with tempfile.NamedTemporaryFile(
            suffix=".html", mode="w", delete=False, prefix="impeccable_newcomp_"
        ) as f:
            f.write(bad_html)
            tmp_path = Path(f.name)

        try:
            result = subprocess.run(
                ["npx", "impeccable", "detect", str(tmp_path)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=60,
            )
            # With no inline suppressions and no global config muting low-contrast,
            # this must be detected.
            assert result.returncode != 0, (
                "AC4: global config is still muting low-contrast — a new component "
                "with a real low-contrast violation was not detected. "
                "Remove detector.ignoreRules from .impeccable/config.json."
            )
        finally:
            tmp_path.unlink(missing_ok=True)
