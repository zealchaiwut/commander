"""Tests for #701: Enrich gate failure comments with root cause analysis.

AC items verified:
  AC-1  When retries exhausted: GitHub comment with ## Gate Failure Analysis heading posted
  AC-2  Comment section 1 — Gate & Error: gate name + full untruncated error output
  AC-3  Comment section 2 — Root Cause present with analysis text
  AC-4  Comment section 3 — Prevention present with actionable text
  AC-5  Identical content appended to sprint log file under a dated entry
  AC-6  Existing needs-rework label + merge-block behavior unchanged (no regression)
  AC-7  Multiple gate failures in sequence → separate entries, no merging/dedup
  AC-8  Machine-parseable: consistent heading and section structure
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.sprint_manager as sm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_cfg(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.worktree_coder = tmp_path
    cfg.worktree_tester = tmp_path
    cfg.worktree_tester_app = tmp_path
    cfg.repo_name = None
    cfg.api_url = None
    cfg.coder_prompt_template = None
    cfg.logs_dir = tmp_path / "logs"
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    cfg.sprints_dir = tmp_path / "sprints"
    cfg.sprints_dir.mkdir(parents=True, exist_ok=True)
    cfg.app_default_port = None
    cfg.documentor_enabled = False
    return cfg


def _default_analysis() -> dict:
    return {
        "root_cause": "Type annotation missing on return value of process_item",
        "prevention": "Run `tsc --noEmit` locally before marking complete",
    }


def _run_sprint_loop(
    tmp_path: Path,
    *,
    gate_sequence: list,
    publish_calls: list,
    transition_calls: list | None = None,
    posted_comments: list | None = None,
) -> tuple:
    """Run run_sprint for issue #1 with controlled gate sequence.

    gate_sequence entries: (merged, summary_line, gate_category) — 3-tuples
    matching current handle_post_tester return signature.

    Captures calls to _publish_gate_failure_analyses (mocked).
    """
    if transition_calls is None:
        transition_calls = []
    if posted_comments is None:
        posted_comments = []

    coder_iter = iter([(True, None)] * 20)
    gate_iter = iter(gate_sequence)

    def fake_coder(issue_num, alert_modes, sprint_branch="develop",
                   repo_name=None, cfg=None, chosen_port=None,
                   rate_limit_events=None, on_running=None, sprint_label=None,
                   prior_failures=None, **kw):
        if on_running:
            on_running()
        return next(coder_iter)

    def fake_tester(issue_num, alert_modes, sprint_branch="develop",
                    repo_name=None, cfg=None, chosen_port=None,
                    rate_limit_events=None, on_running=None, sprint_label=None, **kw):
        if on_running:
            on_running()
        return 0, None

    def fake_gates(**kwargs):
        return next(gate_iter)

    def fake_transition(issue_num, target, actor=None, repo_name=None):
        transition_calls.append((issue_num, target))

    def fake_publish(issue_num, repo_name=None, cfg=None):
        publish_calls.append({"issue_num": issue_num, "repo_name": repo_name})

    def fake_add_comment(issue_num, body, repo_name=None):
        if posted_comments is not None:
            posted_comments.append({"issue": issue_num, "body": body})

    with (
        patch.object(sm, "_create_sprint_branch", lambda b, **kw: None),
        patch.object(sm, "list_backlog_issues",
                     lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
        patch.object(sm, "_dispatch_coder", fake_coder),
        patch.object(sm, "_dispatch_tester", fake_tester),
        patch.object(sm, "handle_post_tester", fake_gates),
        patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
        patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
        patch.object(sm, "_warn_file_conflicts", lambda i: None),
        patch.object(sm, "_setup_pid_file", lambda n: None),
        patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
        patch.object(sm, "_transition_safe", fake_transition),
        patch.object(sm, "record_failure", lambda *a, **kw: None),
        patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
        patch.object(sm, "_design_docs_guard", lambda p: None),
        patch.object(sm, "_publish_gate_failure_analyses", fake_publish),
        patch.object(sm.github_client, "add_comment", fake_add_comment),
        patch.object(sm, "_is_issue_merged_into_target", lambda *a, **kw: False),
        patch.object(sm, "_prune_stale_local_feature_branch", lambda *a, **kw: None),
    ):
        summary, state = sm.run_sprint(
            label="sprint-99",
            skip_gates=False,
            gate_pytest=False,
            gate_lint=False,
            gate_merge_preview=False,
            gate_typecheck=False,
            gate_design=False,
        )

    return summary, transition_calls, publish_calls


# ---------------------------------------------------------------------------
# AC-1: ## Gate Failure Analysis published when retries exhausted
# ---------------------------------------------------------------------------

class TestAC1AnalysisPublishedOnExhaustion:
    def test_publish_called_on_exhaustion(self, tmp_path):
        """_publish_gate_failure_analyses must be called when fix-loop exhausted."""
        # Use PYTEST_FAIL (in _LOGIC_FAILURE_CATEGORIES) so the fix-loop retries
        gate_fail = (False, "Issue #1: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL)
        publish_calls: list = []
        _run_sprint_loop(
            tmp_path,
            # consecutive dup → early abort after 2 attempts
            gate_sequence=[gate_fail, gate_fail],
            publish_calls=publish_calls,
        )
        assert len(publish_calls) == 1, (
            f"_publish_gate_failure_analyses must be called once on exhaustion; "
            f"got {len(publish_calls)}"
        )
        assert publish_calls[0]["issue_num"] == 1

    def test_publish_not_called_on_gate_pass(self, tmp_path):
        """_publish_gate_failure_analyses must NOT be called when gate passes."""
        publish_calls: list = []
        _run_sprint_loop(
            tmp_path,
            gate_sequence=[(True, "Issue #1: merged", None)],
            publish_calls=publish_calls,
        )
        assert not publish_calls, (
            "_publish_gate_failure_analyses must not be called when gate passes"
        )

    def test_publish_called_after_k_rounds_exhausted(self, tmp_path):
        """_publish_gate_failure_analyses called after all K rounds consumed (for/else path)."""
        # Alternating logic categories → no early abort; K=3 exhausts all rounds
        gate_sequence = [
            (False, "Issue #1: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL),
            (False, "Issue #1: gate failed (lint)", sm.FailureCategory.LINT_FAIL),
            (False, "Issue #1: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL),
        ]
        publish_calls: list = []
        _run_sprint_loop(tmp_path, gate_sequence=gate_sequence, publish_calls=publish_calls)
        assert len(publish_calls) == 1, (
            "_publish_gate_failure_analyses must fire after K rounds exhausted"
        )

    def test_publish_not_called_on_infra_failure(self, tmp_path):
        """Infra failures (CRASH) bypass fix-loop — no Gate Failure Analysis."""
        publish_calls: list = []
        coder_iter = iter([(False, sm.FailureCategory.CRASH)])

        def fake_coder_crash(issue_num, alert_modes, sprint_branch="develop",
                             repo_name=None, cfg=None, chosen_port=None,
                             rate_limit_events=None, on_running=None,
                             sprint_label=None, prior_failures=None, **kw):
            if on_running:
                on_running()
            return next(coder_iter)

        with (
            patch.object(sm, "_create_sprint_branch", lambda b, **kw: None),
            patch.object(sm, "list_backlog_issues",
                         lambda label, repo_name=None: [{"number": 1, "title": "T"}]),
            patch.object(sm, "_dispatch_coder", fake_coder_crash),
            patch.object(sm, "_dispatch_tester", lambda *a, **kw: (0, None)),
            patch.object(sm, "_post_sprint_status", lambda *a, **kw: None),
            patch.object(sm, "_neon_ticket_status", lambda *a, **kw: None),
            patch.object(sm, "_warn_file_conflicts", lambda i: None),
            patch.object(sm, "_setup_pid_file", lambda n: None),
            patch.object(sm, "_find_feature_branch", lambda n: f"feature/{n}-stub"),
            patch.object(sm, "_transition_safe", lambda *a, **kw: None),
            patch.object(sm, "record_failure", lambda *a, **kw: None),
            patch.object(sm, "dispatch_alerts", lambda *a, **kw: None),
            patch.object(sm, "_design_docs_guard", lambda p: None),
            patch.object(sm, "_publish_gate_failure_analyses",
                         lambda *a, **kw: publish_calls.append(True)),
            patch.object(sm, "_is_issue_merged_into_target", lambda *a, **kw: False),
            patch.object(sm, "_prune_stale_local_feature_branch", lambda *a, **kw: None),
        ):
            sm.run_sprint(
                label="sprint-99",
                skip_gates=False,
                gate_pytest=False,
                gate_lint=False,
                gate_merge_preview=False,
                gate_typecheck=False,
                gate_design=False,
            )
        assert not publish_calls, (
            "CRASH (infra) must not trigger Gate Failure Analysis"
        )


# ---------------------------------------------------------------------------
# AC-2: Gate & Error section — gate name + full output
# ---------------------------------------------------------------------------

class TestAC2PostGateFailureAnalysisCommentFormat:
    """Unit-test _post_gate_failure_analysis_comment directly."""

    def test_heading_present(self, tmp_path):
        """Comment body must contain ## Gate Failure Analysis heading."""
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="typecheck",
                error_output="TS2322: Type 'number' is not assignable to type 'string'",
                root_cause="Return type annotation missing",
                prevention="Run `tsc --noEmit` before submitting",
            )
        assert posted, "add_comment must be called"
        body = posted[0]
        assert "## Gate Failure Analysis" in body

    def test_section_1_gate_and_error(self, tmp_path):
        """Comment section 1 has Gate & Error heading, gate name, and error output."""
        error = "TS2322: Type 'number' is not assignable to type 'string'"
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="typecheck",
                error_output=error,
                root_cause="Missing annotation",
                prevention="Run tsc",
            )
        body = posted[0]
        assert "Gate & Error" in body, "Section heading 'Gate & Error' missing"
        assert "typecheck" in body, "Gate name 'typecheck' must appear in body"
        assert error in body, "Full error output must appear in body"

    def test_section_1_includes_full_long_error(self, tmp_path):
        """Error output is NOT truncated (AC-2: untruncated)."""
        long_error = "error: TS2322 " + "x" * 3000
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="typecheck",
                error_output=long_error,
                root_cause="rc",
                prevention="pv",
            )
        body = posted[0]
        # At minimum the first 3000 chars of the error must appear
        assert long_error[:3000] in body, (
            "Full untruncated error output must appear in the Gate Failure Analysis comment"
        )

    def test_section_1_gate_name_is_labeled(self, tmp_path):
        """Gate name is explicitly labeled (machine-parseable per AC-8)."""
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="pytest",
                error_output="FAILED tests/test_x.py::test_y",
                root_cause="rc",
                prevention="pv",
            )
        body = posted[0]
        # Gate name should be labeled — e.g. "**Gate:** pytest" or "Gate: pytest"
        assert re.search(r"\*\*Gate.*\*\*.*pytest|Gate[^:]*:.*pytest", body), (
            f"Gate name must be clearly labeled in the comment for machine parsing:\n{body}"
        )


