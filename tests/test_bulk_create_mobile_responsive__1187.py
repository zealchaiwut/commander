"""Tests for issue #1187: Reflow Bulk Create draft-card header on mobile (runs against UAT)"""
import os
import pytest


# CSS file is served from disk directly at UAT, so we verify the file itself
@pytest.fixture
def project_html_path():
    # UAT clone uses the same HTML file as the server; verify it's present
    uat_repo = os.environ.get("UAT_REPO", "/Users/zeal-server/dev/commander/uat")
    path = os.path.join(uat_repo, "apps/dashboard/static/project.html")
    assert os.path.exists(path), f"project.html not found at {path}"
    return path


# --- Acceptance Criteria ---

def test_bulk_create_mobile_responsive__card_head_stacks_at_375px(project_html_path):
    # AC: At 375px viewport: `.bc-card-head` stacks title above actions (column direction, left-aligned)
    with open(project_html_path, 'r') as f:
        html = f.read()
    # Verify the CSS rule exists in the HTML
    assert "@media (max-width:600px)" in html
    assert ".bc-card-head { flex-direction: column; align-items: flex-start; }" in html


def test_bulk_create_mobile_responsive__actions_wrap_at_375px(project_html_path):
    # AC: At 375px viewport: `.bc-card-actions` wraps buttons onto multiple lines when needed
    with open(project_html_path, 'r') as f:
        html = f.read()
    # Verify the CSS rule for actions wrapping exists
    assert ".bc-card-actions { flex-wrap: wrap; flex-shrink: unset; }" in html


def test_bulk_create_mobile_responsive__same_at_600px(project_html_path):
    # AC: At 600px viewport: same stacking/wrapping behavior applies
    with open(project_html_path, 'r') as f:
        html = f.read()
    # Both rules should be under the @media (max-width:600px) query
    media_start = html.find("@media (max-width:600px)")
    assert media_start != -1, "Media query not found"
    media_section = html[media_start:media_start + 2000]
    assert ".bc-card-head { flex-direction: column; align-items: flex-start; }" in media_section
    assert ".bc-card-actions { flex-wrap: wrap; flex-shrink: unset; }" in media_section


def test_bulk_create_mobile_responsive__desktop_unchanged(project_html_path):
    # AC: Desktop (>600px): layout unchanged — title and actions remain in a single row
    with open(project_html_path, 'r') as f:
        html = f.read()
    # Desktop rules should not have flex-direction: column or flex-wrap
    desktop_head_rule = ".bc-card-head { display: flex; align-items: center; gap: 8px; margin-bottom: 3px; }"
    desktop_actions_rule = ".bc-card-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }"
    # These should appear outside any @media query
    head_idx = html.find(desktop_head_rule)
    actions_idx = html.find(desktop_actions_rule)
    assert head_idx > 0, "Desktop .bc-card-head rule not found"
    assert actions_idx > 0, "Desktop .bc-card-actions rule not found"
    # Verify they're NOT inside a @media query by checking no @media precedes them in a reasonable window
    head_section = html[max(0, head_idx - 500):head_idx]
    assert "@media (max-width:600px)" not in head_section


def test_bulk_create_mobile_responsive__css_in_media_query(project_html_path):
    # AC: CSS lives under `@media (max-width:600px)` targeting `.bc-card-head` and `.bc-card-actions`
    with open(project_html_path, 'r') as f:
        html = f.read()
    # Find the media query and verify both rules are present
    media_pattern = "@media (max-width:600px)"
    assert media_pattern in html
    media_idx = html.find(media_pattern)
    # Extract next 1000 characters to capture the rules within the media query
    media_section = html[media_idx:media_idx + 1000]
    assert ".bc-card-head" in media_section
    assert ".bc-card-actions" in media_section
    assert "flex-direction: column" in media_section
    assert "flex-wrap: wrap" in media_section
