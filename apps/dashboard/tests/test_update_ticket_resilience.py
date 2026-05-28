"""
Tests for issue #238 — update_ticket.py retry-and-warn resilience.

Acceptance Criteria:
  AC-1: A label absent from the pre-fetched set is skipped and no subprocess
        call is made for it.
  AC-2: Three consecutive non-zero returns from --add-label trigger the warning
        comment and exit code 2.
  AC-3: Two failures followed by success on --add-label produces no warning
        comment and exit code 0.
  AC-4: Running --status uat on a repo without the 'blocked' label succeeds
        (exit 0) and output includes the skip line.

Strategy: patch subprocess.run at the update_ticket module level so that gh
calls are intercepted — no real GitHub token or network access required.
"""

import importlib
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Locate update_ticket.py so we can import it directly
SCRIPT = Path(__file__).parent.parent.parent.parent / "scripts" / "update_ticket.py"


def _load_update_ticket() -> types.ModuleType:
    """Import update_ticket as a fresh module each time (avoids cross-test state)."""
    spec = importlib.util.spec_from_file_location("update_ticket", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Stub github_client so we don't need the real one
    fake_gc = types.ModuleType("github_client")
    fake_gc.repo = lambda: "owner/repo"
    sys.modules["github_client"] = fake_gc
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cp(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """Create a mock CompletedProcess."""
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _labels_json(*names: str) -> str:
    """Build the JSON that gh issue view --json labels would return."""
    labels = [{"name": n} for n in names]
    return json.dumps({"labels": labels})


# ---------------------------------------------------------------------------
# AC-1: Label absent from pre-fetched set is skipped — no subprocess call made
# ---------------------------------------------------------------------------

class TestAC1SkipAbsentRemoveLabels:
    """A label not currently on the issue must be skipped silently."""

    def test_skipped_label_is_not_passed_to_gh(self, capsys):
        mod = _load_update_ticket()

        # Use "sit" status so we avoid the UAT safeguard which needs git state.
        # sit removes: in-progress, UAT, need-rework, blocked
        # Pre-fetched labels: only "in-progress" — others absent.
        labels_response = _make_cp(0, stdout=_labels_json("in-progress"))
        add_response = _make_cp(0)
        remove_response = _make_cp(0)

        call_log: list = []

        def fake_run(cmd, **kwargs):
            call_log.append(list(cmd))
            if "--json" in cmd:
                return labels_response
            if "--remove-label" in cmd:
                return remove_response
            return add_response

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            try:
                sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                mod.main()
            except SystemExit:
                pass

        # sit removes: in-progress, UAT, need-rework, blocked
        # Only "in-progress" is present; UAT, need-rework, blocked must be skipped
        remove_calls = [c for c in call_log if "--remove-label" in c]
        removed_labels = [c[c.index("--remove-label") + 1] for c in remove_calls]

        assert "UAT" not in removed_labels, (
            "UAT was absent from issue labels but a gh remove call was still made"
        )
        assert "need-rework" not in removed_labels, (
            "need-rework was absent but a gh remove call was still made"
        )
        assert "blocked" not in removed_labels, (
            "blocked was absent but a gh remove call was still made"
        )
        # "in-progress" IS present, so it should have been attempted
        assert "in-progress" in removed_labels, (
            "in-progress was present but was not removed"
        )

    def test_skipped_label_prints_skip_line(self, capsys):
        mod = _load_update_ticket()

        # Use "sit" status; current labels have only "in-progress"
        labels_response = _make_cp(0, stdout=_labels_json("in-progress"))
        add_response = _make_cp(0)
        remove_response = _make_cp(0)

        def fake_run(cmd, **kwargs):
            if "--json" in cmd:
                return labels_response
            if "--remove-label" in cmd:
                return remove_response
            return add_response

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            try:
                sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                mod.main()
            except SystemExit:
                pass

        captured = capsys.readouterr()
        assert 'skipped remove "UAT" (not present)' in captured.out
        assert 'skipped remove "blocked" (not present)' in captured.out


# ---------------------------------------------------------------------------
# AC-2: Three consecutive failures on --add-label → warning comment + exit 2
# ---------------------------------------------------------------------------

class TestAC2AllRetriesExhausted:
    """When all 3 retry attempts fail, a warning comment is posted and exit code is 2."""

    def test_exit_code_2_on_all_retries_failed(self):
        mod = _load_update_ticket()

        # Pre-fetched labels: empty
        labels_response = _make_cp(0, stdout=_labels_json())
        # add-label always fails
        fail_response = _make_cp(1, stderr="label not found")
        # comment posting succeeds
        comment_response = _make_cp(0)

        def fake_run(cmd, **kwargs):
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                return fail_response
            # gh issue comment
            return comment_response

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            with patch.object(mod.time, "sleep"):  # skip actual sleeps
                with pytest.raises(SystemExit) as exc_info:
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                    mod.main()

        assert exc_info.value.code == 2, (
            f"Expected exit code 2 but got {exc_info.value.code}"
        )

    def test_warning_comment_posted_on_all_retries_failed(self):
        mod = _load_update_ticket()

        labels_response = _make_cp(0, stdout=_labels_json())
        fail_response = _make_cp(1, stderr="label not found")
        comment_calls: list = []

        def fake_run(cmd, **kwargs):
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                return fail_response
            if "comment" in cmd:
                comment_calls.append(list(cmd))
                return _make_cp(0)
            return _make_cp(0)

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            with patch.object(mod.time, "sleep"):
                with pytest.raises(SystemExit):
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                    mod.main()

        assert len(comment_calls) == 1, (
            f"Expected exactly 1 warning comment call but got {len(comment_calls)}"
        )
        # Warning body should be in the comment call
        comment_cmd = comment_calls[0]
        assert "--body" in comment_cmd, "comment call must include --body"
        body_idx = comment_cmd.index("--body")
        body = comment_cmd[body_idx + 1]
        assert "sit" in body, "warning comment should mention the target status"
        assert "failed" in body.lower() or "manually" in body.lower(), (
            "warning comment should indicate manual intervention needed"
        )

    def test_add_label_retried_3_times_before_giving_up(self):
        mod = _load_update_ticket()

        labels_response = _make_cp(0, stdout=_labels_json())
        fail_response = _make_cp(1, stderr="label not found")

        add_call_count = 0

        def fake_run(cmd, **kwargs):
            nonlocal add_call_count
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                add_call_count += 1
                return fail_response
            return _make_cp(0)

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            with patch.object(mod.time, "sleep"):
                with pytest.raises(SystemExit):
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                    mod.main()

        # 4 attempts: initial + 3 retries (len(_BACKOFFS) + 1 = 4)
        assert add_call_count == 4, (
            f"Expected 4 add-label attempts (1 initial + 3 retries) but got {add_call_count}"
        )


# ---------------------------------------------------------------------------
# AC-3: Two failures then success → no warning comment, exit 0
# ---------------------------------------------------------------------------

class TestAC3SuccessAfterRetries:
    """When add-label eventually succeeds, no warning comment is posted and exit is 0."""

    def test_exit_0_when_add_succeeds_on_third_attempt(self, capsys):
        mod = _load_update_ticket()

        labels_response = _make_cp(0, stdout=_labels_json())
        fail_response = _make_cp(1, stderr="transient error")
        success_response = _make_cp(0, stdout="#42 https://github.com/owner/repo/issues/42")

        add_attempt = 0
        comment_calls: list = []

        def fake_run(cmd, **kwargs):
            nonlocal add_attempt
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                add_attempt += 1
                if add_attempt <= 2:
                    return fail_response
                return success_response
            if "comment" in cmd:
                comment_calls.append(cmd)
                return _make_cp(0)
            # gh issue close or other
            return success_response

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            with patch.object(mod.time, "sleep"):
                try:
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                    mod.main()
                    exit_code = 0
                except SystemExit as e:
                    exit_code = e.code if e.code is not None else 0

        assert exit_code == 0, f"Expected exit 0 but got {exit_code}"
        assert len(comment_calls) == 0, (
            f"Expected no warning comment but got {len(comment_calls)} comment call(s)"
        )

    def test_applied_label_line_printed_on_success(self, capsys):
        mod = _load_update_ticket()

        labels_response = _make_cp(0, stdout=_labels_json())
        fail_response = _make_cp(1, stderr="transient error")
        success_response = _make_cp(0)

        add_attempt = 0

        def fake_run(cmd, **kwargs):
            nonlocal add_attempt
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                add_attempt += 1
                if add_attempt <= 2:
                    return fail_response
                return success_response
            return success_response

        with patch.object(mod.subprocess, "run", side_effect=fake_run):
            with patch.object(mod.time, "sleep"):
                try:
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "sit"]
                    mod.main()
                except SystemExit:
                    pass

        captured = capsys.readouterr()
        assert 'applied label "SIT"' in captured.out, (
            f"Expected 'applied label \"SIT\"' in output but got: {captured.out!r}"
        )


# ---------------------------------------------------------------------------
# AC-4: --status uat on repo without 'blocked' label → exit 0 + skip line
# ---------------------------------------------------------------------------

class TestAC4UATWithoutBlockedLabel:
    """--status uat succeeds even when 'blocked' label doesn't exist on the issue."""

    def test_exit_0_when_blocked_not_present(self, tmp_path):
        """UAT status transition succeeds when blocked is not on the issue."""
        mod = _load_update_ticket()

        # Pre-fetched labels: UAT safeguard needs a merged branch;
        # we'll patch _check_uat_safeguard to bypass the git check.
        # Current labels: only "SIT" is present — blocked, in-progress, need-rework absent.
        labels_response = _make_cp(0, stdout=_labels_json("SIT"))
        add_response = _make_cp(0)
        remove_response = _make_cp(0)

        def fake_run(cmd, **kwargs):
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                return add_response
            if "--remove-label" in cmd:
                return remove_response
            return add_response

        with patch.object(mod, "_check_uat_safeguard"):  # bypass git merge check
            with patch.object(mod.subprocess, "run", side_effect=fake_run):
                try:
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "uat"]
                    mod.main()
                    exit_code = 0
                except SystemExit as e:
                    exit_code = e.code if e.code is not None else 0

        assert exit_code == 0, f"Expected exit 0 but got {exit_code}"

    def test_skip_line_printed_for_blocked(self, capsys, tmp_path):
        """Output includes 'skipped remove \"blocked\" (not present)' line."""
        mod = _load_update_ticket()

        labels_response = _make_cp(0, stdout=_labels_json("SIT"))
        add_response = _make_cp(0)
        remove_response = _make_cp(0)

        def fake_run(cmd, **kwargs):
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                return add_response
            if "--remove-label" in cmd:
                return remove_response
            return add_response

        with patch.object(mod, "_check_uat_safeguard"):
            with patch.object(mod.subprocess, "run", side_effect=fake_run):
                try:
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "uat"]
                    mod.main()
                except SystemExit:
                    pass

        captured = capsys.readouterr()
        assert 'skipped remove "blocked" (not present)' in captured.out, (
            f"Expected skip line for 'blocked' in output:\n{captured.out!r}"
        )

    def test_no_remove_call_made_for_blocked_when_absent(self, capsys, tmp_path):
        """No gh --remove-label blocked call is made when blocked is not on the issue."""
        mod = _load_update_ticket()

        labels_response = _make_cp(0, stdout=_labels_json("SIT"))
        add_response = _make_cp(0)
        remove_calls: list = []

        def fake_run(cmd, **kwargs):
            if "--json" in cmd:
                return labels_response
            if "--add-label" in cmd:
                return add_response
            if "--remove-label" in cmd:
                remove_calls.append(list(cmd))
                return _make_cp(0)
            return add_response

        with patch.object(mod, "_check_uat_safeguard"):
            with patch.object(mod.subprocess, "run", side_effect=fake_run):
                try:
                    sys.argv = ["update_ticket.py", "--issue", "42", "--status", "uat"]
                    mod.main()
                except SystemExit:
                    pass

        removed = [c[c.index("--remove-label") + 1] for c in remove_calls]
        assert "blocked" not in removed, (
            f"'blocked' was removed even though it was not on the issue: {removed}"
        )


# ---------------------------------------------------------------------------
# Meta: verify script structure
# ---------------------------------------------------------------------------

class TestScriptStructure:
    def test_script_exists(self):
        assert SCRIPT.exists(), f"update_ticket.py not found at {SCRIPT}"

    def test_script_has_fetch_current_labels(self):
        content = SCRIPT.read_text()
        assert "_fetch_current_labels" in content, (
            "Script must pre-fetch current labels from GitHub"
        )

    def test_script_has_retry_logic(self):
        content = SCRIPT.read_text()
        assert "_run_with_retry" in content or "retry" in content.lower(), (
            "Script must contain retry logic"
        )

    def test_script_has_estimated_status(self):
        content = SCRIPT.read_text()
        assert '"estimated"' in content, (
            "STATUS_MAP must include 'estimated' status"
        )

    def test_script_has_needs_rework_status(self):
        content = SCRIPT.read_text()
        assert '"needs-rework"' in content, (
            "STATUS_MAP must include 'needs-rework' status"
        )

    def test_script_exits_2_on_failure(self):
        content = SCRIPT.read_text()
        assert "sys.exit(2)" in content, (
            "Script must exit with code 2 when label operations fail after retries"
        )
