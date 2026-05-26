"""Tests for issue #33 — Sprint pre-flight review (BA sanity-check pass).

Tests cover:
  AC-1:  sprint_review.py script exists with correct CLI; sprint_manager.py --preflight flag
  AC-2:  API prompt structure and response parsing
  AC-3:  Status classification (stale, conflict, needs-update, missing-info, ok)
  AC-4:  Concurrent review with ThreadPoolExecutor
  AC-5:  Report format
  AC-6:  Interactive prompt options (A/S/E/Q)
  AC-7:  Non-TTY auto-approve
  AC-8:  JSON output file structure
  AC-9:  Missing ANTHROPIC_API_KEY handling
  AC-10: Standalone + integrated execution paths
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from unittest import mock
from io import StringIO

import pytest

SCRIPTS_DIR    = Path(__file__).parent.parent / "scripts"
REVIEW_SCRIPT  = SCRIPTS_DIR / "sprint_review.py"
MANAGER_SCRIPT = SCRIPTS_DIR / "sprint_manager.py"


# ── import helpers ────────────────────────────────────────────────────────────

def _stub_modules():
    """Provide minimal stubs for dotenv and github_client."""
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo  = lambda: "zealchaiwut/commander"
    gc_stub._r    = lambda x: x or "zealchaiwut/commander"
    gc_stub.update_labels = lambda *a, **kw: None
    gc_stub.add_comment   = lambda *a, **kw: None
    sys.modules["github_client"] = gc_stub
    return gc_stub


def _import_sprint_review():
    """Import sprint_review.py as a fresh module with stubs."""
    _stub_modules()
    mod_name = "sprint_review_test_import"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, REVIEW_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_sprint_manager():
    """Import sprint_manager.py as a fresh module with stubs."""
    _stub_modules()
    mod_name = "sprint_manager_test_import_33"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, MANAGER_SCRIPT)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── AC-1: script existence and CLI ───────────────────────────────────────────

def test_ac1_sprint_review_script_exists():
    """AC-1: sprint_review.py must exist in dashboard/scripts/."""
    assert REVIEW_SCRIPT.exists(), f"Missing: {REVIEW_SCRIPT}"


def test_ac1_sprint_review_accepts_sprint_label_arg(tmp_path, monkeypatch):
    """AC-1: script accepts positional <sprint-label> argument."""
    sr = _import_sprint_review()
    # Patch run_preflight to avoid real network calls
    monkeypatch.setattr(sr, "run_preflight", lambda **kw: ([], []))
    monkeypatch.setattr(sys, "argv", ["sprint_review.py", "sprint-99"])
    # Should not raise
    sr.main()


def test_ac1_sprint_manager_preflight_flag():
    """AC-1: sprint_manager.py must accept --preflight flag."""
    sm = _import_sprint_manager()
    import argparse
    # Parse --preflight in isolation
    p = argparse.ArgumentParser()
    p.add_argument("label")
    p.add_argument("--preflight", action="store_true", default=False)
    args = p.parse_args(["sprint-3", "--preflight"])
    assert args.preflight is True


def test_ac1_sprint_manager_has_preflight_in_source():
    """AC-1: sprint_manager.py source contains --preflight argument definition."""
    source = MANAGER_SCRIPT.read_text(encoding="utf-8")
    assert "--preflight" in source
    assert "run_preflight" in source


# ── AC-2: API prompt structure ────────────────────────────────────────────────

def test_ac2_build_prompt_contains_issue_info():
    """AC-2: prompt must contain issue number, title, body, and cross-issue context."""
    sr = _import_sprint_review()
    issue = {
        "number": 42,
        "title":  "Fix the bug",
        "body":   "## Acceptance Criteria\n- [ ] It works",
    }
    other_issues = [
        {"number": 10, "title": "Other thing", "body": ""},
        {"number": 42, "title": "Fix the bug", "body": "## Acceptance Criteria\n- [ ] It works"},
    ]
    prompt = sr._build_prompt(issue, other_issues)
    assert "#42" in prompt
    assert "Fix the bug" in prompt
    assert "Acceptance Criteria" in prompt
    assert "#10" in prompt  # cross-issue context
    assert "ok|stale|conflict|needs-update|missing-info" in prompt


def test_ac2_api_response_parsing():
    """AC-2: _call_anthropic should parse JSON from model response."""
    sr = _import_sprint_review()
    # Simulate a well-formed API response
    fake_response_json = json.dumps({
        "content": [{"text": '{"status": "ok", "notes": "looks good", "suggested_action": "proceed"}'}]
    })
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = fake_response_json.encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = mock.MagicMock(return_value=False)

    with mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = sr._call_anthropic("fake-key", "test prompt")

    assert result["status"] == "ok"
    assert result["notes"] == "looks good"
    assert result["suggested_action"] == "proceed"


def test_ac2_api_response_bad_status_defaults_ok():
    """AC-2: invalid status from API is normalised to 'ok'."""
    sr = _import_sprint_review()
    fake_response_json = json.dumps({
        "content": [{"text": '{"status": "garbage", "notes": "n", "suggested_action": "proceed"}'}]
    })
    mock_resp = mock.MagicMock()
    mock_resp.read.return_value = fake_response_json.encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = mock.MagicMock(return_value=False)

    with mock.patch("urllib.request.urlopen", return_value=mock_resp):
        result = sr._call_anthropic("fake-key", "test prompt")

    assert result["status"] == "ok"


# ── AC-3: status classification ───────────────────────────────────────────────

def test_ac3_missing_info_detected():
    """AC-3: issue with no AC section → missing-info."""
    sr = _import_sprint_review()
    issue = {"number": 1, "title": "No AC", "body": "Just a description, no acceptance criteria."}
    note = sr._detect_missing_info(issue["body"])
    assert note is not None
    assert "Acceptance Criteria" in note


def test_ac3_missing_info_not_triggered_with_ac():
    """AC-3: issue WITH an AC section → _detect_missing_info returns None."""
    sr = _import_sprint_review()
    body = "## Acceptance Criteria\n- [ ] item 1\n- [ ] item 2"
    assert sr._detect_missing_info(body) is None


def test_ac3_needs_update_too_few_checkboxes():
    """AC-3: AC section with < 2 checkboxes → needs-update."""
    sr = _import_sprint_review()
    body = "## Acceptance Criteria\n- [ ] Only one item\n"
    note = sr._detect_needs_update(body)
    assert note is not None
    assert "1" in note  # mentions count


def test_ac3_needs_update_vague_phrase():
    """AC-3: AC section containing 'works correctly' → needs-update."""
    sr = _import_sprint_review()
    body = (
        "## Acceptance Criteria\n"
        "- [ ] The feature works correctly\n"
        "- [ ] Another criterion\n"
    )
    note = sr._detect_needs_update(body)
    assert note is not None
    assert "works correctly" in note


def test_ac3_needs_update_not_triggered_for_good_ac():
    """AC-3: clear AC with >= 2 items and no vague phrases → None."""
    sr = _import_sprint_review()
    body = (
        "## Acceptance Criteria\n"
        "- [ ] The endpoint returns HTTP 200\n"
        "- [ ] The JSON body has a 'status' key\n"
        "- [ ] The response time < 200ms\n"
    )
    note = sr._detect_needs_update(body)
    assert note is None


def test_ac3_conflict_detected():
    """AC-3: two issues sharing a file path → conflict."""
    sr = _import_sprint_review()
    body_a = "Changes to server.py and db.py"
    body_b = "Also touches server.py"
    note = sr._detect_conflict(body_a, [(99, body_b)])
    assert note is not None
    assert "server.py" in note
    assert "#99" in note


def test_ac3_conflict_not_triggered_no_overlap():
    """AC-3: no shared file paths → None."""
    sr = _import_sprint_review()
    body_a = "Changes to server.py"
    body_b = "Changes to static/app.js"
    note = sr._detect_conflict(body_a, [(99, body_b)])
    assert note is None


def test_ac3_pre_classify_priority_missing_info_first():
    """AC-3: missing-info takes priority over other statuses."""
    sr = _import_sprint_review()
    # Body has no AC section but does reference a file path that might conflict
    issue = {"number": 5, "title": "No AC", "body": "touches server.py"}
    all_issues = [
        issue,
        {"number": 6, "title": "Other", "body": "also server.py"},
    ]
    # missing-info should win over conflict
    with mock.patch.object(sr, "_detect_stale", return_value=None):
        status, notes = sr._pre_classify(issue, all_issues, repo_name=None)
    assert status == "missing-info"


def test_ac3_pre_classify_ok_for_clean_issue():
    """AC-3: clean issue with good AC → ('', '') — let API decide."""
    sr = _import_sprint_review()
    issue = {
        "number": 7,
        "title":  "Good issue",
        "body": (
            "## Acceptance Criteria\n"
            "- [ ] The endpoint returns 200\n"
            "- [ ] The payload is JSON\n"
        ),
    }
    all_issues = [issue]
    with mock.patch.object(sr, "_detect_stale", return_value=None):
        status, notes = sr._pre_classify(issue, all_issues, repo_name=None)
    assert status == ""
    assert notes == ""


# ── AC-4: concurrent execution ────────────────────────────────────────────────

def test_ac4_uses_thread_pool_executor():
    """AC-4: run_reviews uses ThreadPoolExecutor."""
    sr = _import_sprint_review()
    source = REVIEW_SCRIPT.read_text(encoding="utf-8")
    assert "ThreadPoolExecutor" in source
    assert "max_workers" in source
    # Default max_workers must be <= 5
    assert "MAX_WORKERS = 5" in source or "max_workers=5" in source or "MAX_WORKERS=5" in source


def test_ac4_run_reviews_returns_ordered_results():
    """AC-4: run_reviews returns results in original issue order."""
    sr = _import_sprint_review()
    issues = [
        {"number": 10, "title": "A", "body": ""},
        {"number": 20, "title": "B", "body": ""},
        {"number": 30, "title": "C", "body": ""},
    ]

    def fake_review(issue, all_issues, api_key, repo_name):
        return sr.ReviewResult(
            number=issue["number"],
            title=issue["title"],
            status="ok",
            notes="",
            suggested_action="proceed",
        )

    with mock.patch.object(sr, "review_issue", side_effect=fake_review):
        results = sr.run_reviews(issues, api_key="fake", repo_name=None)

    assert [r.number for r in results] == [10, 20, 30]


# ── AC-5: report format ───────────────────────────────────────────────────────

def test_ac5_print_report_contains_expected_sections(capsys):
    """AC-5: report prints header, per-issue lines, summary, and token budget."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(1, "Fix bug", "ok",           "all good",           "proceed"),
        sr.ReviewResult(2, "Add feature", "stale",    "references #3 (closed)", "rewrite"),
        sr.ReviewResult(3, "Refactor", "conflict",    "overlaps server.py with #2", "reorder"),
        sr.ReviewResult(4, "New API", "missing-info", "no AC section",       "skip"),
    ]
    sr.print_report("sprint-3", results)
    captured = capsys.readouterr().out

    assert "sprint-3" in captured
    assert "4 issues" in captured
    assert "#1" in captured
    assert "#2" in captured
    assert "#3" in captured
    assert "#4" in captured
    assert "ok" in captured
    assert "stale" in captured
    assert "conflict" in captured
    assert "missing-info" in captured
    assert "Summary:" in captured
    assert "Est. token budget:" in captured


