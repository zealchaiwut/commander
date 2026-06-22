"""Tests for issue #1417 — Estimator file-prediction accuracy tracking.

AC1: On merge, compute actual vs predicted files and compare to estimate's files_likely_affected
AC2: Per-ticket artifact written to .commander/estimates/accuracy/issue-<N>.json
     with: issue_number, predicted_files, actual_files, precision, recall
AC3: Rolling summary written/updated at .commander/estimates/accuracy/summary.json
     with aggregate precision and recall across all recorded tickets
AC4: preview-dag returns accuracy_warning=True when last-10-ticket recall < 70%
AC5: preview-dag returns accuracy_warning=False when recall >= 70% or < 10 tickets
AC6: No GitHub API calls at any point in this flow
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from services.sprint_manager.estimate_accuracy import (
    compute_metrics,
    record_accuracy,
    should_warn_unreliable,
)


# ── AC1: compute_metrics gives correct precision and recall ──────────────────

def test_compute_metrics_perfect_prediction():
    precision, recall = compute_metrics(["a.py", "b.py"], ["a.py", "b.py"])
    assert precision == pytest.approx(1.0)
    assert recall == pytest.approx(1.0)


def test_compute_metrics_no_overlap():
    precision, recall = compute_metrics(["a.py"], ["b.py"])
    assert precision == pytest.approx(0.0)
    assert recall == pytest.approx(0.0)


def test_compute_metrics_partial_overlap():
    # predicted: a, b, c — actual: a, b, d
    # TP = {a, b}, precision = 2/3, recall = 2/3
    precision, recall = compute_metrics(["a.py", "b.py", "c.py"], ["a.py", "b.py", "d.py"])
    assert precision == pytest.approx(2 / 3)
    assert recall == pytest.approx(2 / 3)


def test_compute_metrics_empty_predicted_empty_actual():
    # Nothing predicted, nothing changed → recall = 1.0 (nothing missed)
    precision, recall = compute_metrics([], [])
    assert recall == pytest.approx(1.0)


def test_compute_metrics_empty_predicted_nonempty_actual():
    # Predicted nothing but actual files changed → recall = 0.0
    _, recall = compute_metrics([], ["a.py"])
    assert recall == pytest.approx(0.0)


def test_compute_metrics_predicted_subset_of_actual():
    # predicted: a — actual: a, b, c → precision=1.0 (all predicted were right), recall=1/3
    precision, recall = compute_metrics(["a.py"], ["a.py", "b.py", "c.py"])
    assert precision == pytest.approx(1.0)
    assert recall == pytest.approx(1 / 3)


# ── AC2: per-ticket artifact written with required fields ────────────────────

def test_record_accuracy_writes_per_ticket_artifact(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    record_accuracy(42, ["src/a.py", "src/b.py"], ["src/a.py", "src/c.py"], accuracy_dir)

    artifact_path = accuracy_dir / "issue-42.json"
    assert artifact_path.exists(), "per-ticket JSON must be written"
    artifact = json.loads(artifact_path.read_text())

    assert artifact["issue_number"] == 42
    assert set(artifact["predicted_files"]) == {"src/a.py", "src/b.py"}
    assert set(artifact["actual_files"]) == {"src/a.py", "src/c.py"}
    assert "precision" in artifact
    assert "recall" in artifact


def test_record_accuracy_precision_and_recall_correct(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    # predicted: a, b — actual: a → precision=0.5 (1/2), recall=1.0 (1/1)
    record_accuracy(10, ["a.py", "b.py"], ["a.py"], accuracy_dir)

    artifact = json.loads((accuracy_dir / "issue-10.json").read_text())
    assert artifact["precision"] == pytest.approx(0.5)
    assert artifact["recall"] == pytest.approx(1.0)


def test_record_accuracy_creates_accuracy_dir_if_missing(tmp_path):
    accuracy_dir = tmp_path / "deep" / "path" / "accuracy"
    assert not accuracy_dir.exists()
    record_accuracy(1, ["a.py"], ["a.py"], accuracy_dir)
    assert accuracy_dir.exists()


# ── AC3: rolling summary is written/updated ──────────────────────────────────

def test_record_accuracy_creates_summary(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    record_accuracy(1, ["a.py"], ["a.py"], accuracy_dir)

    summary_path = accuracy_dir / "summary.json"
    assert summary_path.exists(), "summary.json must be created"
    summary = json.loads(summary_path.read_text())

    assert "tickets" in summary
    assert "aggregate_precision" in summary
    assert "aggregate_recall" in summary
    assert summary["count"] == 1


def test_record_accuracy_updates_summary_aggregate(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    record_accuracy(1, ["a.py"], ["a.py"], accuracy_dir)   # recall=1.0
    record_accuracy(2, ["b.py"], ["c.py"], accuracy_dir)   # recall=0.0

    summary = json.loads((accuracy_dir / "summary.json").read_text())
    assert summary["count"] == 2
    assert summary["aggregate_recall"] == pytest.approx(0.5)


def test_record_accuracy_deduplicates_same_issue(tmp_path):
    """Re-recording the same issue replaces the old entry, not appends."""
    accuracy_dir = tmp_path / "accuracy"
    record_accuracy(5, ["a.py"], ["z.py"], accuracy_dir)   # recall=0.0
    record_accuracy(5, ["a.py"], ["a.py"], accuracy_dir)   # recall=1.0 (update)

    summary = json.loads((accuracy_dir / "summary.json").read_text())
    assert summary["count"] == 1
    assert summary["aggregate_recall"] == pytest.approx(1.0)


def test_record_accuracy_summary_preserves_ticket_history(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    for n in range(1, 4):
        record_accuracy(n, ["a.py"], ["a.py"], accuracy_dir)

    summary = json.loads((accuracy_dir / "summary.json").read_text())
    issue_nums = {t["issue_number"] for t in summary["tickets"]}
    assert issue_nums == {1, 2, 3}


# ── AC5: suppress warning when < 10 tickets recorded ─────────────────────────

def test_should_warn_false_when_fewer_than_10_tickets(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    # Record 9 tickets with recall=0.0 — should still NOT warn (< 10)
    for i in range(1, 10):
        record_accuracy(i, ["a.py"], ["z.py"], accuracy_dir)

    assert should_warn_unreliable(accuracy_dir) is False


def test_should_warn_false_when_no_summary_file(tmp_path):
    accuracy_dir = tmp_path / "no_data"
    assert should_warn_unreliable(accuracy_dir) is False


def test_should_warn_false_when_exactly_9_tickets_low_recall(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    for i in range(1, 10):  # 9 tickets
        record_accuracy(i, ["a.py"], ["z.py"], accuracy_dir)
    assert should_warn_unreliable(accuracy_dir) is False


# ── AC4: warn when 10+ tickets have avg recall < 70% ─────────────────────────

def test_should_warn_true_when_10_tickets_zero_recall(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    for i in range(1, 11):  # 10 tickets with recall=0.0
        record_accuracy(i, ["a.py"], ["z.py"], accuracy_dir)

    assert should_warn_unreliable(accuracy_dir) is True


def test_should_warn_false_when_10_tickets_perfect_recall(tmp_path):
    accuracy_dir = tmp_path / "accuracy"
    for i in range(1, 11):
        record_accuracy(i, ["a.py"], ["a.py"], accuracy_dir)

    assert should_warn_unreliable(accuracy_dir) is False


def test_should_warn_at_boundary_exactly_70_percent(tmp_path):
    """Recall = 70% exactly → no warning (threshold is strictly < 70%)."""
    accuracy_dir = tmp_path / "accuracy"
    # 7 tickets with recall=1.0 and 3 with recall=0.0 → avg = 0.7
    for i in range(1, 8):
        record_accuracy(i, ["a.py"], ["a.py"], accuracy_dir)
    for i in range(8, 11):
        record_accuracy(i, ["a.py"], ["z.py"], accuracy_dir)

    assert should_warn_unreliable(accuracy_dir) is False


def test_should_warn_just_below_70_percent(tmp_path):
    """Recall just below 70% → warning fires."""
    accuracy_dir = tmp_path / "accuracy"
    # 6 tickets recall=1.0, 4 tickets recall=0.0 → avg = 0.6
    for i in range(1, 7):
        record_accuracy(i, ["a.py"], ["a.py"], accuracy_dir)
    for i in range(7, 11):
        record_accuracy(i, ["a.py"], ["z.py"], accuracy_dir)

    assert should_warn_unreliable(accuracy_dir) is True


def test_should_warn_uses_last_10_only(tmp_path):
    """Old low-recall tickets don't affect warning if the last 10 are fine."""
    accuracy_dir = tmp_path / "accuracy"
    # First 5 with 0 recall
    for i in range(1, 6):
        record_accuracy(i, ["a.py"], ["z.py"], accuracy_dir)
    # Next 10 with perfect recall
    for i in range(6, 16):
        record_accuracy(i, ["a.py"], ["a.py"], accuracy_dir)

    assert should_warn_unreliable(accuracy_dir) is False


