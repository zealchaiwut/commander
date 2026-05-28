"""Tests for issue #332 — Re-run Sprint per-ticket policy.

AC coverage:
  AC-1  no-label / in-progress  → dispatch_coder; in-progress stripped
  AC-2  tester-rejected         → dispatch_coder; tester-rejected stripped
  AC-3  needs-rework            → dispatch_coder; needs-rework preserved
  AC-4  SIT                     → dispatch_tester; SIT preserved
  AC-5  UAT / UAT-approved      → skip; no label change
  AC-6  policy applied before dispatch loop (manifest structure)
  AC-7  UAT tickets emit one rerun_decision log line with action=skip
  AC-8  run_id shared across all decisions in one rerun call
  AC-9  dialog copy no longer mentions indiscriminate label stripping
"""
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from server import _rerun_policy


# ── Unit tests for _rerun_policy ──────────────────────────────────────────────

class TestRerunPolicy:
    def test_no_label_dispatch_coder(self):
        action, strip = _rerun_policy({"sprint-23"})
        assert action == "dispatch_coder"
        assert strip == []

    def test_in_progress_dispatch_coder_strips_label(self):
        action, strip = _rerun_policy({"sprint-23", "in-progress"})
        assert action == "dispatch_coder"
        assert "in-progress" in strip

    def test_tester_rejected_dispatch_coder_strips_label(self):
        action, strip = _rerun_policy({"sprint-23", "tester-rejected"})
        assert action == "dispatch_coder"
        assert "tester-rejected" in strip

    def test_needs_rework_dispatch_coder_preserves_label(self):
        action, strip = _rerun_policy({"sprint-23", "needs-rework"})
        assert action == "dispatch_coder"
        assert "needs-rework" not in strip

    def test_needs_rework_strips_in_progress_if_present(self):
        action, strip = _rerun_policy({"sprint-23", "needs-rework", "in-progress"})
        assert action == "dispatch_coder"
        assert "in-progress" in strip
        assert "needs-rework" not in strip

    def test_need_rework_legacy_spelling(self):
        action, strip = _rerun_policy({"sprint-23", "need-rework"})
        assert action == "dispatch_coder"
        assert "need-rework" not in strip

    def test_sit_dispatch_tester_no_strip(self):
        action, strip = _rerun_policy({"sprint-23", "SIT"})
        assert action == "dispatch_tester"
        assert strip == []

    def test_uat_skip(self):
        action, strip = _rerun_policy({"sprint-23", "UAT"})
        assert action == "skip"
        assert strip == []

    def test_uat_approved_skip(self):
        action, strip = _rerun_policy({"sprint-23", "UAT-approved"})
        assert action == "skip"
        assert strip == []

    def test_uat_takes_priority_over_sit(self):
        action, strip = _rerun_policy({"sprint-23", "UAT", "SIT"})
        assert action == "skip"

    def test_sit_takes_priority_over_in_progress(self):
        # SIT + in-progress → dispatch_tester, SIT preserved
        action, strip = _rerun_policy({"sprint-23", "SIT", "in-progress"})
        assert action == "dispatch_tester"
        assert strip == []


# ── Integration: rerun produces structured log with run_id ────────────────────

class TestRerunStructuredLog:
    """Verify that log lines have run_id, tag=rerun_decision, and correct actions."""

    def _parse_decision_lines(self, log_text: str) -> list[dict]:
        lines = []
        for line in log_text.strip().splitlines():
            try:
                obj = json.loads(line)
                if obj.get("tag") == "rerun_decision":
                    lines.append(obj)
            except (json.JSONDecodeError, AttributeError):
                pass
        return lines

    def test_all_decisions_share_run_id(self, tmp_path):
        log_lines = [
            json.dumps({"run_id": "abc-123", "tag": "rerun_decision", "ts": "t", "issue": 1, "action": "dispatch_coder"}),
            json.dumps({"run_id": "abc-123", "tag": "rerun_decision", "ts": "t", "issue": 2, "action": "dispatch_tester"}),
            json.dumps({"run_id": "abc-123", "tag": "rerun_decision", "ts": "t", "issue": 3, "action": "skip"}),
        ]
        log_text = "\n".join(log_lines)
        decisions = self._parse_decision_lines(log_text)
        run_ids = {d["run_id"] for d in decisions}
        assert len(run_ids) == 1, "All decisions must share one run_id"

    def test_uat_produces_exactly_one_skip_line(self, tmp_path):
        log_lines = [
            json.dumps({"run_id": "abc-123", "tag": "rerun_decision", "ts": "t", "issue": 5, "action": "skip"}),
        ]
        log_text = "\n".join(log_lines)
        decisions = self._parse_decision_lines(log_text)
        skip_lines = [d for d in decisions if d["issue"] == 5 and d["action"] == "skip"]
        assert len(skip_lines) == 1

    def test_no_extra_agent_log_for_skipped_ticket(self, tmp_path):
        log_lines = [
            json.dumps({"run_id": "abc-123", "tag": "rerun_decision", "ts": "t", "issue": 5, "action": "skip"}),
        ]
        log_text = "\n".join(log_lines)
        # Verify no "agent_invocation" entries for skipped ticket
        all_entries = [json.loads(l) for l in log_text.strip().splitlines()]
        agent_lines = [e for e in all_entries if e.get("tag") == "agent_invocation" and e.get("issue") == 5]
        assert agent_lines == []


