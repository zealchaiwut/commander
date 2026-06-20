"""Estimator file-prediction accuracy tracking (issue #1417).

Records per-ticket precision/recall after each merge and maintains a rolling
summary so preview-dag can warn when predictions are unreliable.

All I/O is local file operations only — no GitHub API calls, no network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def compute_metrics(predicted: list, actual: list) -> tuple:
    """Return (precision, recall) as floats in [0.0, 1.0].

    Handles empty lists:
      - If actual is empty: recall = 1.0 (nothing was missed)
      - If predicted is empty and actual is non-empty: precision = 0.0, recall = 0.0
    """
    predicted_set = set(predicted)
    actual_set = set(actual)
    tp = len(predicted_set & actual_set)

    if not predicted_set:
        precision = 1.0 if not actual_set else 0.0
    else:
        precision = tp / len(predicted_set)

    if not actual_set:
        recall = 1.0
    else:
        recall = tp / len(actual_set)

    return precision, recall


def record_accuracy(
    issue_number: int,
    predicted_files: list,
    actual_files: list,
    accuracy_dir: Path,
) -> dict:
    """Write per-ticket accuracy JSON and update rolling summary.json.

    Returns the per-ticket artifact dict.
    """
    accuracy_dir.mkdir(parents=True, exist_ok=True)
    precision, recall = compute_metrics(predicted_files, actual_files)

    artifact = {
        "issue_number": issue_number,
        "predicted_files": sorted(predicted_files),
        "actual_files": sorted(actual_files),
        "precision": precision,
        "recall": recall,
    }
    (accuracy_dir / f"issue-{issue_number}.json").write_text(
        json.dumps(artifact, indent=2), encoding="utf-8"
    )
    _update_summary(issue_number, precision, recall, accuracy_dir)
    return artifact


def _update_summary(
    issue_number: int, precision: float, recall: float, accuracy_dir: Path
) -> None:
    summary_path = accuracy_dir / "summary.json"
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            summary = {"tickets": []}
    else:
        summary = {"tickets": []}

    tickets = summary.get("tickets", [])
    tickets = [t for t in tickets if t.get("issue_number") != issue_number]
    tickets.append({"issue_number": issue_number, "precision": precision, "recall": recall})

    count = len(tickets)
    agg_precision = sum(t["precision"] for t in tickets) / count
    agg_recall = sum(t["recall"] for t in tickets) / count

    summary_path.write_text(
        json.dumps(
            {
                "tickets": tickets,
                "aggregate_precision": agg_precision,
                "aggregate_recall": agg_recall,
                "count": count,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def should_warn_unreliable(accuracy_dir: Path) -> bool:
    """Return True if the last 10 merged tickets have average recall < 70%.

    Returns False when fewer than 10 tickets have been recorded (AC5).
    """
    summary_path = accuracy_dir / "summary.json"
    if not summary_path.exists():
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    tickets = summary.get("tickets", [])
    if len(tickets) < 10:
        return False
    last_10 = tickets[-10:]
    avg_recall = sum(t["recall"] for t in last_10) / 10
    return avg_recall < 0.70


def find_commander_dir(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from start (default: CWD) to find a .commander/ directory."""
    current = (start or Path.cwd()).resolve()
    while True:
        candidate = current / ".commander"
        if candidate.is_dir():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None
