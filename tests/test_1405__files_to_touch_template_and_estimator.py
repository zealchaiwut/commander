"""Tests for issue #1405: Add 'Files to touch' section to feature template and estimator.

AC1 — feature.md contains '## Files to touch' section marked optional, with format comment
AC2 — template renders correctly (no broken syntax)
AC3 — Bulk-create BA prompt includes '## Files to touch' as a stub section
AC4 — When '## Files to touch' lists paths, estimator includes them in files_likely_affected
AC5 — When section is empty or absent, estimator inference is unchanged (no regression)
AC6 — Ticket with services/sprint_manager/pipeline.py → files_likely_affected includes it
AC7 — Listed paths not inferred independently still appear in files_likely_affected
AC8 — Inferred paths not listed still appear in files_likely_affected (merge, not replace)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

FEATURE_TEMPLATE = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "feature.md"
SERVER_PY = REPO_ROOT / "apps" / "dashboard" / "server.py"

from services.sprint_manager.estimate_issue import (  # noqa: E402
    parse_files_to_touch,
    run_estimator,
)


# ── AC1: feature.md has ## Files to touch with optional marker and format comment ──

def test_feature_template_has_files_to_touch_section():
    """AC1: feature.md contains a '## Files to touch' section."""
    content = FEATURE_TEMPLATE.read_text()
    assert re.search(r"^##\s+Files to touch", content, re.MULTILINE | re.IGNORECASE), \
        "feature.md must contain a '## Files to touch' heading"


def test_feature_template_files_to_touch_marked_optional():
    """AC1: '## Files to touch' section is marked as optional."""
    content = FEATURE_TEMPLATE.read_text()
    # Find the section and check nearby text for 'optional'
    match = re.search(
        r"(##\s+Files to touch.*?)(?=\n##|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "feature.md missing '## Files to touch' section"
    section_text = match.group(1)
    assert "optional" in section_text.lower(), \
        "'## Files to touch' section must be marked as optional"


def test_feature_template_files_to_touch_has_format_comment():
    """AC1: section has a comment explaining expected format (one path per line)."""
    content = FEATURE_TEMPLATE.read_text()
    match = re.search(
        r"(##\s+Files to touch.*?)(?=\n##|\Z)",
        content,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "feature.md missing '## Files to touch' section"
    section_text = match.group(1)
    # Should mention paths or per-line format
    assert re.search(r"path|per line|one.*line", section_text, re.IGNORECASE), \
        "section should explain expected format (one repo-relative path per line)"


# ── AC2: template has no broken YAML frontmatter or markdown syntax ──

def test_feature_template_yaml_frontmatter_valid():
    """AC2: template frontmatter is well-formed (opens and closes with ---)."""
    content = FEATURE_TEMPLATE.read_text()
    assert content.startswith("---"), "feature.md must start with YAML frontmatter block '---'"
    parts = content.split("---", 2)
    assert len(parts) >= 3, "feature.md frontmatter must have a closing '---'"


def test_feature_template_no_unclosed_html_comments():
    """AC2: template has no unclosed HTML comments."""
    content = FEATURE_TEMPLATE.read_text()
    opens = content.count("<!--")
    closes = content.count("-->")
    assert opens == closes, \
        f"feature.md has mismatched HTML comments: {opens} opens, {closes} closes"


# ── AC3: bulk-create BA prompt includes ## Files to touch stub ──

def test_server_ba_prompt_includes_files_to_touch_section():
    """AC3: the bulk-create BA prompt in server.py lists '## Files to touch' as a section."""
    content = SERVER_PY.read_text()
    # Both the single-draft and bulk-create paths should include this section heading
    assert "Files to touch" in content, \
        "server.py BA prompts must include '## Files to touch' as a draft section"


# ── Helpers for run_estimator mocking ──

def _mock_process(stdout: str, returncode: int = 0):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = ""
    return p


def _base_payload(**overrides) -> dict:
    base = {
        "size": "M",
        "estimated_hours": 2,
        "confidence": "medium",
        "files_likely_affected": ["apps/dashboard/server.py"],
        "files_touched": ["apps/dashboard/server.py"],
        "depends_on": [],
        "blocks": [],
        "risk_flags": [],
        "summary": "Some feature.",
    }
    base.update(overrides)
    return base


# ── AC4: explicit paths merged into files_likely_affected ──

def test_run_estimator_merges_explicit_paths_into_files_likely_affected():
    """AC4: paths from '## Files to touch' appear in files_likely_affected."""
    body = (
        "## What & Why\nDoes something.\n\n"
        "## Files to touch\nservices/sprint_manager/pipeline.py\n\n"
        "## Acceptance Criteria\n- [ ] Something\n"
    )
    payload = _base_payload(
        files_likely_affected=["apps/dashboard/server.py"],
        files_touched=["apps/dashboard/server.py"],
    )
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    assert estimate is not None
    assert "services/sprint_manager/pipeline.py" in estimate["files_likely_affected"]


# ── AC5: empty / absent section → no regression ──

def test_run_estimator_empty_files_to_touch_no_regression():
    """AC5: '## Files to touch' with no paths leaves inferred paths unchanged."""
    body = (
        "## What & Why\nDoes something.\n\n"
        "## Files to touch\n\n"
        "## Acceptance Criteria\n- [ ] Something\n"
    )
    inferred = ["apps/dashboard/server.py", "services/sprint_manager/models.py"]
    payload = _base_payload(
        files_likely_affected=inferred[:],
        files_touched=inferred[:],
    )
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    assert estimate is not None
    assert estimate["files_likely_affected"] == inferred


def test_run_estimator_absent_files_to_touch_no_regression():
    """AC5: absent '## Files to touch' leaves inferred paths unchanged."""
    body = "## What & Why\nDoes something.\n\n## Acceptance Criteria\n- [ ] Something\n"
    inferred = ["apps/dashboard/server.py"]
    payload = _base_payload(
        files_likely_affected=inferred[:],
        files_touched=inferred[:],
    )
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    assert estimate is not None
    assert estimate["files_likely_affected"] == inferred


# ── AC6: specific path services/sprint_manager/pipeline.py ──

def test_run_estimator_pipeline_path_appears_in_files_likely_affected():
    """AC6: ticket with services/sprint_manager/pipeline.py in ## Files to touch produces it in estimate."""
    body = (
        "## What & Why\nSome pipeline change.\n\n"
        "## Files to touch\nservices/sprint_manager/pipeline.py\n\n"
        "## Acceptance Criteria\n- [ ] Something\n"
    )
    payload = _base_payload(
        files_likely_affected=["apps/dashboard/server.py"],
        files_touched=["apps/dashboard/server.py"],
    )
    issue_data = {"title": "Pipeline change", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    assert estimate is not None
    assert "services/sprint_manager/pipeline.py" in estimate["files_likely_affected"]


# ── AC7: explicit paths not inferred independently still appear ──

def test_run_estimator_explicit_paths_not_inferred_still_appear():
    """AC7: paths listed in ## Files to touch but not inferred still appear in files_likely_affected."""
    body = (
        "## Files to touch\n"
        "services/sprint_manager/pipeline.py\n"
        "services/sprint_manager/models.py\n"
    )
    # LLM only infers server.py, not the explicit paths
    payload = _base_payload(
        files_likely_affected=["apps/dashboard/server.py"],
        files_touched=["apps/dashboard/server.py"],
    )
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    fla = estimate["files_likely_affected"]
    assert "services/sprint_manager/pipeline.py" in fla
    assert "services/sprint_manager/models.py" in fla


# ── AC8: inferred paths not listed still appear (merge, not replace) ──

def test_run_estimator_inferred_paths_not_listed_still_appear():
    """AC8: inferred paths not in ## Files to touch still appear (union, not replace)."""
    body = (
        "## Files to touch\n"
        "services/sprint_manager/pipeline.py\n"
    )
    # LLM infers server.py, which is NOT in ## Files to touch
    payload = _base_payload(
        files_likely_affected=["apps/dashboard/server.py"],
        files_touched=["apps/dashboard/server.py"],
    )
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    fla = estimate["files_likely_affected"]
    assert "apps/dashboard/server.py" in fla
    assert "services/sprint_manager/pipeline.py" in fla


# ── parse_files_to_touch unit tests ──

def test_parse_files_to_touch_extracts_paths():
    """parse_files_to_touch returns paths from section."""
    body = (
        "## What & Why\nSomething.\n\n"
        "## Files to touch\n"
        "services/sprint_manager/pipeline.py\n"
        "apps/dashboard/server.py\n\n"
        "## Acceptance Criteria\n- [ ] Done\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/pipeline.py", "apps/dashboard/server.py"]


def test_parse_files_to_touch_empty_section():
    """parse_files_to_touch returns [] for empty section."""
    body = "## Files to touch\n\n## Acceptance Criteria\n- [ ] Done\n"
    assert parse_files_to_touch(body) == []


def test_parse_files_to_touch_absent_section():
    """parse_files_to_touch returns [] when section is absent."""
    body = "## What & Why\nSomething.\n\n## Acceptance Criteria\n- [ ] Done\n"
    assert parse_files_to_touch(body) == []


def test_parse_files_to_touch_skips_html_comments():
    """parse_files_to_touch ignores HTML comment lines."""
    body = (
        "## Files to touch\n"
        "<!-- optional: one repo-relative path per line -->\n"
        "services/sprint_manager/pipeline.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/pipeline.py"]


def test_parse_files_to_touch_stops_at_next_heading():
    """parse_files_to_touch stops collecting at the next section heading."""
    body = (
        "## Files to touch\n"
        "services/sprint_manager/pipeline.py\n"
        "## Acceptance Criteria\n"
        "apps/dashboard/server.py\n"
    )
    result = parse_files_to_touch(body)
    assert result == ["services/sprint_manager/pipeline.py"]
    assert "apps/dashboard/server.py" not in result


def test_parse_files_to_touch_deduplicates():
    """parse_files_to_touch preserves order and deduplicates."""
    body = (
        "## Files to touch\n"
        "services/sprint_manager/pipeline.py\n"
        "services/sprint_manager/pipeline.py\n"
        "apps/dashboard/server.py\n"
    )
    result = parse_files_to_touch(body)
    # dedup while preserving order
    assert result.count("services/sprint_manager/pipeline.py") == 1
    assert "apps/dashboard/server.py" in result


def test_run_estimator_no_duplicates_in_merged_paths():
    """Explicit path already inferred → appears once in files_likely_affected."""
    body = "## Files to touch\nservices/sprint_manager/pipeline.py\n"
    payload = _base_payload(
        files_likely_affected=["services/sprint_manager/pipeline.py", "apps/dashboard/server.py"],
        files_touched=["services/sprint_manager/pipeline.py"],
    )
    issue_data = {"title": "Test", "body": body}

    with patch("services.sprint_manager.estimate_issue.subprocess.run") as mock_run, \
         patch("services.sprint_manager.estimate_issue.load_agent_instructions", return_value=""):
        mock_run.return_value = _mock_process(json.dumps(payload))
        estimate, err = run_estimator(1, issue_data)

    assert err is None
    fla = estimate["files_likely_affected"]
    assert fla.count("services/sprint_manager/pipeline.py") == 1