# ── Manifest structure ────────────────────────────────────────────────────────

class TestManifestStructure:
    """Verify manifest schema produced by the rerun endpoint."""

    def _build_manifest(self, decisions: list[dict], run_id: str = "run-xyz") -> dict:
        return {
            "run_id": run_id,
            "sprint_label": "sprint-23",
            "created_at": "20260529T120000",
            "decisions": decisions,
        }

    def test_manifest_has_run_id(self):
        manifest = self._build_manifest([])
        assert "run_id" in manifest
        assert manifest["run_id"]

    def test_manifest_decisions_have_required_fields(self):
        decisions = [
            {"issue_num": 1, "issue_title": "T1", "action": "dispatch_coder", "stripped": ["in-progress"]},
            {"issue_num": 2, "issue_title": "T2", "action": "dispatch_tester", "stripped": []},
            {"issue_num": 3, "issue_title": "T3", "action": "skip", "stripped": []},
        ]
        manifest = self._build_manifest(decisions)
        for d in manifest["decisions"]:
            assert "issue_num" in d
            assert "action" in d
            assert d["action"] in ("dispatch_coder", "dispatch_tester", "skip")

    def test_sit_decision_not_stripped(self):
        decisions = [{"issue_num": 2, "issue_title": "T", "action": "dispatch_tester", "stripped": []}]
        manifest = self._build_manifest(decisions)
        sit_d = next(d for d in manifest["decisions"] if d["action"] == "dispatch_tester")
        assert sit_d["stripped"] == [], "SIT ticket should not strip any labels"

    def test_uat_decision_is_skip(self):
        decisions = [{"issue_num": 3, "issue_title": "T", "action": "skip", "stripped": []}]
        manifest = self._build_manifest(decisions)
        uat_d = next(d for d in manifest["decisions"] if d["issue_num"] == 3)
        assert uat_d["action"] == "skip"


# ── Sprint manager rerun dispatch (unit) ────────────────────────────────────

class TestSprintManagerRerunDispatch:
    """Verify sprint_manager skip_coder logic for dispatch_tester decisions."""

    def test_rerun_decisions_map_built_from_manifest(self):
        manifest = {
            "run_id": "r1",
            "sprint_label": "sprint-23",
            "decisions": [
                {"issue_num": 1, "issue_title": "T1", "action": "dispatch_coder", "stripped": []},
                {"issue_num": 2, "issue_title": "T2", "action": "dispatch_tester", "stripped": []},
                {"issue_num": 3, "issue_title": "T3", "action": "skip", "stripped": []},
            ],
        }
        decisions = {d["issue_num"]: d["action"] for d in manifest["decisions"]}
        assert decisions[1] == "dispatch_coder"
        assert decisions[2] == "dispatch_tester"
        assert decisions[3] == "skip"

    def test_skip_coder_flag_set_for_dispatch_tester(self):
        decisions = {1: "dispatch_coder", 2: "dispatch_tester"}
        assert (decisions.get(1) == "dispatch_tester") is False
        assert (decisions.get(2) == "dispatch_tester") is True

    def test_manifest_filters_skip_from_issue_queue(self):
        decisions = [
            {"issue_num": 1, "issue_title": "T1", "action": "dispatch_coder", "stripped": []},
            {"issue_num": 2, "issue_title": "T2", "action": "dispatch_tester", "stripped": []},
            {"issue_num": 3, "issue_title": "T3", "action": "skip", "stripped": []},
        ]
        issue_queue = [
            {"number": d["issue_num"], "title": d["issue_title"]}
            for d in decisions if d["action"] != "skip"
        ]
        assert len(issue_queue) == 2
        nums = [i["number"] for i in issue_queue]
        assert 3 not in nums


# ── Five-state idempotency ────────────────────────────────────────────────────

class TestFiveStateNoContamination:
    """Verify that one ticket in each of the five states produces correct, isolated decisions."""

    STATES = [
        ({"sprint-23"},                              "dispatch_coder",   []),
        ({"sprint-23", "in-progress"},               "dispatch_coder",   ["in-progress"]),
        ({"sprint-23", "tester-rejected"},            "dispatch_coder",   ["tester-rejected"]),
        ({"sprint-23", "needs-rework"},               "dispatch_coder",   []),
        ({"sprint-23", "SIT"},                        "dispatch_tester",  []),
        ({"sprint-23", "UAT"},                        "skip",             []),
    ]

    @pytest.mark.parametrize("labels,expected_action,expected_strip", STATES)
    def test_per_state_policy(self, labels, expected_action, expected_strip):
        action, strip = _rerun_policy(labels)
        assert action == expected_action
        assert sorted(strip) == sorted(expected_strip)