# ---------------------------------------------------------------------------
# AC-3: Root Cause section present
# ---------------------------------------------------------------------------

class TestAC3RootCause:
    def test_root_cause_section_heading_present(self, tmp_path):
        """Comment body contains ### Root Cause heading."""
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="lint",
                error_output="E302 expected 2 blank lines",
                root_cause="CSS variable name does not follow --color-* convention",
                prevention="Match --color-* naming",
            )
        assert "Root Cause" in posted[0], "Root Cause section must be present"

    def test_root_cause_content_appears_in_comment(self, tmp_path):
        """The generated root_cause text appears in the comment."""
        rc_text = "CSS variable name does not follow --color-* convention"
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="lint",
                error_output="ruff E501",
                root_cause=rc_text,
                prevention="fix it",
            )
        assert rc_text in posted[0], "root_cause text must appear in comment body"


# ---------------------------------------------------------------------------
# AC-4: Prevention section present
# ---------------------------------------------------------------------------

class TestAC4Prevention:
    def test_prevention_section_heading_present(self, tmp_path):
        """Comment body contains ### Prevention heading."""
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="pytest",
                error_output="FAILED test_foo.py",
                root_cause="Missing test",
                prevention="Write pytest for every new endpoint before submission",
            )
        assert "Prevention" in posted[0], "Prevention section must be present"

    def test_prevention_content_appears_in_comment(self, tmp_path):
        """The generated prevention text appears in the comment."""
        pv_text = "Write pytest for every new endpoint before submission"
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="pytest",
                error_output="FAILED",
                root_cause="no tests",
                prevention=pv_text,
            )
        assert pv_text in posted[0], "prevention text must appear in comment body"


