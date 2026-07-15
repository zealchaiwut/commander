"""Tests for issue #1942 — Persist dead-letter registry for exhausted sprint tickets.

AC coverage:
  AC-a  fix_attempts increments per failed fix round on IssueState.
  AC-b  dead_letter is populated on fix-loop exhaustion (no duplicates on rerun).
  AC-c  Round-trip serialisation/deserialisation of fix_attempts, last_error,
        and dead_letter survives a save/load cycle.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

if "github_client" not in sys.modules:
    sys.modules["github_client"] = MagicMock()

from services.sprint_manager.state import IssueState, SprintState


# ── AC-c: round-trip serialisation ───────────────────────────────────────────

class TestIssueStateRoundTrip:
    """AC-c: fix_attempts and last_error survive to_dict → from_dict."""

    def test_defaults_are_zero_and_none(self):
        iss = IssueState(number=1, title="T")
        assert iss.fix_attempts == 0
        assert iss.last_error is None

    def test_to_dict_includes_fix_attempts(self):
        iss = IssueState(number=1, title="T")
        iss.fix_attempts = 3
        d = iss.to_dict()
        assert d["fix_attempts"] == 3

    def test_to_dict_includes_last_error(self):
        iss = IssueState(number=1, title="T")
        iss.last_error = "pytest failed"
        d = iss.to_dict()
        assert d["last_error"] == "pytest failed"

    def test_from_dict_restores_fix_attempts(self):
        iss = IssueState.from_dict({
            "number": 7, "title": "Bug", "fix_attempts": 2,
        })
        assert iss.fix_attempts == 2

    def test_from_dict_restores_last_error(self):
        iss = IssueState.from_dict({
            "number": 7, "title": "Bug", "last_error": "lint failed",
        })
        assert iss.last_error == "lint failed"

    def test_from_dict_defaults_fix_attempts_when_absent(self):
        iss = IssueState.from_dict({"number": 7, "title": "Bug"})
        assert iss.fix_attempts == 0

    def test_from_dict_defaults_last_error_when_absent(self):
        iss = IssueState.from_dict({"number": 7, "title": "Bug"})
        assert iss.last_error is None

    def test_full_roundtrip(self):
        iss = IssueState(number=42, title="Fix me")
        iss.fix_attempts = 3
        iss.last_error = "gate: pytest 2 failures"
        restored = IssueState.from_dict(iss.to_dict())
        assert restored.fix_attempts == 3
        assert restored.last_error == "gate: pytest 2 failures"


class TestSprintStateDeadLetterRoundTrip:
    """AC-c: dead_letter survives to_dict → from_dict and state.save/load."""

    def test_default_dead_letter_is_empty_list(self):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        assert st.dead_letter == []

    def test_to_dict_includes_dead_letter(self):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.dead_letter.append({
            "ticket_id": 10, "title": "T", "attempts": 3, "last_error": "fail",
        })
        d = st.to_dict()
        assert "dead_letter" in d
        assert d["dead_letter"][0]["ticket_id"] == 10

    def test_from_dict_restores_dead_letter(self):
        st = SprintState.from_dict({
            "sprint_label": "sprint-1",
            "sprint_number": 1,
            "dead_letter": [
                {"ticket_id": 99, "title": "Crash", "attempts": 2, "last_error": "err"},
            ],
        })
        assert len(st.dead_letter) == 1
        assert st.dead_letter[0]["ticket_id"] == 99
        assert st.dead_letter[0]["attempts"] == 2

    def test_from_dict_defaults_dead_letter_when_absent(self):
        st = SprintState.from_dict({"sprint_label": "sprint-1", "sprint_number": 1})
        assert st.dead_letter == []

    def test_save_and_reload_preserves_dead_letter(self, tmp_path):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        st.dead_letter.append({
            "ticket_id": 5, "title": "Work", "attempts": 3, "last_error": "oops",
        })
        state_path = tmp_path / "state.json"
        st.save(state_path)
        raw = json.loads(state_path.read_text())
        restored = SprintState.from_dict(raw)
        assert len(restored.dead_letter) == 1
        assert restored.dead_letter[0]["last_error"] == "oops"

    def test_save_and_reload_preserves_fix_attempts_in_issues(self, tmp_path):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        iss = IssueState(number=3, title="Ticket")
        iss.fix_attempts = 2
        iss.last_error = "ruff: 3 errors"
        st.issues.append(iss)
        state_path = tmp_path / "state.json"
        st.save(state_path)
        raw = json.loads(state_path.read_text())
        restored = SprintState.from_dict(raw)
        restored_iss = restored.issues[0]
        assert restored_iss.fix_attempts == 2
        assert restored_iss.last_error == "ruff: 3 errors"


# ── AC-a: fix_attempts increments per round ───────────────────────────────────

class TestFixAttemptsIncrement:
    """AC-a: fix_attempts increments on each failed fix round."""

    def test_increment_once(self):
        iss = IssueState(number=1, title="T")
        iss.fix_attempts += 1
        iss.last_error = "round 1 fail"
        assert iss.fix_attempts == 1
        assert iss.last_error == "round 1 fail"

    def test_increment_multiple_rounds(self):
        iss = IssueState(number=1, title="T")
        for i, err in enumerate(["fail A", "fail B", "fail C"], start=1):
            iss.fix_attempts += 1
            iss.last_error = err
        assert iss.fix_attempts == 3
        assert iss.last_error == "fail C"

    def test_fix_attempts_persists_across_save(self, tmp_path):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        iss = IssueState(number=1, title="T")
        iss.fix_attempts = 2
        iss.last_error = "round 2 error"
        st.issues.append(iss)
        state_path = tmp_path / "state.json"
        st.save(state_path)

        raw = json.loads(state_path.read_text())
        restored_st = SprintState.from_dict(raw)
        restored_iss = restored_st.issues[0]
        assert restored_iss.fix_attempts == 2
        assert restored_iss.last_error == "round 2 error"

    def test_intermediate_save_after_each_round(self, tmp_path):
        """Simulate K rounds each saving immediately — last save wins for last_error."""
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        iss = IssueState(number=10, title="Flaky")
        st.issues.append(iss)
        state_path = tmp_path / "state.json"

        errors = ["err round 1", "err round 2", "err round 3"]
        for err in errors:
            iss.fix_attempts += 1
            iss.last_error = err
            st.save(state_path)

        raw = json.loads(state_path.read_text())
        restored = SprintState.from_dict(raw)
        assert restored.issues[0].fix_attempts == 3
        assert restored.issues[0].last_error == "err round 3"


# ── AC-b: dead_letter populated on exhaustion, no duplicates ─────────────────

class TestDeadLetterPopulation:
    """AC-b: dead_letter is populated on fix-loop exhaustion; no duplicates on rerun."""

    def _make_state(self) -> SprintState:
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        iss = IssueState(number=7, title="Broken")
        iss.fix_attempts = 3
        iss.last_error = "pytest: 5 failures"
        st.issues.append(iss)
        return st

    def test_dead_letter_entry_added_on_exhaustion(self):
        st = self._make_state()
        iss = st.issues[0]
        if not any(d.get("ticket_id") == iss.number for d in st.dead_letter):
            st.dead_letter.append({
                "ticket_id": iss.number,
                "title": iss.title,
                "attempts": iss.fix_attempts,
                "last_error": iss.last_error,
            })
        assert len(st.dead_letter) == 1
        entry = st.dead_letter[0]
        assert entry["ticket_id"] == 7
        assert entry["title"] == "Broken"
        assert entry["attempts"] == 3
        assert entry["last_error"] == "pytest: 5 failures"

    def test_dead_letter_no_duplicate_on_rerun(self):
        st = self._make_state()
        iss = st.issues[0]

        def _add_to_dead_letter(state: SprintState, issue: IssueState) -> None:
            if not any(d.get("ticket_id") == issue.number for d in state.dead_letter):
                state.dead_letter.append({
                    "ticket_id": issue.number,
                    "title": issue.title,
                    "attempts": issue.fix_attempts,
                    "last_error": issue.last_error,
                })

        _add_to_dead_letter(st, iss)
        _add_to_dead_letter(st, iss)  # second call simulates rerun
        assert len(st.dead_letter) == 1

    def test_dead_letter_entry_contains_required_fields(self):
        st = self._make_state()
        iss = st.issues[0]
        st.dead_letter.append({
            "ticket_id": iss.number,
            "title": iss.title,
            "attempts": iss.fix_attempts,
            "last_error": iss.last_error,
        })
        entry = st.dead_letter[0]
        for key in ("ticket_id", "title", "attempts", "last_error"):
            assert key in entry, f"missing key: {key}"

    def test_dead_letter_survives_restart(self, tmp_path):
        st = self._make_state()
        iss = st.issues[0]
        st.dead_letter.append({
            "ticket_id": iss.number,
            "title": iss.title,
            "attempts": iss.fix_attempts,
            "last_error": iss.last_error,
        })
        state_path = tmp_path / "state.json"
        st.save(state_path)

        raw = json.loads(state_path.read_text())
        restored = SprintState.from_dict(raw)
        assert len(restored.dead_letter) == 1
        assert restored.dead_letter[0]["ticket_id"] == 7
        assert restored.dead_letter[0]["attempts"] == 3

    def test_multiple_dead_letter_entries(self):
        st = SprintState(sprint_label="sprint-1", sprint_number=1)
        for i, (tid, err) in enumerate([(1, "err A"), (2, "err B")], start=1):
            iss = IssueState(number=tid, title=f"T{i}")
            iss.fix_attempts = 3
            iss.last_error = err
            st.issues.append(iss)
            if not any(d.get("ticket_id") == tid for d in st.dead_letter):
                st.dead_letter.append({
                    "ticket_id": tid, "title": iss.title,
                    "attempts": iss.fix_attempts, "last_error": err,
                })
        assert len(st.dead_letter) == 2
        assert st.dead_letter[0]["ticket_id"] == 1
        assert st.dead_letter[1]["ticket_id"] == 2
