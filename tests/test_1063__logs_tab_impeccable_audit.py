"""
Tests for issue #1063: Audit and fix Logs tab impeccable findings.

AC1: Impeccable detect runs on logpanel.js and progress-activity.js with zero findings
AC2: All anti-patterns fixed using only foundation tokens (no hardcoded color/spacing values)
AC3: Chip and severity labels meet contrast requirements in dark theme
AC4: Row spacing and timestamp legibility pass impeccable rules (spacing scale compliance)
AC5: Alignment issues resolved across all log row elements
AC6: npm run build executed and bundle.js committed if src/ files changed
AC7: Log filtering functionality remains intact after all changes
AC8: Bundle integrity verified (no broken imports, no build errors)
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
LOGPANEL = ROOT / "apps/dashboard/static/src/logpanel.js"
PROGRESS = ROOT / "apps/dashboard/static/src/progress-activity.js"
PROJECT_HTML = ROOT / "apps/dashboard/static/project.html"
BUNDLE = ROOT / "apps/dashboard/static/dist/bundle.js"

# Spacing scale per DESIGN.md: 4·8·12·16·24·32 px
# 0, 1, 2 are accepted for borders/micro-adjustments only
ALLOWED_SPACING_PX = {0, 4, 8, 12, 16, 24, 32}
BORDER_WIDTH_PX = {1, 2}  # border widths are not spacing tokens


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── AC1: Zero impeccable findings on src modules ─────────────────────────────

def test_ac1_zero_impeccable_findings_logpanel():
    """AC1: impeccable detect on logpanel.js reports zero findings."""
    result = subprocess.run(
        ["npx", "impeccable", "detect", str(LOGPANEL)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = (result.stdout + result.stderr).strip()
    # impeccable detect outputs filename line then findings; if only filename → zero findings
    # Strip the filename line; any remaining lines are findings
    lines = [line for line in output.splitlines() if line.strip() and not line.strip().startswith(str(LOGPANEL).split("/")[-1]) and not line.strip() == str(LOGPANEL)]
    assert not any("[" in line for line in lines), f"impeccable findings in logpanel.js:\n{output}"


def test_ac1_zero_impeccable_findings_progress_activity():
    """AC1: impeccable detect on progress-activity.js reports zero findings."""
    result = subprocess.run(
        ["npx", "impeccable", "detect", str(PROGRESS)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    output = (result.stdout + result.stderr).strip()
    lines = [line for line in output.splitlines() if line.strip() and not line.strip() == str(PROGRESS)]
    assert not any("[" in line for line in lines), f"impeccable findings in progress-activity.js:\n{output}"


# ── AC2: No hardcoded hex colors in src modules ──────────────────────────────

# Matches CSS hex colors like #rgb, #rrggbb, #rrggbbaa — NOT HTML entities (&#NNN;)
HEX_RE = re.compile(r'(?<![&a-zA-Z0-9_])#[0-9a-fA-F]{3,8}(?![0-9a-fA-F;])')


def test_ac2_no_hardcoded_hex_in_logpanel():
    """AC2: logpanel.js must not contain hardcoded hex color values."""
    content = _read(LOGPANEL)
    # Remove comment lines
    code_only = re.sub(r'/\*.*?\*/', "", content, flags=re.DOTALL)
    code_only = re.sub(r'//[^\n]*', "", code_only)
    matches = HEX_RE.findall(code_only)
    assert not matches, f"Hardcoded hex colors in logpanel.js: {matches}"


def test_ac2_no_hardcoded_hex_in_progress_activity():
    """AC2: progress-activity.js must not contain hardcoded hex color values outside CSS variables.

    HTML entities like &#9650; (▲) are exempt — they are not colors.
    """
    content = _read(PROGRESS)
    code_only = re.sub(r'/\*.*?\*/', "", content, flags=re.DOTALL)
    code_only = re.sub(r'//[^\n]*', "", code_only)
    matches = HEX_RE.findall(code_only)
    assert not matches, f"Hardcoded hex colors in progress-activity.js: {matches}"


# ── AC3: Severity chips meet contrast requirements in dark theme ──────────────

def test_ac3_info_chip_contrast_token():
    """AC3: INFO severity chip must use a token with sufficient contrast on dark --surface-2.

    --text-sub (#6b7280) on --surface-2 (#1e1e1e) = ~3.5:1 → fails WCAG AA.
    Must use --text-muted (#9ca3af) or stronger for ≥4.5:1 on dark surfaces.
    """
    content = _read(PROJECT_HTML)
    match = re.search(r'\.evl-sev-chip--info\s*\{([^}]+)\}', content)
    assert match, ".evl-sev-chip--info rule not found in project.html"
    chip_css = match.group(1)
    assert "var(--text-sub)" not in chip_css, (
        "INFO chip uses --text-sub which has insufficient contrast (~3.5:1) "
        "on dark --surface-2. Use --text-muted or stronger."
    )


def test_ac3_warn_chip_uses_amber_token():
    """AC3: WARN chip uses --amber token (passes dark theme contrast)."""
    content = _read(PROJECT_HTML)
    match = re.search(r'\.evl-sev-chip--warn\s*\{([^}]+)\}', content)
    assert match, ".evl-sev-chip--warn rule not found"
    chip_css = match.group(1)
    assert "var(--amber)" in chip_css, "WARN chip must use --amber token"
    assert "var(--amber-bg)" in chip_css, "WARN chip must use --amber-bg token"


def test_ac3_error_chip_uses_red_token():
    """AC3: ERROR chip uses --red token (passes dark theme contrast)."""
    content = _read(PROJECT_HTML)
    match = re.search(r'\.evl-sev-chip--error\s*\{([^}]+)\}', content)
    assert match, ".evl-sev-chip--error rule not found"
    chip_css = match.group(1)
    assert "var(--red)" in chip_css, "ERROR chip must use --red token"
    assert "var(--red-bg)" in chip_css, "ERROR chip must use --red-bg token"


# ── AC4: Spacing scale compliance in progress-activity.js PA_CSS ─────────────

def _extract_pa_css(content: str) -> str:
    """Extract the PA_CSS template literal from progress-activity.js."""
    match = re.search(r'export const PA_CSS = `(.*?)`;', content, re.DOTALL)
    assert match, "PA_CSS not found in progress-activity.js"
    return match.group(1)


# Properties where the spacing scale (4·8·12·16·24·32) must be applied.
# font-size, width, height, max-height, border-radius have their own scales.
SPACING_PROPS = re.compile(
    r'(margin|padding|gap|column-gap|row-gap|outline-offset)\s*:\s*([^;]+);',
    re.IGNORECASE,
)


def test_ac4_pa_css_spacing_scale_compliance():
    """AC4: margin/padding/gap in PA_CSS must only use DESIGN.md spacing scale values.

    DESIGN.md spacing scale: 4·8·12·16·24·32 px.
    Exemptions: 0, 1px (micro-pixel borders/offsets), 2px (border widths), 3px.
    font-size, width, height, max-height, border-radius are NOT checked here.
    """
    content = _read(PROGRESS)
    pa_css = _extract_pa_css(content)

    EXEMPT = {0, 1, 2, 3}  # borders, near-zero micro-adjustments
    off_scale = []

    for prop_match in SPACING_PROPS.finditer(pa_css):
        prop_name = prop_match.group(1)
        values_str = prop_match.group(2)
        for val_str in re.findall(r'\b(\d+)px', values_str):
            val = int(val_str)
            if val in EXEMPT:
                continue
            if val not in ALLOWED_SPACING_PX:
                off_scale.append(f"{prop_name}: ...{val}px...")

    assert not off_scale, (
        "Off-scale spacing/gap/margin/padding values in PA_CSS:\n  "
        + "\n  ".join(sorted(set(off_scale)))
        + f"\nAllowed px values: {sorted(ALLOWED_SPACING_PX)}"
    )


# ── AC5: Log row alignment — flex-start alignment on log rows ─────────────────

def test_ac5_evl_log_row_uses_flex_alignment():
    """AC5: .evl-log-row should use flex with proper alignment."""
    content = _read(PROJECT_HTML)
    match = re.search(r'\.evl-log-row\s*\{([^}]+)\}', content)
    assert match, ".evl-log-row rule not found in project.html"
    row_css = match.group(1)
    assert "display" in row_css and "flex" in row_css, ".evl-log-row must be a flex container"


def test_ac5_lr_time_uses_flex_shrink():
    """AC5: .lr-time (timestamp) must have flex-shrink: 0 to prevent layout collapse."""
    content = _read(PROJECT_HTML)
    match = re.search(r'\.lr-time\s*\{([^}]+)\}', content)
    assert match, ".lr-time rule not found in project.html"
    css = match.group(1)
    assert "flex-shrink" in css, ".lr-time must have flex-shrink to prevent timestamp squashing"


# ── AC7: Log filtering functionality intact ───────────────────────────────────

def test_ac7_evl_set_filter_function_exists():
    """AC7: evlSetFilter function must still exist in project.html."""
    content = _read(PROJECT_HTML)
    assert "function evlSetFilter(" in content, "evlSetFilter function is missing from project.html"


def test_ac7_evl_toggle_errors_only_exists():
    """AC7: evlToggleErrorsOnly function must still exist in project.html."""
    content = _read(PROJECT_HTML)
    assert "function evlToggleErrorsOnly(" in content, "evlToggleErrorsOnly is missing from project.html"


def test_ac7_filter_chips_render_in_html():
    """AC7: Source filter chips (All, Agent & Sprint, Dashboard, GitHub) present in Logs toolbar."""
    content = _read(PROJECT_HTML)
    assert "evlSetFilter('all')" in content, "All filter chip missing"
    assert "evlSetFilter('agent')" in content, "Agent filter chip missing"
    assert "evlSetFilter('dashboard')" in content, "Dashboard filter chip missing"
    assert "evlSetFilter('github')" in content, "GitHub filter chip missing"


# ── AC6 / AC8: Bundle exists and contains key exports ────────────────────────

def test_ac6_bundle_js_exists():
    """AC6/AC8: static/dist/bundle.js must exist (built after src/ changes)."""
    assert BUNDLE.exists(), f"bundle.js not found at {BUNDLE}"
    assert BUNDLE.stat().st_size > 1000, "bundle.js is suspiciously small"


def test_ac8_bundle_contains_key_exports():
    """AC8: bundle.js must expose the globals declared in index.js."""
    content = _read(BUNDLE)
    # These are the symbols explicitly assigned to root.* in index.js
    assert "colorizeLogLine" in content, "colorizeLogLine missing from bundle"
    assert "renderProgressActivity" in content, "renderProgressActivity missing from bundle"
    assert "updateProgressActivityLog" in content, "updateProgressActivityLog missing from bundle"
    assert "paToggleLog" in content, "paToggleLog missing from bundle"
    # Verify the bundle is a valid IIFE (not truncated)
    assert content.strip().startswith("(()"), "bundle.js must start with IIFE wrapper"
