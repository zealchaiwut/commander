"""Tests for issue #209: Fix Estimation Size Definitions S, M, and L."""
import ast
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[3]


# ── AC1: S defined as ~1–5 min ────────────────────────────────────────────────

def test_sprint_planner_size_s_is_5_minutes():
    """SIZE_MINUTES["S"] == 5 in sprint_planner.py."""
    planner = REPO_ROOT / "scripts" / "sprint_planner.py"
    src = planner.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SIZE_MINUTES":
                    d = ast.literal_eval(node.value)
                    assert d["S"] == 5, f"Expected SIZE_MINUTES['S']==5, got {d['S']}"
                    return
    raise AssertionError("SIZE_MINUTES not found in sprint_planner.py")


def test_estimate_issue_apply_label_s_description():
    """apply_label size_descriptions dict has '1–5 min' for S."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py").read_text()
    assert "1–5 min" in src, "Expected '1–5 min' in estimate_issue.py size_descriptions"


def test_estimator_agent_md_s_definition():
    """apps/dashboard estimator.md size table row S contains '1–5 min'."""
    md = (REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md").read_text()
    assert "1–5 min" in md, "Expected '1–5 min' in apps/dashboard/.claude/agents/estimator.md"


def test_root_estimator_agent_md_s_definition():
    """Root .claude/agents/estimator.md size definition contains '1–5 min'."""
    md = (REPO_ROOT / ".claude" / "agents" / "estimator.md").read_text()
    assert "1–5 min" in md, "Expected '1–5 min' in .claude/agents/estimator.md"


def test_seed_test_issues_label_s_description():
    """seed_test_issues.py size-S label description contains '1–5 min'."""
    src = (REPO_ROOT / "scripts" / "seed_test_issues.py").read_text()
    assert "1–5 min" in src, "Expected '1–5 min' for size-S label in seed_test_issues.py"


# ── AC2: M defined as ~15 min ─────────────────────────────────────────────────

def test_sprint_planner_size_m_is_15_minutes():
    """SIZE_MINUTES["M"] == 15 in sprint_planner.py."""
    planner = REPO_ROOT / "scripts" / "sprint_planner.py"
    src = planner.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SIZE_MINUTES":
                    d = ast.literal_eval(node.value)
                    assert d["M"] == 15, f"Expected SIZE_MINUTES['M']==15, got {d['M']}"
                    return
    raise AssertionError("SIZE_MINUTES not found in sprint_planner.py")


def test_estimate_issue_apply_label_m_description():
    """apply_label size_descriptions dict has '~15 min' for M."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py").read_text()
    assert "~15 min" in src, "Expected '~15 min' in estimate_issue.py size_descriptions"