# ── AC4/AC5: preview-dag endpoint returns accuracy_warning field ──────────────

def _issue(number: int, title: str, labels: list) -> dict:
    return {
        "number": number,
        "title": title,
        "state": "open",
        "url": f"https://github.com/test/repo/issues/{number}",
        "body": "## Acceptance Criteria\n- [ ] do it",
        "labels": [{"name": l} for l in labels],
    }


def _write_estimate(estimates_dir: Path, issue_num: int, files: list) -> None:
    estimates_dir.mkdir(parents=True, exist_ok=True)
    (estimates_dir / f"issue-{issue_num}.json").write_text(
        json.dumps({"issue_number": issue_num, "size": "S", "confidence": "high",
                    "files_likely_affected": files, "depends_on": [], "blocks": [], "risk_flags": []}),
        encoding="utf-8",
    )


def _seed_accuracy(estimates_dir: Path, recall: float, count: int = 10) -> None:
    """Seed accuracy summary with `count` tickets at the given recall."""
    accuracy_dir = estimates_dir / "accuracy"
    for i in range(1, count + 1):
        # precision=1.0, recall=given
        artifact = {
            "issue_number": 1000 + i,
            "predicted_files": ["f.py"],
            "actual_files": ["f.py"] if recall >= 1.0 else ["z.py"],
            "precision": 1.0 if recall >= 1.0 else 0.0,
            "recall": recall,
        }
        accuracy_dir.mkdir(parents=True, exist_ok=True)
        (accuracy_dir / f"issue-{1000 + i}.json").write_text(json.dumps(artifact))

    tickets_meta = [{"issue_number": 1000 + i, "precision": 1.0 if recall >= 1.0 else 0.0, "recall": recall}
                    for i in range(1, count + 1)]
    summary = {"tickets": tickets_meta, "aggregate_recall": recall,
               "aggregate_precision": 1.0 if recall >= 1.0 else 0.0, "count": count}
    (accuracy_dir / "summary.json").write_text(json.dumps(summary))