# ---------------------------------------------------------------------------
# AC-5: Sprint log file — dated entry
# ---------------------------------------------------------------------------

class TestAC5SprintLog:
    def test_append_creates_file(self, tmp_path):
        """_append_gate_failure_to_sprint_log creates the log file."""
        cfg = _fake_cfg(tmp_path)
        log_path = tmp_path / "logs" / "gate-failures.md"

        with patch.object(sm, "_gate_failures_log_path", return_value=log_path):
            sm._append_gate_failure_to_sprint_log(
                issue_num=1,
                gate_name="typecheck",
                error_output="TS2322 error",
                root_cause="rc text",
                prevention="pv text",
                cfg=cfg,
            )
        assert log_path.exists(), "Sprint log file must be created"

    def test_append_has_gfa_heading(self, tmp_path):
        """Sprint log entry contains ## Gate Failure Analysis heading."""
        cfg = _fake_cfg(tmp_path)
        log_path = tmp_path / "logs" / "gate-failures.md"

        with patch.object(sm, "_gate_failures_log_path", return_value=log_path):
            sm._append_gate_failure_to_sprint_log(
                issue_num=1,
                gate_name="typecheck",
                error_output="TS2322",
                root_cause="rc",
                prevention="pv",
                cfg=cfg,
            )
        content = log_path.read_text()
        assert "## Gate Failure Analysis" in content

    def test_append_has_dated_entry(self, tmp_path):
        """Sprint log entry contains a date stamp."""
        cfg = _fake_cfg(tmp_path)
        log_path = tmp_path / "logs" / "gate-failures.md"

        with patch.object(sm, "_gate_failures_log_path", return_value=log_path):
            sm._append_gate_failure_to_sprint_log(
                issue_num=1,
                gate_name="pytest",
                error_output="FAILED",
                root_cause="rc",
                prevention="pv",
                cfg=cfg,
            )
        content = log_path.read_text()
        assert re.search(r"\d{4}-\d{2}-\d{2}", content), (
            "Sprint log entry must contain a date stamp"
        )

    def test_append_has_gate_name_and_outputs(self, tmp_path):
        """Sprint log entry contains gate name, root cause, and prevention."""
        cfg = _fake_cfg(tmp_path)
        log_path = tmp_path / "logs" / "gate-failures.md"
        rc_text = "Missing return type annotation"
        pv_text = "Run `tsc --noEmit` before marking complete"

        with patch.object(sm, "_gate_failures_log_path", return_value=log_path):
            sm._append_gate_failure_to_sprint_log(
                issue_num=42,
                gate_name="typecheck",
                error_output="TS2322 error details",
                root_cause=rc_text,
                prevention=pv_text,
                cfg=cfg,
            )
        content = log_path.read_text()
        assert "typecheck" in content
        assert rc_text in content
        assert pv_text in content

    def test_append_accumulates_entries(self, tmp_path):
        """Multiple calls to append accumulate entries in the same file."""
        cfg = _fake_cfg(tmp_path)
        log_path = tmp_path / "logs" / "gate-failures.md"

        with patch.object(sm, "_gate_failures_log_path", return_value=log_path):
            sm._append_gate_failure_to_sprint_log(
                issue_num=1, gate_name="typecheck",
                error_output="e1", root_cause="rc1", prevention="pv1", cfg=cfg,
            )
            sm._append_gate_failure_to_sprint_log(
                issue_num=1, gate_name="lint",
                error_output="e2", root_cause="rc2", prevention="pv2", cfg=cfg,
            )
        content = log_path.read_text()
        assert content.count("## Gate Failure Analysis") == 2, (
            "Two appended entries must produce two ## Gate Failure Analysis sections"
        )


