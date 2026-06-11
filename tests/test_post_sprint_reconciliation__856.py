"""Tests for issue #856: Add post-sprint reconciliation check for loose ends (runs against UAT)

Tester note on verification surface
-----------------------------------
This feature is *backend logic* (`services/sprint_manager/reconciliation.py`)
that is driven by the **sprint-finish lifecycle** in `sprint_manager.main()`,
plus a **frontend checklist** rendered on the history card (`project.html`).
Its only HTTP surface is the new `reconciliation` field on
`GET /api/sprints/history`, which is added by a router that is *not deployed*
to the running UAT server (UAT currently serves an unrelated feature branch),
and there is **no on-demand HTTP endpoint** that triggers a reconciliation pass
— it only runs as part of finishing a sprint.

Therefore the acceptance criteria cannot be exercised by an httpx call against
the live UAT server. Per the tester skill, each AC that "cannot be HTTP-tested"
is recorded as a manual skip below. The real correctness signal for this ticket
comes from the in-repo deterministic suite
`tests/test_856__post_sprint_reconciliation.py` (16 tests, all green — see the
test report), which covers every AC via the pure reconciliation logic, the
sprint-finish wiring, the history-service enrichment, and the API model.

The one thing that IS verifiable live is that the UAT server is up and serving
the dashboard, which the smoke test asserts.
"""
import os

import httpx
import pytest


# Resolved from UAT .env at runtime; see tester skill Step 0.
BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "")
if not BASE_URL.startswith("http"):
    raise RuntimeError(
        "UAT_BASE_URL / UAT_PORT not set. Run the tester skill's Step 0 to resolve UAT before pytest."
    )


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# --- Live smoke ---------------------------------------------------------------

def test_post_sprint_reconciliation__uat_server_is_reachable(client):
    # The dashboard root must respond so any HTTP-driven verification is possible.
    r = client.get("/")
    assert r.status_code == 200


# --- Acceptance Criteria (no HTTP surface on UAT — see module docstring) -------

def test_post_sprint_reconciliation__reconciliation_runs_automatically_on_finish():
    # AC1: reconciliation runs automatically after a sprint finishes.
    pytest.skip(
        "manual — lifecycle-triggered in sprint_manager.main(); no HTTP trigger. "
        "Verified by test_856 (test_ac1_*) + sprint_manager wiring review."
    )


def test_post_sprint_reconciliation__checks_summary_issue_exists():
    # AC2: a summary issue exists for the exact sprint label.
    pytest.skip(
        "manual — pure backend check (check_summary_issue); cannot be HTTP-tested. "
        "Verified by test_856 (test_ac2_*)."
    )


def test_post_sprint_reconciliation__checks_sprint_pr_merged():
    # AC3: sprint PR merged; if not, reason + conflicting files captured.
    pytest.skip(
        "manual — pure backend check (check_sprint_pr); cannot be HTTP-tested. "
        "Verified by test_856 (test_ac3_*)."
    )


def test_post_sprint_reconciliation__checks_no_stale_status_labels():
    # AC4: no stale sprint status labels remain on tickets.
    pytest.skip(
        "manual — pure backend check (check_stale_labels); cannot be HTTP-tested. "
        "Verified by test_856 (test_ac4_*)."
    )


def test_post_sprint_reconciliation__surfaced_on_history_card_checklist():
    # AC5: results surfaced as a checklist on the history card, red for unresolved.
    pytest.skip(
        "manual — frontend checklist (project.html) + reconciliation field on the "
        "new /api/sprints/history router, which is not deployed to the UAT server. "
        "Verified by test_856 (test_ac5_*) + frontend render review."
    )


def test_post_sprint_reconciliation__emitted_as_activity_log_event():
    # AC6: results emitted as an activity-log event tied to the sprint.
    pytest.skip(
        "manual — event emitted via _emit_sprint_lifecycle_event during finish; "
        "no HTTP trigger. Verified by test_856 (test_ac6_*) + wiring review."
    )


def test_post_sprint_reconciliation__clean_sprint_shows_all_clear():
    # AC7: a cleanly closed sprint shows a green/all-clear checklist.
    pytest.skip(
        "manual — all_clear computed in reconcile_sprint; rendered in frontend. "
        "Verified by test_856 (test_ac7_*)."
    )


def test_post_sprint_reconciliation__failure_explains_what_and_why():
    # AC8: a failing sprint shows exactly what is unresolved and why, no GitHub needed.
    pytest.skip(
        "manual — detail strings carry reason + files (reconcile_sprint); rendered "
        "in frontend. Verified by test_856 (test_ac8_*)."
    )


def test_post_sprint_reconciliation__idempotent_no_duplicate_events():
    # AC9: re-running on an already-clean sprint produces no duplicate events/entries.
    pytest.skip(
        "manual — idempotency via _fingerprint in run_reconciliation; no HTTP trigger. "
        "Verified by test_856 (test_ac9_*)."
    )
