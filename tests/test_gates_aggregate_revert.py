"""_revert_to_sit_aggregate — one combined revert for all failing gates.

Run-all gates means a single retry should fix every failure. The aggregate
reverter posts ONE comment listing all failing gates, writes ONE sidecar covering
them (so the coder retry sees them all), and does ONE SIT transition.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT / "services" / "sprint_manager"))
sys.path.insert(0, str(_REPO_ROOT))

import sprint_manager as sm  # noqa: E402
from services.sprint_manager.state import GateResult  # noqa: E402


def test_aggregate_one_comment_one_sidecar_one_transition(monkeypatch):
    calls = {"record": 0, "gaterec": 0, "sit": 0, "comments": []}
    monkeypatch.setattr(sm, "record_failure",
                        lambda *a, **k: calls.__setitem__("record", calls["record"] + 1))
    monkeypatch.setattr(sm, "_write_gate_failure_record",
                        lambda *a, **k: calls.__setitem__("gaterec", calls["gaterec"] + 1))
    monkeypatch.setattr(sm, "_transition_safe",
                        lambda *a, **k: calls.__setitem__("sit", calls["sit"] + 1))

    class _GH:
        @staticmethod
        def add_comment(num, body, repo_name=None):
            calls["comments"].append(body)

    monkeypatch.setattr(sm, "github_client", _GH)
    monkeypatch.setattr(sm, "_FAILURE_PARSING_AVAILABLE", False)

    failed = [
        GateResult(gate="design", passed=False, output="11.5px tiny-text body"),
        GateResult(gate="pytest", passed=False, output="2 tests failed"),
        GateResult(gate="merge-preview", passed=False, output="conflict in main.py"),
    ]
    sm._revert_to_sit_aggregate(1059, failed, total=6, repo_name="o/r")

    assert calls["record"] == 1, "exactly one sidecar for all failures"
    assert calls["sit"] == 1, "exactly one SIT transition"
    assert len(calls["comments"]) == 1, "exactly one combined comment"
    assert calls["gaterec"] == 3, "full gate-failure record kept per gate"
    body = calls["comments"][0]
    assert "3/6" in body
    for g in ("design", "pytest", "merge-preview"):
        assert g in body


def test_aggregate_empty_is_noop(monkeypatch):
    hit = {"n": 0}
    monkeypatch.setattr(sm, "record_failure", lambda *a, **k: hit.__setitem__("n", hit["n"] + 1))
    sm._revert_to_sit_aggregate(1, [], total=4, repo_name="o/r")
    assert hit["n"] == 0