def test_estimator_agent_md_m_definition():
    """apps/dashboard estimator.md size table row M contains '15 min'."""
    md = (REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md").read_text()
    assert "15 min" in md, "Expected '15 min' in apps/dashboard/.claude/agents/estimator.md"


def test_root_estimator_agent_md_m_definition():
    """Root .claude/agents/estimator.md size definition contains '15 min'."""
    md = (REPO_ROOT / ".claude" / "agents" / "estimator.md").read_text()
    assert "15 min" in md, "Expected '15 min' in .claude/agents/estimator.md"


def test_seed_test_issues_label_m_description():
    """seed_test_issues.py size-M label description contains '15 min'."""
    src = (REPO_ROOT / "scripts" / "seed_test_issues.py").read_text()
    assert "15 min" in src, "Expected '15 min' for size-M label in seed_test_issues.py"


# ── AC3: L defined as ~30 min ─────────────────────────────────────────────────

def test_sprint_planner_size_l_is_30_minutes():
    """SIZE_MINUTES["L"] == 30 in sprint_planner.py."""
    planner = REPO_ROOT / "scripts" / "sprint_planner.py"
    src = planner.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SIZE_MINUTES":
                    d = ast.literal_eval(node.value)
                    assert d["L"] == 30, f"Expected SIZE_MINUTES['L']==30, got {d['L']}"
                    return
    raise AssertionError("SIZE_MINUTES not found in sprint_planner.py")


def test_estimate_issue_apply_label_l_description():
    """apply_label size_descriptions dict has '~30 min' for L."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py").read_text()
    assert "~30 min" in src, "Expected '~30 min' in estimate_issue.py size_descriptions"


def test_estimator_agent_md_l_definition():
    """apps/dashboard estimator.md size table row L contains '30 min'."""
    md = (REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md").read_text()
    assert "30 min" in md, "Expected '30 min' in apps/dashboard/.claude/agents/estimator.md"


def test_root_estimator_agent_md_l_definition():
    """Root .claude/agents/estimator.md size definition contains '30 min'."""
    md = (REPO_ROOT / ".claude" / "agents" / "estimator.md").read_text()
    assert "30 min" in md, "Expected '30 min' in .claude/agents/estimator.md"


def test_seed_test_issues_label_l_description():
    """seed_test_issues.py size-L label description contains '30 min'."""
    src = (REPO_ROOT / "scripts" / "seed_test_issues.py").read_text()
    assert "30 min" in src, "Expected '30 min' for size-L label in seed_test_issues.py"


# ── AC4: All locations updated consistently ────────────────────────────────────

def test_claude_md_size_scale_updated():
    """CLAUDE.md Size scale line reflects minutes (S=1–5min, M=~15min, L=~30min)."""
    md = (REPO_ROOT / "CLAUDE.md").read_text()
    assert "S=1–5min" in md, "Expected 'S=1–5min' in CLAUDE.md"
    assert "M=~15min" in md, "Expected 'M=~15min' in CLAUDE.md"
    assert "L=~30min" in md, "Expected 'L=~30min' in CLAUDE.md"


def test_sprint_planner_no_size_hours_constant():
    """sprint_planner.py no longer has the old SIZE_HOURS constant."""
    src = (REPO_ROOT / "scripts" / "sprint_planner.py").read_text()
    assert "SIZE_HOURS" not in src, "SIZE_HOURS should be removed from sprint_planner.py"


def test_sprint_planner_print_uses_min_units():
    """sprint_planner.py sprint summary prints minutes, not hours."""
    src = (REPO_ROOT / "scripts" / "sprint_planner.py").read_text()
    assert "min\")" in src or "min)" in src, "Expected sprint summary to print 'min' units"
    assert "est {total_minutes}" in src, "Expected total_minutes variable in sprint_planner.py"


# ── AC5: XL not unintentionally altered ───────────────────────────────────────

def test_sprint_planner_xl_still_present():
    """SIZE_MINUTES still contains XL."""
    planner = REPO_ROOT / "scripts" / "sprint_planner.py"
    src = planner.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SIZE_MINUTES":
                    d = ast.literal_eval(node.value)
                    assert "XL" in d, "XL must still exist in SIZE_MINUTES"
                    return
    raise AssertionError("SIZE_MINUTES not found in sprint_planner.py")


def test_estimator_agent_md_xl_still_present():
    """apps/dashboard estimator.md still defines XL size."""
    md = (REPO_ROOT / "apps" / "dashboard" / ".claude" / "agents" / "estimator.md").read_text()
    assert "XL" in md, "XL size definition must still be present in estimator.md"


def test_estimate_issue_valid_sizes_unchanged():
    """apply_label still recognises S, M, L, XL as valid sizes."""
    src = (REPO_ROOT / "services" / "sprint_manager" / "estimate_issue.py").read_text()
    assert '"S", "M", "L", "XL"' in src or '{"S", "M", "L", "XL"}' in src, (
        "Valid sizes set must still include S, M, L, XL in estimate_issue.py"
    )


def test_no_xs_size_introduced():
    """No XS size was accidentally added to sprint_planner.py."""
    src = (REPO_ROOT / "scripts" / "sprint_planner.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SIZE_MINUTES":
                    d = ast.literal_eval(node.value)
                    assert "XS" not in d, "XS must NOT be in SIZE_MINUTES"
                    return