# ---------------------------------------------------------------------------
# AC-6: Existing behavior unchanged
# ---------------------------------------------------------------------------

class TestAC6ExistingBehaviorUnchanged:
    def test_needs_rework_still_applied_on_exhaustion(self, tmp_path):
        """NEEDS_REWORK transition must still fire after exhaustion (no regression)."""
        gate_fail = (False, "Issue #1: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL)
        publish_calls: list = []
        transition_calls: list = []
        _run_sprint_loop(
            tmp_path,
            gate_sequence=[gate_fail, gate_fail],
            publish_calls=publish_calls,
            transition_calls=transition_calls,
        )
        needs_rework = [
            (n, t) for n, t in transition_calls
            if t is not None and hasattr(t, "value") and "needs" in str(t.value).lower()
        ]
        assert needs_rework, (
            "NEEDS_REWORK transition must still fire after exhaustion — no regression"
        )

    def test_merged_empty_on_exhaustion(self, tmp_path):
        """Issue must NOT appear in merged list when gate is exhausted."""
        gate_fail = (False, "Issue #1: gate failed (lint)", sm.FailureCategory.LINT_FAIL)
        publish_calls: list = []
        summary, _, _ = _run_sprint_loop(
            tmp_path,
            gate_sequence=[gate_fail, gate_fail],
            publish_calls=publish_calls,
        )
        assert not summary.merged, (
            "Exhausted issue must NOT be in merged list (no regression)"
        )

    def test_analysis_published_does_not_block_sprint_completion(self, tmp_path):
        """Sprint summary is returned even if _publish_gate_failure_analyses is called."""
        gate_fail = (False, "Issue #1: gate failed (pytest)", sm.FailureCategory.PYTEST_FAIL)
        publish_calls: list = []
        summary, _, _ = _run_sprint_loop(
            tmp_path,
            gate_sequence=[gate_fail, gate_fail],  # consecutive dup → early abort
            publish_calls=publish_calls,
        )
        assert summary is not None, "SprintSummary must still be returned"


# ---------------------------------------------------------------------------
# AC-7: Multiple gate failures → separate entries
# ---------------------------------------------------------------------------

class TestAC7MultipleFailuresSeparateEntries:
    def test_publish_gate_failure_analyses_posts_per_record(self, tmp_path):
        """_publish_gate_failure_analyses posts one comment per gate failure record."""
        records = [
            {"gate_name": "typecheck", "output": "TS2322 error", "timestamp": "2026-01-01"},
            {"gate_name": "lint",      "output": "E302 blank lines", "timestamp": "2026-01-01"},
        ]
        posted = []
        analysis = {"root_cause": "rc", "prevention": "pv"}

        with (
            patch.object(sm, "_read_gate_failure_records", return_value=records),
            patch.object(sm, "_clear_gate_failure_records", lambda *a, **kw: None),
            patch.object(sm, "_generate_gate_failure_analysis", return_value=analysis),
            patch.object(sm, "_append_gate_failure_to_sprint_log", lambda *a, **kw: None),
            patch.object(sm.github_client, "add_comment",
                         lambda num, body, repo_name=None: posted.append(body)),
        ):
            sm._publish_gate_failure_analyses(issue_num=1)

        assert len(posted) == 2, (
            f"Two gate failure records must produce 2 comments; got {len(posted)}"
        )

    def test_each_comment_has_its_own_gate_name(self, tmp_path):
        """Each analysis comment contains its respective gate name."""
        records = [
            {"gate_name": "typecheck", "output": "TS2322", "timestamp": "t"},
            {"gate_name": "lint",      "output": "E302",   "timestamp": "t"},
        ]
        posted = []
        analysis = {"root_cause": "rc", "prevention": "pv"}

        with (
            patch.object(sm, "_read_gate_failure_records", return_value=records),
            patch.object(sm, "_clear_gate_failure_records", lambda *a, **kw: None),
            patch.object(sm, "_generate_gate_failure_analysis", return_value=analysis),
            patch.object(sm, "_append_gate_failure_to_sprint_log", lambda *a, **kw: None),
            patch.object(sm.github_client, "add_comment",
                         lambda num, body, repo_name=None: posted.append(body)),
        ):
            sm._publish_gate_failure_analyses(issue_num=1)

        gate_names_found = set()
        for body in posted:
            if "typecheck" in body:
                gate_names_found.add("typecheck")
            if "lint" in body:
                gate_names_found.add("lint")
        assert "typecheck" in gate_names_found and "lint" in gate_names_found, (
            f"Both gate names must appear in their own analysis comments; found: {gate_names_found}"
        )

    def test_no_publish_when_no_records(self, tmp_path):
        """No comments posted when no gate failure records exist."""
        posted = []
        with (
            patch.object(sm, "_read_gate_failure_records", return_value=[]),
            patch.object(sm, "_clear_gate_failure_records", lambda *a, **kw: None),
            patch.object(sm.github_client, "add_comment",
                         lambda num, body, repo_name=None: posted.append(body)),
        ):
            sm._publish_gate_failure_analyses(issue_num=1)
        assert not posted, "No comments when no records"


# ---------------------------------------------------------------------------
# AC-8: Machine-parseable consistent structure
# ---------------------------------------------------------------------------

class TestAC8MachineParseable:
    def test_all_three_sections_present(self, tmp_path):
        """Comment contains all three required section headings."""
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="pytest",
                error_output="FAILED test_endpoint.py::test_create",
                root_cause="Missing return type annotation",
                prevention="Run `tsc --noEmit` before submitting",
            )
        body = posted[0]
        assert "## Gate Failure Analysis" in body, "Top-level heading missing"
        assert "### Gate & Error" in body or "### Gate and Error" in body, (
            "Section 1 heading 'Gate & Error' missing"
        )
        assert "### Root Cause" in body, "Section 2 heading 'Root Cause' missing"
        assert "### Prevention" in body, "Section 3 heading 'Prevention' missing"

    def test_error_output_in_code_block(self, tmp_path):
        """Error output is wrapped in a code block for parsability."""
        error = "FAILED test_x.py::test_y"
        posted = []
        with patch.object(sm.github_client, "add_comment",
                          lambda num, body, repo_name=None: posted.append(body)):
            sm._post_gate_failure_analysis_comment(
                issue_num=1,
                gate_name="pytest",
                error_output=error,
                root_cause="rc",
                prevention="pv",
            )
        body = posted[0]
        # Error output should be inside a markdown code block
        assert "```" in body and error in body, (
            "Error output must be wrapped in a code block"
        )

    def test_gate_failure_record_sidecar_roundtrip(self, tmp_path):
        """Gate failure records written by _write and read back by _read are identical."""
        with (
            patch.object(sm, "REPO_ROOT", tmp_path),
        ):
            # Ensure runtime dir is created
            (tmp_path / ".commander" / "runtime").mkdir(parents=True, exist_ok=True)
            sm._write_gate_failure_record(
                issue_num=42,
                gate_name="lint",
                output="E501 line too long",
            )
            records = sm._read_gate_failure_records(issue_num=42, repo_root=tmp_path)
        assert len(records) == 1
        assert records[0]["gate_name"] == "lint"
        assert records[0]["output"] == "E501 line too long"


