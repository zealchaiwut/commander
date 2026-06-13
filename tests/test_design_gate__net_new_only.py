"""Regression tests for the diff-aware design gate.

Context: issue #879's UI is finding-neutral (introduces zero anti-patterns), yet
the design gate bounced it because the legacy gate ran ``impeccable detect`` over
the *entire* static/ directory and failed on pre-existing baseline warnings in
unrelated files (analytics.html, etc.). These tests anchor the fix: under the
default ``gate_scope='changed'`` the gate must fail ONLY on net-new anti-patterns
introduced by the diff, mirroring the diff-aware monolith/typecheck/lint gates.

Each test maps to a specific behaviour:
  T1  signature ignores file path (base vs HEAD temp copies compare cleanly)
  T2  HEAD findings that are a subset of base findings → no net-new
  T3  a *second* identical anti-pattern still surfaces as net-new (multiset diff)
  T4  a brand-new anti-pattern signature surfaces as net-new
  T5  empty base (newly added file) → every HEAD finding is net-new
  T6  gate PASSES on a baseline-dirty changed file when the diff adds nothing
  T7  gate FAILS (and reverts to SIT) when the diff adds a net-new anti-pattern
  T8  gate PASSES with no detect calls when no frontend files changed
  T9  full-scope (legacy) still fails on any finding across the directory
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


_LC1 = {"antipattern": "low-contrast", "line": 0,
        "snippet": "4.4:1 (need 4.5:1) — text #6b7280 on #f3f4f6"}
_LC2 = {"antipattern": "low-contrast", "line": 0,
        "snippet": "2.5:1 (need 4.5:1) — text #9ca3af on #ffffff"}
_TINY = {"antipattern": "tiny-text", "line": 0, "snippet": "11px body text"}


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_finding_sig_ignores_file_path():  # T1
    a = dict(_LC1, file="/a/analytics.html")
    b = dict(_LC1, file="/tmp/whatever.html")
    assert sm._finding_sig(a) == sm._finding_sig(b)


def test_net_new_empty_when_head_subset_of_base():  # T2
    base = [_LC1, _LC2, _TINY]
    head = [_LC1, _LC2]
    assert sm._net_new_findings(base, head) == []


def test_net_new_detects_added_duplicate():  # T3
    base = [_LC1]
    head = [_LC1, _LC1]  # a second identical low-contrast instance
    net = sm._net_new_findings(base, head)
    assert len(net) == 1
    assert sm._finding_sig(net[0]) == sm._finding_sig(_LC1)


def test_net_new_detects_new_antipattern():  # T4
    base = [_LC1]
    head = [_LC1, _TINY]
    net = sm._net_new_findings(base, head)
    assert [sm._finding_sig(f) for f in net] == [sm._finding_sig(_TINY)]


def test_net_new_all_when_base_empty():  # T5
    base = []
    head = [_LC1, _TINY]
    assert len(sm._net_new_findings(base, head)) == 2


# ── _gate_design behaviour under gate_scope='changed' ──────────────────────────

def _stub_common(monkeypatch, tmp_path):
    """Make a frontend file present and stub out side-effecting deps."""
    (tmp_path / "x.html").write_text("<html></html>", encoding="utf-8")
    monkeypatch.setattr(sm, "_try", lambda *a, **k: (True, "npx", ""))
    monkeypatch.setattr(sm, "_post_agent_event", lambda *a, **k: None)
    reverts = []
    monkeypatch.setattr(sm, "_revert_to_sit",
                        lambda *a, **k: reverts.append((a, k)))
    # git show (base version fetch) always succeeds with dummy source
    monkeypatch.setattr(sm, "_run_timed",
                        lambda *a, **k: (0, "<html>base</html>", ""))
    return reverts


def test_gate_passes_on_baseline_dirty_neutral_diff(monkeypatch, tmp_path):  # T6
    reverts = _stub_common(monkeypatch, tmp_path)
    monkeypatch.setattr(sm, "_changed_frontend_files",
                        lambda base, cwd: ["apps/dashboard/static/project.html"])
    # HEAD and base both report the same baseline findings → net-new is empty.
    monkeypatch.setattr(sm, "_impeccable_findings",
                        lambda npx, target, cwd: [dict(_LC1), dict(_LC2)])
    r = sm._gate_design(879, tmp_path, skip=False,
                        base_branch="base", gate_scope="changed")
    assert r.passed is True
    assert reverts == []


def test_gate_fails_on_net_new_antipattern(monkeypatch, tmp_path):  # T7
    reverts = _stub_common(monkeypatch, tmp_path)
    monkeypatch.setattr(sm, "_changed_frontend_files",
                        lambda base, cwd: ["apps/dashboard/static/project.html"])
    # First call = HEAD (two findings); second call = base (one finding).
    seq = [[dict(_LC1), dict(_TINY)], [dict(_LC1)]]
    monkeypatch.setattr(sm, "_impeccable_findings",
                        lambda *a, **k: seq.pop(0))
    r = sm._gate_design(879, tmp_path, skip=False,
                        base_branch="base", gate_scope="changed")
    assert r.passed is False
    assert len(reverts) == 1
    assert "tiny-text" in r.output


def test_gate_passes_with_no_changed_frontend_files(monkeypatch, tmp_path):  # T8
    reverts = _stub_common(monkeypatch, tmp_path)
    monkeypatch.setattr(sm, "_changed_frontend_files", lambda base, cwd: [])
    called = {"n": 0}

    def _should_not_run(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(sm, "_impeccable_findings", _should_not_run)
    r = sm._gate_design(879, tmp_path, skip=False,
                        base_branch="base", gate_scope="changed")
    assert r.passed is True
    assert reverts == []
    assert called["n"] == 0  # no detect invoked when nothing changed


def test_full_scope_still_fails_on_any_finding(monkeypatch, tmp_path):  # T9
    reverts = _stub_common(monkeypatch, tmp_path)
    # In full scope the gate shells out to detect over the whole dir and keys
    # off the exit code; non-zero (issues found) must fail.
    monkeypatch.setattr(sm, "_run_timed",
                        lambda *a, **k: (2, "[{\"antipattern\":\"low-contrast\"}]", ""))
    r = sm._gate_design(879, tmp_path, skip=False,
                        base_branch="base", gate_scope="full")
    assert r.passed is False
    assert len(reverts) == 1