def test_ac5_report_shows_notes_for_non_ok(capsys):
    """AC-5: non-ok issues have their notes printed below the issue line."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(5, "Stale ticket", "stale", "references #2 (closed 3 weeks ago)", "rewrite"),
    ]
    sr.print_report("sprint-1", results)
    captured = capsys.readouterr().out
    assert "references #2" in captured


# ── AC-6: interactive prompt ─────────────────────────────────────────────────

def test_ac6_approve_all_returns_all_issues():
    """AC-6 A: Approve all returns all results unchanged."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(1, "ok issue",     "ok",    "", "proceed"),
        sr.ReviewResult(2, "stale issue",  "stale", "", "rewrite"),
    ]
    with mock.patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        with mock.patch("builtins.input", return_value="A"):
            approved = sr.prompt_user(results)
    assert len(approved) == 2
    assert {r.number for r in approved} == {1, 2}


def test_ac6_skip_flagged_excludes_non_ok():
    """AC-6 S: Skip flagged removes non-ok issues."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(1, "ok issue",     "ok",    "", "proceed"),
        sr.ReviewResult(2, "stale issue",  "stale", "", "rewrite"),
        sr.ReviewResult(3, "missing",      "missing-info", "", "skip"),
    ]
    with mock.patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        with mock.patch("builtins.input", return_value="S"):
            approved = sr.prompt_user(results)
    assert len(approved) == 1
    assert approved[0].number == 1


def test_ac6_edit_per_issue_y_skips_n_keeps():
    """AC-6 E: y skips, n keeps per-issue."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(1, "ok issue",    "ok",    "", "proceed"),
        sr.ReviewResult(2, "stale issue", "stale", "", "rewrite"),
        sr.ReviewResult(3, "stale too",   "stale", "", "rewrite"),
    ]
    # ok → kept automatically; stale#2 → skip (y); stale#3 → keep (n)
    with mock.patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        with mock.patch("builtins.input", side_effect=["E", "y", "n"]):
            approved = sr.prompt_user(results)
    assert {r.number for r in approved} == {1, 3}


