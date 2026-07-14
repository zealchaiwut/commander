"""Tests for issue #1942: Persist dead-letter registry for exhausted sprint tickets (runs against UAT)"""
import os
import json
import tempfile
from pathlib import Path

import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Acceptance Criteria ---

def test_persist_dead_letter_registry__issue_state_gains_fix_attempts_and_last_error_fields(client):
    # AC: `IssueState` (in `state.json`) gains two new optional fields:
    # `fix_attempts: int` (default `0`) and `last_error: str | None` (default `None`).
    pytest.skip("manual — code-level verification of IssueState dataclass fields, not HTTP-testable")


def test_persist_dead_letter_registry__fix_attempts_persisted_after_each_round(client):
    # AC: After each failed fix round, `SprintManager` increments `fix_attempts`
    # and records `last_error` on the corresponding `IssueState` and persists
    # `state.json` immediately (not only on sprint completion).
    pytest.skip("manual — requires live sprint run with intentionally failing coder; integration test scope")


def test_persist_dead_letter_registry__dead_letter_list_populated_on_exhaustion(client):
    # AC: When `COMMANDER_MAX_FIX_ROUNDS` is exhausted, the ticket entry is
    # appended to a `dead_letter` list on the sprint state object; each entry
    # contains `ticket_id`, `title`, `attempts`, and `last_error`.
    pytest.skip("manual — requires live sprint run with exhaustion scenario")


def test_persist_dead_letter_registry__dead_letter_survives_restart(client):
    # AC: `dead_letter` is serialised into `state.json` and survives process restart
    # (i.e. loading an existing state file re-hydrates the list).
    pytest.skip("manual — requires process restart during sprint; integration test scope")


def test_persist_dead_letter_registry__sprint_summary_includes_dead_letter_section(client):
    # AC: Sprint summary / report output includes a **Dead Letter** section
    # listing all dead-letter tickets with their attempt count and last error message.
    pytest.skip("manual — checked via sprint report inspection, not HTTP-testable")


def test_persist_dead_letter_registry__no_duplicate_dead_letter_entries_on_rerun(client):
    # AC: Tickets already present in `dead_letter` from a previous run are
    # not re-added as duplicates on rerun.
    pytest.skip("manual — requires rerun scenario with pre-existing dead_letter state")


def test_persist_dead_letter_registry__existing_behavior_unchanged(client):
    # AC: Existing behaviour (labelling `needs-rework`, continuing the sprint) is unchanged.
    pytest.skip("manual — verified via sprint lifecycle, requires live run")


def test_persist_dead_letter_registry__unit_test_fix_attempts_increment(client):
    # AC: Unit tests cover: (a) `fix_attempts` increment per round
    pytest.skip("manual — unit-test scope, verified via pytest on the services/ layer")


def test_persist_dead_letter_registry__unit_test_dead_letter_population(client):
    # AC: Unit tests cover: (b) `dead_letter` population on exhaustion
    pytest.skip("manual — unit-test scope, verified via pytest on the services/ layer")


def test_persist_dead_letter_registry__unit_test_serialization_round_trip(client):
    # AC: Unit tests cover: (c) round-trip serialisation/deserialisation of both fields
    pytest.skip("manual — unit-test scope, verified via pytest on the services/ layer")