# ---------------------------------------------------------------------------
# _generate_gate_failure_analysis — unit tests
# ---------------------------------------------------------------------------

class TestGenerateGateFailureAnalysis:
    def test_returns_dict_with_required_keys(self, tmp_path):
        """Returns dict with 'root_cause' and 'prevention' keys."""
        mock_output = json.dumps({
            "root_cause": "Type annotation missing",
            "prevention": "Run tsc before submitting",
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = mock_output

        with patch.object(sm.subprocess, "run", return_value=mock_result):
            result = sm._generate_gate_failure_analysis("typecheck", "TS2322 error")

        assert "root_cause" in result
        assert "prevention" in result

    def test_returns_fallback_on_subprocess_error(self, tmp_path):
        """Returns placeholder dict when subprocess fails."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch.object(sm.subprocess, "run", return_value=mock_result):
            result = sm._generate_gate_failure_analysis("pytest", "FAILED test_x")

        assert "root_cause" in result
        assert "prevention" in result
        assert isinstance(result["root_cause"], str) and result["root_cause"]
        assert isinstance(result["prevention"], str) and result["prevention"]

    def test_returns_fallback_on_exception(self, tmp_path):
        """Returns placeholder dict when subprocess raises an exception."""
        with patch.object(sm.subprocess, "run", side_effect=FileNotFoundError("no claude")):
            result = sm._generate_gate_failure_analysis("lint", "E302 error")

        assert "root_cause" in result and result["root_cause"]
        assert "prevention" in result and result["prevention"]

    def test_parses_json_from_llm_output(self, tmp_path):
        """Correctly parses JSON from LLM output (possibly wrapped in code fences)."""
        raw_output = """Here is the analysis:
```json
{"root_cause": "Missing annotation", "prevention": "Add types"}
```"""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = raw_output

        with patch.object(sm.subprocess, "run", return_value=mock_result):
            result = sm._generate_gate_failure_analysis("typecheck", "TS2322")

        assert result["root_cause"] == "Missing annotation"
        assert result["prevention"] == "Add types"