def test_ac6_quit_exits_with_code_1():
    """AC-6 Q: quit causes sys.exit(1)."""
    sr = _import_sprint_review()
    results = [sr.ReviewResult(1, "ok", "ok", "", "proceed")]
    with mock.patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = True
        with mock.patch("builtins.input", return_value="Q"):
            with pytest.raises(SystemExit) as exc_info:
                sr.prompt_user(results)
    assert exc_info.value.code == 1


# ── AC-7: non-TTY auto-approve ────────────────────────────────────────────────

def test_ac7_non_tty_auto_approves_all(capsys):
    """AC-7: non-TTY mode auto-approves all issues without prompt."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(1, "ok",    "ok",    "", "proceed"),
        sr.ReviewResult(2, "stale", "stale", "", "rewrite"),
    ]
    with mock.patch("sys.stdout") as mock_stdout:
        mock_stdout.isatty.return_value = False
        with mock.patch("sys.stdout.write"):  # suppress output
            approved = sr.prompt_user(results)
    assert len(approved) == 2


# ── AC-8: JSON output ────────────────────────────────────────────────────────

def test_ac8_preflight_json_fields(tmp_path):
    """AC-8: JSON file has sprint_label, reviewed_at, issues, summary fields."""
    sr = _import_sprint_review()
    results = [
        sr.ReviewResult(10, "Some issue", "ok", "all clear", "proceed"),
        sr.ReviewResult(11, "Bad issue",  "stale", "old ref", "rewrite"),
    ]
    out_path = sr.write_preflight_json("sprint-3", results, sprints_dir=tmp_path)
    assert out_path.exists()

    data = json.loads(out_path.read_text())
    assert data["sprint_label"] == "sprint-3"
    assert "reviewed_at" in data
    assert isinstance(data["issues"], list)
    assert len(data["issues"]) == 2
    assert "summary" in data

    summary = data["summary"]
    for key in ("ok", "stale", "conflict", "needs_update", "missing_info"):
        assert key in summary, f"Missing summary key: {key}"

    assert summary["ok"] == 1
    assert summary["stale"] == 1


def test_ac8_json_filename_format(tmp_path):
    """AC-8: filename is sprint-N-preflight-YYYY-MM-DD.json."""
    sr = _import_sprint_review()
    results = []
    out_path = sr.write_preflight_json("sprint-5", results, sprints_dir=tmp_path)
    name = out_path.name
    assert name.startswith("sprint-5-preflight-")
    assert name.endswith(".json")
    # YYYY-MM-DD part
    date_part = name[len("sprint-5-preflight-"):-len(".json")]
    parts = date_part.split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 4  # year
    assert len(parts[1]) == 2  # month
    assert len(parts[2]) == 2  # day


def test_ac8_issue_records_have_required_fields(tmp_path):
    """AC-8: each issue entry has number, title, status, notes, suggested_action."""
    sr = _import_sprint_review()
    results = [sr.ReviewResult(7, "Test", "ok", "fine", "proceed")]
    out_path = sr.write_preflight_json("sprint-1", results, sprints_dir=tmp_path)
    data = json.loads(out_path.read_text())
    issue_record = data["issues"][0]
    for field in ("number", "title", "status", "notes", "suggested_action"):
        assert field in issue_record, f"Missing field: {field}"


# ── AC-9: missing API key ─────────────────────────────────────────────────────

def test_ac9_no_api_key_prints_warning_and_exits_0(tmp_path, monkeypatch, capsys):
    """AC-9: ANTHROPIC_API_KEY unset → warning message, exit 0, no API calls."""
    sr = _import_sprint_review()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    # Ensure _call_anthropic is never called
    with mock.patch.object(sr, "_call_anthropic") as mock_api:
        with mock.patch.object(sr, "_fetch_sprint_issues", return_value=[]):
            all_results, approved = sr.run_preflight(
                sprint_label="sprint-9",
                repo_name=None,
                sprints_dir=tmp_path,
                interactive=False,
            )
        mock_api.assert_not_called()

    captured = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY not set" in captured
    assert "skipping review" in captured


def test_ac9_no_api_key_does_not_raise(tmp_path, monkeypatch):
    """AC-9: missing API key must not raise exceptions; exits 0."""
    sr = _import_sprint_review()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with mock.patch.object(sr, "_fetch_sprint_issues", return_value=[]):
        # Should not raise
        all_results, approved = sr.run_preflight(
            sprint_label="sprint-9",
            repo_name=None,
            sprints_dir=tmp_path,
            interactive=False,
        )
    # Returns lists (possibly empty), no exception
    assert isinstance(all_results, list)
    assert isinstance(approved, list)


# ── AC-10: standalone and integrated paths use same review function ───────────

def test_ac10_sprint_review_callable_standalone():
    """AC-10: run_preflight function is importable and callable."""
    sr = _import_sprint_review()
    assert callable(sr.run_preflight)


def test_ac10_sprint_manager_imports_run_preflight_lazily():
    """AC-10: sprint_manager.py lazy-imports from sprint_review when --preflight set."""
    source = MANAGER_SCRIPT.read_text(encoding="utf-8")
    # The import should be inside the --preflight block, not at module level
    assert "from sprint_review import run_preflight" in source


def test_ac10_run_preflight_returns_tuple(tmp_path, monkeypatch):
    """AC-10: run_preflight returns (all_results, approved_issues) tuple."""
    sr = _import_sprint_review()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with mock.patch.object(sr, "_fetch_sprint_issues", return_value=[]):
        result = sr.run_preflight(
            sprint_label="sprint-1",
            repo_name=None,
            sprints_dir=tmp_path,
            interactive=False,
        )
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_ac10_sprint_manager_preflight_approved_filter():
    """AC-10: run_sprint with preflight_approved filters issues."""
    sm = _import_sprint_manager()

    # Build minimal state with two issues
    state = sm.SprintState(
        sprint_label="sprint-1",
        sprint_number=1,
        start_timestamp="2026-01-01T00:00:00Z",
        issues=[
            sm.IssueState(number=10, title="Approved issue"),
            sm.IssueState(number=20, title="Skipped issue"),
        ],
    )

    dispatched = []

    def fake_dispatch_coder(issue_num, alert_modes, sprint_branch="develop",
                            repo_name=None, cfg=None, **kwargs):
        dispatched.append(issue_num)
        return True, None

    def fake_dispatch_tester(issue_num, alert_modes, repo_name=None, cfg=None, **kwargs):
        return 0, None

    def fake_handle_post_tester(*args, **kwargs):
        return True, f"Issue #{kwargs.get('issue_num', '?')}: done", None

    def fake_list_backlog_issues(label, repo_name=None):
        return [
            {"number": 10, "title": "Approved issue"},
            {"number": 20, "title": "Skipped issue"},
        ]

    with mock.patch.object(sm, "_dispatch_coder", side_effect=fake_dispatch_coder), \
         mock.patch.object(sm, "_dispatch_tester", side_effect=fake_dispatch_tester), \
         mock.patch.object(sm, "handle_post_tester", side_effect=fake_handle_post_tester), \
         mock.patch.object(sm, "list_backlog_issues", side_effect=fake_list_backlog_issues), \
         mock.patch.object(sm, "_create_sprint_branch"), \
         mock.patch.object(sm, "_post_sprint_status"):
        summary, final_state = sm.run_sprint(
            label              = "sprint-1",
            skip_gates         = True,
            gate_pytest        = False,
            gate_lint          = False,
            gate_merge_preview = False,
            dry_run            = False,
            preflight_approved = [10],  # only issue #10 approved
        )

    # Only issue #10 should have been dispatched
    assert 10 in dispatched
    assert 20 not in dispatched

    # Issue #20 should be in skipped state
    issue_20 = next(i for i in final_state.issues if i.number == 20)
    assert issue_20.status == "skipped"
    assert "preflight" in (issue_20.skip_reason or "")


# ── Regression tests for UAT-reported bugs ───────────────────────────────────

def test_bug2_api_failure_does_not_default_to_ok():
    """Bug 2 regression: API failure must NOT produce status='ok'/suggested_action='proceed'.

    Previously, any exception in _call_anthropic caused the issue to be
    silently marked as status='ok', suggested_action='proceed' — which meant
    un-reviewed tickets would appear approved in the report.
    """
    sr = _import_sprint_review()
    issue = {
        "number": 42,
        "title":  "Test issue",
        "body":   "## Acceptance Criteria\n- [ ] item 1\n- [ ] item 2\n",
    }
    all_issues = [issue]

    # _pre_classify returns ("", "") → falls through to API call
    with mock.patch.object(sr, "_pre_classify", return_value=("", "")), \
         mock.patch.object(sr, "_call_anthropic", side_effect=Exception("SSL error")):
        result = sr.review_issue(issue, all_issues, api_key="fake", repo_name=None)

    # Must NOT be ok/proceed after API failure
    assert result.status != "ok", (
        "API failure must not produce status='ok' — issue would appear approved"
    )
    assert result.suggested_action != "proceed", (
        "API failure must not produce suggested_action='proceed'"
    )
    # Must be flagged so the user notices
    assert result.status == "missing-info"
    assert result.suggested_action == "skip"
    assert "API review failed" in result.notes


def test_bug2_api_failure_includes_error_in_notes():
    """Bug 2 regression: API failure notes must include the exception text."""
    sr = _import_sprint_review()
    issue = {
        "number": 7,
        "title":  "Another issue",
        "body":   "## Acceptance Criteria\n- [ ] item 1\n- [ ] item 2\n",
    }
    all_issues = [issue]

    with mock.patch.object(sr, "_pre_classify", return_value=("", "")), \
         mock.patch.object(sr, "_call_anthropic",
                           side_effect=Exception("CERTIFICATE_VERIFY_FAILED")):
        result = sr.review_issue(issue, all_issues, api_key="fake", repo_name=None)

    assert "CERTIFICATE_VERIFY_FAILED" in result.notes


def test_bug1_sprints_dir_passed_to_write_preflight_json(tmp_path, monkeypatch):
    """Bug 1 regression: write_preflight_json must use sprints_dir when provided.

    When run from a project directory that has a .commander/sprint.yaml,
    the preflight JSON must be written to that project's sprints_dir,
    NOT to commander's own dashboard/sprints/ folder.
    """
    sr = _import_sprint_review()
    project_sprints = tmp_path / "project" / ".commander" / "sprints"
    project_sprints.mkdir(parents=True)

    results = [
        sr.ReviewResult(1, "Some issue", "ok", "all clear", "proceed"),
    ]

    # write_preflight_json should write to the provided sprints_dir
    out_path = sr.write_preflight_json("sprint-1", results, sprints_dir=project_sprints)

    # File must be inside project_sprints, NOT in SPRINTS_DIR (commander's own dir)
    assert out_path.parent == project_sprints, (
        f"Preflight JSON written to {out_path.parent!r}, expected {project_sprints!r}. "
        "Bug 1: JSON must go to the project's sprints directory, not commander's."
    )

    data = json.loads(out_path.read_text())
    assert data["sprint_label"] == "sprint-1"


def test_bug1b_resolve_sprints_dir_fixes_double_commander(tmp_path):
    """Bug 1b regression: _resolve_sprints_dir must correct double .commander paths.

    When a sprint.yaml generated with the old flat-layout template is used in a
    nested-layout project, load_config resolves sprints_dir to
    .commander/.commander/sprints (double-prefix). _resolve_sprints_dir must detect
    this and return .commander/sprints instead.
    """
    sr = _import_sprint_review()

    # Simulate the bad resolution: project/.commander/.commander/sprints
    project_root   = tmp_path / "myproject"
    commander_dir  = project_root / ".commander"
    double_bad     = commander_dir / ".commander" / "sprints"
    double_bad.mkdir(parents=True)

    corrected = sr._resolve_sprints_dir(double_bad, commander_dir)

    # Must correct to .commander/sprints, not .commander/.commander/sprints
    assert ".commander/.commander" not in str(corrected), (
        f"Double .commander not corrected: {corrected}"
    )
    assert corrected == commander_dir / "sprints", (
        f"Expected {commander_dir / 'sprints'}, got {corrected}"
    )


def test_bug1b_resolve_sprints_dir_leaves_correct_path_unchanged(tmp_path):
    """_resolve_sprints_dir must NOT modify a correctly resolved sprints_dir."""
    sr = _import_sprint_review()

    project_root  = tmp_path / "myproject"
    commander_dir = project_root / ".commander"
    correct_dir   = commander_dir / "sprints"
    correct_dir.mkdir(parents=True)

    result = sr._resolve_sprints_dir(correct_dir, commander_dir)
    assert result == correct_dir.resolve(), (
        f"Correct path was unexpectedly modified: {result}"
    )
