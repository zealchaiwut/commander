"""Behavioral tests for issue #2174 — log swallowed record_sprint_ready_to_merge failure.

AC1: When db.record_sprint_ready_to_merge raises inside
     _sprint_db_mark_merged_completed, the exception is logged at WARNING level
     (not silently swallowed with a bare pass).

AC2: Despite the logged error, _sprint_db_mark_merged_completed continues
     executing — the function remains best-effort and does not re-raise.
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


def _load_startup():
    """Return the module that contains _sprint_db_mark_merged_completed."""
    for mod_name in ("startup", "server"):
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "_sprint_db_mark_merged_completed"):
                return mod
        except Exception:
            continue
    raise ImportError("_sprint_db_mark_merged_completed not found in startup or server")


def _fake_transition_result(accepted=True):
    """Return a minimal TransitionResult-like object."""
    r = MagicMock()
    r.accepted = accepted
    return r


class TestAC1LogsWarningOnFailure:
    """record_sprint_ready_to_merge failure must be logged at WARNING.

    We isolate the RTM call by:
    - Patching db.get_sprint to return a 'draft'-state row (triggers the RTM branch)
    - Patching db.record_sprint_ready_to_merge to raise
    - Patching _sprint_db_set_state to succeed silently (so no illegal-edge noise)
    Then we check only log records emitted from startup/server (not from db).
    """

    def _run(self, mod, exc, caplog):
        """Invoke _sprint_db_mark_merged_completed with RTM raising exc."""
        draft_row = {"state": "draft", "label": "sprint-42", "project": "owner/repo"}

        with patch.object(mod.db, "get_sprint", return_value=draft_row), \
             patch.object(mod.db, "record_sprint_ready_to_merge",
                          side_effect=exc), \
             patch.object(mod, "_sprint_db_set_state", return_value=True), \
             caplog.at_level(logging.WARNING):
            mod._sprint_db_mark_merged_completed(
                "sprint-42", project="owner/repo"
            )

    def test_warning_is_emitted_on_db_error(self, caplog):
        """A WARNING must appear when record_sprint_ready_to_merge raises."""
        mod = _load_startup()
        db_error = Exception("sqlite3 disk I/O error")
        self._run(mod, db_error, caplog)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, (
            "Expected at least one WARNING when record_sprint_ready_to_merge fails; "
            f"got no records. caplog.records={caplog.records!r}"
        )

    def test_warning_references_the_failure(self, caplog):
        """The WARNING message must contain enough context to diagnose the error."""
        mod = _load_startup()
        sentinel = RuntimeError("intentional sentinel error")
        self._run(mod, sentinel, caplog)

        warning_texts = [r.getMessage() for r in caplog.records
                         if r.levelno >= logging.WARNING]
        combined = " ".join(warning_texts)
        # The log must mention either the function name, sprint label, or error text
        assert (
            "record_sprint_ready_to_merge" in combined
            or "sprint-42" in combined
            or "intentional sentinel" in combined
        ), (
            f"WARNING must reference the failure context; got: {warning_texts!r}"
        )


class TestAC2ContinuesAfterFailure:
    """_sprint_db_mark_merged_completed must not re-raise the exception."""

    def test_returns_without_raising(self):
        """Function must return (not raise) even when the RTM call fails."""
        mod = _load_startup()

        draft_row = {"state": "draft", "label": "sprint-55", "project": "owner/repo"}

        with patch.object(mod.db, "get_sprint", return_value=draft_row), \
             patch.object(mod.db, "record_sprint_ready_to_merge",
                          side_effect=Exception("DB down")), \
             patch.object(mod, "_sprint_db_set_state", return_value=True):
            try:
                result = mod._sprint_db_mark_merged_completed(
                    "sprint-55", project="owner/repo"
                )
            except Exception as exc:
                raise AssertionError(
                    f"_sprint_db_mark_merged_completed must not re-raise; got {exc!r}"
                ) from exc

            assert result is True, (
                f"Expected True (set_state succeeded); got {result!r}"
            )

    def test_proceeds_to_set_state_after_rtm_failure(self):
        """After RTM raises, the function still attempts to mark sprint completed."""
        mod = _load_startup()

        set_state_calls: list = []
        draft_row = {"state": "draft", "label": "sprint-77", "project": "owner/repo"}

        def fake_set_state(label, project, state, **kw):
            set_state_calls.append((label, state))
            return True

        with patch.object(mod.db, "get_sprint", return_value=draft_row), \
             patch.object(mod.db, "record_sprint_ready_to_merge",
                          side_effect=Exception("DB down")), \
             patch.object(mod, "_sprint_db_set_state", side_effect=fake_set_state):
            mod._sprint_db_mark_merged_completed(
                "sprint-77", project="owner/repo"
            )

        assert set_state_calls, (
            "_sprint_db_set_state must still be called after record_sprint_ready_to_merge fails"
        )
        assert any(state == "completed" for _, state in set_state_calls), (
            f"Expected a 'completed' transition attempt; got: {set_state_calls!r}"
        )
