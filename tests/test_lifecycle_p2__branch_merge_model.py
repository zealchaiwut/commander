"""Sprint-lifecycle redesign P2: branch / merge model.

Design contract: docs/architecture/sprint-lifecycle.md
Tracking:        docs/milestones/sprint-lifecycle-redesign.md (P2)

Child sprint branches are created off the base sprint branch; per-ticket merges
land on base; develop is reached only at Merge Sprint (renamed Finish Sprint).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(_DASHBOARD_ROOT))

_SM_SRC = (
    _REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
).read_text(encoding="utf-8")
_SERVER_SRC = (_DASHBOARD_ROOT / "server.py").read_text(encoding="utf-8")
_FINISH_MODAL = (
    _DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "finish-modal.js"
).read_text(encoding="utf-8")
_BOARD_RENDER = (
    _DASHBOARD_ROOT / "static" / "src" / "sprint-board" / "board-render.js"
).read_text(encoding="utf-8")
_BUNDLE = (_DASHBOARD_ROOT / "static" / "dist" / "bundle.js").read_text(encoding="utf-8")

import sprint_manager as sm  # noqa: E402


class TestBranchHelpers:
    def test_label_base_strips_sub_index(self):
        assert sm._label_base("sprint-68.6") == "sprint-68"
        assert sm._label_base("sprint-68") == "sprint-68"

    def test_base_sprint_branch(self):
        assert sm._base_sprint_branch("sprint-68.6") == "sprint/sprint-68"
        assert sm._base_sprint_branch("sprint-68") == "sprint/sprint-68"

    def test_is_child_sprint_label(self):
        assert sm._is_child_sprint_label("sprint-68.1")
        assert not sm._is_child_sprint_label("sprint-68")


class TestCreateSprintBranch:
    def test_child_created_off_base_branch(self):
        calls: list[list[str]] = []

        def fake_run(*args):
            calls.append(list(args))

        with patch.object(sm, "_try", side_effect=[(False, "", ""), (False, "", ""), (True, "abc123", "")]), \
             patch.object(sm, "_run", side_effect=fake_run):
            sm._create_sprint_branch("sprint/sprint-68.6", parent_ref="sprint/sprint-68")

        branch_cmds = [c for c in calls if c and c[0] == "git" and c[1] == "branch"]
        assert branch_cmds, "expected git branch call"
        assert branch_cmds[0][3] == "abc123"
        assert "sprint/sprint-68.6" in branch_cmds[0]

    def test_base_created_off_develop_by_default(self):
        captured: dict[str, str] = {}

        def fake_create(branch: str, parent_ref: str = "develop") -> None:
            captured["branch"] = branch
            captured["parent_ref"] = parent_ref

        with patch.object(sm, "_create_sprint_branch", side_effect=fake_create):
            label = "sprint-68"
            sprint_branch = sm._sprint_branch_for_label(label)
            parent_ref = sm._base_sprint_branch(label) if sm._is_child_sprint_label(label) else "develop"
            sm._create_sprint_branch(sprint_branch, parent_ref=parent_ref)

        assert captured["parent_ref"] == "develop"


class TestCreateSprintPr:
    def test_no_auto_merge_to_develop(self):
        assert "gh\", \"pr\", \"merge\"" not in _SM_SRC.split("def _create_sprint_pr")[1].split("def ")[0]

    def test_pr_base_parameter(self):
        assert "pr_base" in _SM_SRC.split("def _create_sprint_pr")[1].split("def ")[0]

    def test_child_pr_targets_base_not_develop(self):
        block = _SM_SRC.split("def _create_sprint_pr")[1].split("def ")[0]
        assert '"--base", pr_base' in block
        assert '"--base", "develop"' not in block


class TestRunSprintMergeTarget:
    def test_default_target_is_base_branch(self):
        snippet = _SM_SRC.split("def run_sprint")[1].split("def _run_pipeline_dispatch")[0]
        assert "base_merge_target = _base_sprint_branch(label)" in snippet
        assert "target_branch = base_merge_target" in snippet

    def test_dispatch_uses_sprint_branch_not_merge_target(self):
        assert "sprint_branch=sprint_branch" in _SM_SRC
        assert "sprint_branch=target_branch" not in _SM_SRC


class TestMergeSprintEndpoint:
    def test_finish_renamed_merge_sprint_in_docstring(self):
        block = _SERVER_SRC.split("async def finish_sprint")[1].split("@app.")[0]
        assert "Merge Sprint" in block
        assert "remove=[label]" not in block
        assert "_merge_sprint_branches_for_label" in block

    def test_finish_preview_exposes_merge_branches(self):
        block = _SERVER_SRC.split("def get_sprint_finish_preview")[1].split("@app.")[0]
        assert "merge_branches" in block
        assert "base_label" in block


class TestMergeSprintUi:
    def test_finish_modal_merge_sprint_copy(self):
        assert "Merge Sprint" in _FINISH_MODAL
        assert "Remove sprint label" not in _FINISH_MODAL
        assert "labels kept" in _FINISH_MODAL

    def test_board_button_merge_sprint(self):
        assert "Merge Sprint" in _BOARD_RENDER
        assert "Finish Sprint" not in _BOARD_RENDER

    def test_bundle_merge_sprint(self):
        assert "Merge Sprint" in _BUNDLE
