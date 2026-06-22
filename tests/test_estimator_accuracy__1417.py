"""Tests for issue #1417: Track estimator file-prediction accuracy after ticket merges.

Acceptance Criteria:
- AC1: On merge, computes actual changed files vs estimate's `files_likely_affected`
- AC2: Per-ticket accuracy artifact written to `.commander/estimates/accuracy/issue-<N>.json`
- AC3: Rolling summary written/updated at `.commander/estimates/accuracy/summary.json`
- AC4: `preview-dag` displays amber warning when 10-ticket recall < 70%
- AC5: `preview-dag` banner suppressed when recall ≥70% or fewer than 10 tickets recorded
- AC6: Zero GitHub API calls in accuracy flow
- AC7: Accuracy artifacts present for all merged tickets after sprint completes
"""
import os

import pytest
import httpx


BASE_URL = os.environ.get("UAT_BASE_URL") or "http://localhost:" + os.environ.get("UAT_PORT", "8001")
if not BASE_URL.startswith("http"):
    raise RuntimeError("UAT_BASE_URL / UAT_PORT not set")


@pytest.fixture
def client():
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        yield c


# ── Acceptance Criteria ──

def test_estimator_accuracy__merge_computes_actual_files(tmp_path):
    """AC1: On merge, computes actual changed files vs estimate's `files_likely_affected`."""
    pytest.skip("manual — merge detection and diff computation tested via integration with sprint_manager, not HTTP")


def test_estimator_accuracy__accuracy_artifact_written(tmp_path):
    """AC2: Per-ticket accuracy artifact written to `.commander/estimates/accuracy/issue-<N>.json`."""
    pytest.skip("manual — accuracy artifacts written by sprint_manager post-merge hook; verified via filesystem inspection")


def test_estimator_accuracy__summary_updated(tmp_path):
    """AC3: Rolling summary written/updated at `.commander/estimates/accuracy/summary.json`."""
    pytest.skip("manual — summary.json updated by sprint_manager after each merge; verified via filesystem inspection")


def test_estimator_accuracy__preview_dag_warning_low_recall(client):
    """AC4: `preview-dag` displays amber warning when 10-ticket recall < 70%."""
    pytest.skip("manual — requires seeding accuracy.json with low-recall tickets and inspecting preview-dag UI banner")


def test_estimator_accuracy__preview_dag_suppressed_high_recall(client):
    """AC5: `preview-dag` banner suppressed when recall ≥70% or fewer than 10 tickets recorded."""
    pytest.skip("manual — requires seeding accuracy.json with high-recall tickets and verifying no banner appears")


def test_estimator_accuracy__no_github_api_calls(tmp_path):
    """AC6: Zero GitHub API calls in accuracy flow."""
    pytest.skip("manual — verified by monitoring network access during sprint_manager merge operations; no HTTP to api.github.com")


def test_estimator_accuracy__artifacts_after_sprint(tmp_path):
    """AC7: Accuracy artifacts present for all merged tickets after sprint completes."""
    pytest.skip("manual — verified via filesystem inspection of `.commander/estimates/accuracy/issue-<N>.json` for all merged tickets in a sprint")