def test_preview_dag_returns_accuracy_warning_false_when_no_data(tmp_path):
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    issues = [_issue(1, "A", ["sprint-99"])]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    # No accuracy data → warning suppressed (AC5)

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")

    assert resp.status_code == 200
    data = resp.json()
    assert "accuracy_warning" in data
    assert data["accuracy_warning"] is False


def test_preview_dag_accuracy_warning_true_when_low_recall(tmp_path):
    """AC4: preview-dag shows warning when last 10 tickets have recall < 70%."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    issues = [_issue(1, "A", ["sprint-99"])]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    _seed_accuracy(est, recall=0.0, count=10)   # all-zero recall

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")

    assert resp.status_code == 200
    data = resp.json()
    assert data["accuracy_warning"] is True


def test_preview_dag_accuracy_warning_false_when_high_recall(tmp_path):
    """AC5: warning suppressed when recall >= 70%."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    issues = [_issue(1, "A", ["sprint-99"])]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    _seed_accuracy(est, recall=1.0, count=10)   # perfect recall

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")

    assert resp.status_code == 200
    data = resp.json()
    assert data["accuracy_warning"] is False


def test_preview_dag_accuracy_warning_false_when_fewer_than_10_tickets(tmp_path):
    """AC5: warning suppressed when < 10 tickets recorded, even with low recall."""
    if "server" in sys.modules:
        del sys.modules["server"]
    import server as srv

    issues = [_issue(1, "A", ["sprint-99"])]

    def fake_root(repo: str) -> Path:
        slug = repo.split("/")[-1] if "/" in repo else repo
        return tmp_path / slug

    est = tmp_path / "repo" / ".commander" / "estimates"
    _write_estimate(est, 1, ["src/a.py"])
    _seed_accuracy(est, recall=0.0, count=9)    # 9 tickets, all zero recall

    with (
        patch.object(srv.github_client, "cached_open_issues_with_body", return_value=issues),
        patch("server._project_root_path", side_effect=fake_root),
    ):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        resp = client.get("/api/sprints/sprint-99/preview-dag?project=test/repo")

    assert resp.status_code == 200
    data = resp.json()
    assert data["accuracy_warning"] is False


# ── AC6: no GitHub API calls in the accuracy flow ────────────────────────────

def test_record_accuracy_makes_no_network_calls(tmp_path, monkeypatch):
    """estimate_accuracy module must not import or invoke subprocess/network."""
    import subprocess as _subprocess

    def _boom(*a, **kw):
        raise AssertionError("accuracy flow must not make subprocess/network calls")

    monkeypatch.setattr(_subprocess, "run", _boom)
    monkeypatch.setattr(_subprocess, "check_output", _boom)

    accuracy_dir = tmp_path / "accuracy"
    record_accuracy(99, ["a.py"], ["a.py"], accuracy_dir)
    assert (accuracy_dir / "issue-99.json").exists()
    assert (accuracy_dir / "summary.json").exists()
