"""AC tests for wiring the baseline-delta check into the merge path (issue #2316).

The merge is refused *before* any merge commit exists, so these tests assert on
the gate function's control flow rather than on git state.

No live HTTP: github_client.add_comment is stubbed throughout.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


def _load_finish_feature():
    """Import scripts/finish_feature.py by path — `scripts` is not a package."""
    spec = importlib.util.spec_from_file_location(
        "finish_feature_under_test", REPO_ROOT / "scripts" / "finish_feature.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ff = _load_finish_feature()

PASSING_OUTPUT = "20 passed, 1 skipped in 0.09s\n"
ONE_NEW_FAILURE = (
    "FAILED tests/test_new.py::test_bad - AssertionError\n"
    "1 failed, 19 passed in 0.12s\n"
)


@pytest.fixture
def comments(monkeypatch):
    """Capture issue comments instead of calling GitHub."""
    captured = []
    monkeypatch.setattr(ff, "_post_comment", lambda n, b, r: captured.append((n, b)))
    return captured


def _patch_suite(monkeypatch, output: str, timed_out: bool = False):
    monkeypatch.setattr(ff, "_run_suite_on_branch", lambda b, t: (output, timed_out))


def _patch_changed(monkeypatch, files):
    monkeypatch.setattr(ff, "_changed_files_vs_target", lambda b, t: files)


# --- AC: docs-only diffs skip the check without running the suite ----------

def test_docs_only_diff_skips_without_running_suite(monkeypatch, comments, capsys):
    _patch_changed(monkeypatch, ["docs/architecture.md", "README.md"])

    def _boom(*a, **k):
        raise AssertionError("suite must not run for a docs-only diff")

    monkeypatch.setattr(ff, "_run_suite_on_branch", _boom)

    ff._baseline_check_or_exit(1, "feature/1-x", "develop", "owner/repo", False)

    assert "documentation only" in capsys.readouterr().out
    assert comments == []


# --- AC: a timeout refuses, and says it is unverified rather than failed ---

def test_timeout_refuses_and_distinguishes_unverified_from_failed(monkeypatch, comments):
    _patch_changed(monkeypatch, ["services/foo.py"])
    _patch_suite(monkeypatch, "", timed_out=True)

    with pytest.raises(SystemExit) as exc:
        ff._baseline_check_or_exit(7, "feature/7-x", "develop", "owner/repo", False)
    assert exc.value.code == 1

    assert comments, "a timeout must be reported on the issue"
    body = comments[0][1]
    assert "could not complete" in body
    assert "unverified, not because it failed" in body


def test_timeout_with_override_proceeds_and_records(monkeypatch, comments):
    _patch_changed(monkeypatch, ["services/foo.py"])
    _patch_suite(monkeypatch, "", timed_out=True)

    ff._baseline_check_or_exit(7, "feature/7-x", "develop", "owner/repo", True)

    bodies = " ".join(b for _, b in comments)
    assert "overridden by the operator" in bodies.lower()


# --- AC: an override is recorded on the issue ------------------------------

def test_override_records_a_comment(monkeypatch, comments):
    _patch_changed(monkeypatch, ["services/foo.py"])
    _patch_suite(monkeypatch, ONE_NEW_FAILURE)
    # No baseline recorded -> refusal; override should proceed and record it.
    ff._baseline_check_or_exit(9, "feature/9-x", "develop", "owner/repo", True)

    bodies = " ".join(b for _, b in comments)
    assert "overridden by the operator" in bodies.lower()


def test_no_baseline_refuses_without_override(monkeypatch, comments):
    _patch_changed(monkeypatch, ["services/foo.py"])
    _patch_suite(monkeypatch, PASSING_OUTPUT)

    with pytest.raises(SystemExit) as exc:
        ff._baseline_check_or_exit(11, "feature/11-x", "develop", "owner/repo", False)
    assert exc.value.code == 1
    assert any("no baseline" in b.lower() for _, b in comments)


# --- The gate runs before the merge, not after -----------------------------

def test_gate_is_invoked_before_any_merge_command():
    """Guard the ordering: the check must precede `git merge` in main()."""
    src = (REPO_ROOT / "scripts" / "finish_feature.py").read_text(encoding="utf-8")
    gate_at = src.index("_baseline_check_or_exit(args.issue")
    merge_at = src.index('"git", "merge", "--no-ff"')
    assert gate_at < merge_at, "baseline check must run before the merge"


# --- Diff helper -----------------------------------------------------------

def test_changed_files_parses_and_strips(monkeypatch):
    monkeypatch.setattr(ff, "_try", lambda *c: (True, "a.py\n\nb/c.md\n"))
    assert ff._changed_files_vs_target("feature/1-x", "develop") == ["a.py", "b/c.md"]


def test_changed_files_returns_empty_on_git_failure(monkeypatch):
    monkeypatch.setattr(ff, "_try", lambda *c: (False, ""))
    assert ff._changed_files_vs_target("feature/1-x", "develop") == []


# --- Rollout kill switch ---------------------------------------------------

def test_kill_switch_disables_the_check(monkeypatch, comments, capsys):
    """COMMANDER_BASELINE_CHECK=0 must skip without running the suite.

    Without this, landing the gate would refuse every merge on any project that
    has not recorded a baseline yet.
    """
    monkeypatch.setenv("COMMANDER_BASELINE_CHECK", "0")

    def _boom(*a, **k):
        raise AssertionError("suite must not run when the check is disabled")

    monkeypatch.setattr(ff, "_run_suite_on_branch", _boom)
    monkeypatch.setattr(ff, "_changed_files_vs_target", _boom)

    ff._baseline_check_or_exit(1, "feature/1-x", "develop", "owner/repo", False)

    assert "disabled" in capsys.readouterr().out
    assert comments == []


def test_check_is_on_by_default(monkeypatch, comments):
    """Absent the env var the gate runs — a gate defaulting to off is not a gate."""
    monkeypatch.delenv("COMMANDER_BASELINE_CHECK", raising=False)
    _patch_changed(monkeypatch, ["services/foo.py"])
    _patch_suite(monkeypatch, PASSING_OUTPUT)

    with pytest.raises(SystemExit):
        ff._baseline_check_or_exit(2, "feature/2-x", "develop", "owner/repo", False)
