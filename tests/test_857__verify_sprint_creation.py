"""Tests for issue #857 — Verify New Sprint creation and surface failures loudly.

Each test maps to an acceptance criterion of the ticket:

  AC1 — Sprint creation executes each step in order: (1) create GitHub label,
        (2) apply label to selected tickets, (3) write plan file.
  AC2 — Each step is verified after execution (verification, not assumed success).
  AC3 — On a transient failure (timeout / 429) the failed step is retried
        exactly once before aborting.
  AC4 — On a non-recoverable failure, earlier steps are rolled back (label
        deleted if created, labels removed from tickets if applied).
  AC5 — A loud, step-named error is surfaced (the dialog shows a non-dismissible
        message naming the failed step and the reason).
  AC6 — A successful creation results in all three artifacts present.
  AC7 — No silent no-ops: a failed attempt never returns an idle/success state.

The orchestration (services/sprint_manager/sprint_creation.py) is pure and
dependency-injected, so AC1-AC7 are exercised with a fake `deps` recorder.
A second layer exercises the real `SprintCreateDeps` adapter and the router
service translation with a fake github_client and a real temp filesystem.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_DASHBOARD_ROOT = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.sprint_manager import sprint_creation
from services.sprint_manager.sprint_creation import (
    SprintCreateDeps,
    SprintCreationError,
    create_sprint_verified,
)

_PROJECT_HTML = _DASHBOARD_ROOT / "static" / "project.html"


# ---------------------------------------------------------------------------
# Fake deps recorder for the pure orchestration tests
# ---------------------------------------------------------------------------
class _Err(RuntimeError):
    def __init__(self, reason="network exploded", transient=False, rate_limit=False):
        super().__init__(reason)
        self.reason = reason
        self.transient = transient
        self.rate_limit = rate_limit


class FakeDeps:
    """Records the ordered method calls and simulates step failures."""

    def __init__(self, tickets=None, fail_step=None, fail_times=999,
                 transient=False, rate_limit=False, verify_ok=True):
        self.tickets = list(tickets or [])
        self.fail_step = fail_step
        self.fail_times = fail_times
        self.transient = transient
        self.rate_limit = rate_limit
        self.verify_ok = verify_ok
        self._fails = 0
        self.calls: list[str] = []
        self.label_present = False
        self.tickets_present = False
        self.plan_present = False
        self.label_rolled_back = False
        self.tickets_rolled_back = False

    def _maybe_fail(self, step):
        if self.fail_step == step and self._fails < self.fail_times:
            self._fails += 1
            raise _Err(
                reason="Rate limit exceeded — try again in 30 s" if self.rate_limit else "network exploded",
                transient=self.transient,
                rate_limit=self.rate_limit,
            )

    def create_label(self):
        self.calls.append("create_label")
        self._maybe_fail("label")
        self.label_present = True

    def label_exists(self):
        self.calls.append("label_exists")
        return self.label_present and self.verify_ok

    def apply_ticket_labels(self):
        self.calls.append("apply_ticket_labels")
        self._maybe_fail("tickets")
        self.tickets_present = True

    def ticket_labels_applied(self):
        self.calls.append("ticket_labels_applied")
        return self.tickets_present

    def remove_ticket_labels(self):
        self.calls.append("remove_ticket_labels")
        self.tickets_present = False
        self.tickets_rolled_back = True

    def delete_label(self):
        self.calls.append("delete_label")
        self.label_present = False
        self.label_rolled_back = True

    def write_plan(self):
        self.calls.append("write_plan")
        self._maybe_fail("plan")
        self.plan_present = True

    def plan_written(self):
        self.calls.append("plan_written")
        return self.plan_present

    def is_transient(self, exc):
        return getattr(exc, "transient", False)

    def describe_error(self, exc):
        return getattr(exc, "reason", str(exc))

    def status_for(self, exc):
        return 429 if getattr(exc, "rate_limit", False) else 502


# ---------------------------------------------------------------------------
# AC1 — steps execute in order
# ---------------------------------------------------------------------------
def test_ac1_steps_execute_in_order():
    deps = FakeDeps(tickets=[11, 22])
    create_sprint_verified(deps)
    # The three mutating actions must run in this order.
    mutations = [c for c in deps.calls if c in
                 ("create_label", "apply_ticket_labels", "write_plan")]
    assert mutations == ["create_label", "apply_ticket_labels", "write_plan"]


def test_ac1_no_tickets_skips_apply_step():
    deps = FakeDeps(tickets=[])
    create_sprint_verified(deps)
    assert "apply_ticket_labels" not in deps.calls
    assert "create_label" in deps.calls and "write_plan" in deps.calls


# ---------------------------------------------------------------------------
# AC2 — each step is verified after execution
# ---------------------------------------------------------------------------
def test_ac2_label_step_verified_not_assumed():
    # create_label "succeeds" (no raise) but verification reports the label
    # absent -> the step must be treated as failed, not silently accepted.
    deps = FakeDeps(verify_ok=False)
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert "label_exists" in deps.calls  # verification actually ran
    assert "create GitHub label" in ei.value.step
    assert "verif" in ei.value.reason.lower()


def test_ac2_every_step_has_a_verify_call():
    deps = FakeDeps(tickets=[7])
    create_sprint_verified(deps)
    for verify in ("label_exists", "ticket_labels_applied", "plan_written"):
        assert verify in deps.calls


# ---------------------------------------------------------------------------
# AC3 — transient failure retried exactly once
# ---------------------------------------------------------------------------
def test_ac3_transient_label_failure_retried_once_then_succeeds():
    deps = FakeDeps(fail_step="label", fail_times=1, transient=True)
    create_sprint_verified(deps)  # should complete after the single retry
    assert deps.calls.count("create_label") == 2  # original + exactly one retry


def test_ac3_transient_failure_on_both_attempts_aborts_after_one_retry():
    deps = FakeDeps(fail_step="label", fail_times=2, transient=True)
    with pytest.raises(SprintCreationError):
        create_sprint_verified(deps)
    assert deps.calls.count("create_label") == 2  # exactly one retry, not more


def test_ac3_non_transient_failure_is_not_retried():
    deps = FakeDeps(fail_step="label", fail_times=1, transient=False)
    with pytest.raises(SprintCreationError):
        create_sprint_verified(deps)
    assert deps.calls.count("create_label") == 1  # no retry for non-transient


def test_ac3_rate_limit_carries_429_status():
    deps = FakeDeps(fail_step="label", fail_times=2, transient=True, rate_limit=True)
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert ei.value.status_code == 429


# ---------------------------------------------------------------------------
# AC4 — rollback of earlier steps on non-recoverable failure
# ---------------------------------------------------------------------------
def test_ac4_failure_applying_tickets_rolls_back_label():
    deps = FakeDeps(tickets=[1, 2], fail_step="tickets")
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert ei.value.step == "apply sprint label to tickets"
    assert deps.label_rolled_back is True       # label deleted
    assert "write_plan" not in deps.calls        # step 3 never ran


def test_ac4_failure_writing_plan_rolls_back_label_and_tickets():
    deps = FakeDeps(tickets=[1, 2], fail_step="plan")
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert ei.value.step == "write sprint plan file"
    assert deps.tickets_rolled_back is True      # ticket labels removed
    assert deps.label_rolled_back is True        # label deleted


def test_ac4_label_step_failure_needs_no_ticket_rollback():
    deps = FakeDeps(tickets=[1], fail_step="label", fail_times=1, transient=False)
    with pytest.raises(SprintCreationError):
        create_sprint_verified(deps)
    assert deps.tickets_rolled_back is False
    assert "apply_ticket_labels" not in deps.calls


# ---------------------------------------------------------------------------
# AC5 — loud error naming the failed step and the reason
# ---------------------------------------------------------------------------
def test_ac5_error_names_step_and_reason():
    deps = FakeDeps(fail_step="label", fail_times=1, transient=False, rate_limit=False)
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    err = ei.value
    assert "Failed to create GitHub label" in err.message
    assert err.reason in err.message
    assert err.reason  # non-empty reason


def test_ac5_rate_limit_reason_is_human_readable():
    deps = FakeDeps(fail_step="label", fail_times=2, transient=True, rate_limit=True)
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert "Rate limit" in ei.value.reason


# ---------------------------------------------------------------------------
# AC6 — successful creation produces all three artifacts
# ---------------------------------------------------------------------------
def test_ac6_success_produces_all_three_artifacts():
    deps = FakeDeps(tickets=[5])
    create_sprint_verified(deps)  # no exception
    assert deps.label_present is True
    assert deps.tickets_present is True
    assert deps.plan_present is True
    assert deps.label_rolled_back is False
    assert deps.tickets_rolled_back is False


# ---------------------------------------------------------------------------
# AC7 — no silent no-ops: a failed step always raises (never returns success)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("step", ["label", "tickets", "plan"])
def test_ac7_any_step_failure_raises(step):
    deps = FakeDeps(tickets=[1], fail_step=step, fail_times=1, transient=False)
    with pytest.raises(SprintCreationError):
        create_sprint_verified(deps)


# ===========================================================================
# Real adapter (SprintCreateDeps) — exercises gh-error classification with a
# fake github_client, so the retry/rollback wiring is verified end-to-end.
# ===========================================================================
class FakeGH:
    def __init__(self, create_raises=None, exists=True):
        self.create_raises = create_raises  # exception to raise from create
        self._create_calls = 0
        self.exists = exists
        self.label_created = False
        self.deleted = []
        self.applied = {}   # issue -> set(labels)
        self.removed = []

    def create_sprint_label_strict(self, sprint_num, repo_name=None):
        self._create_calls += 1
        if self.create_raises is not None:
            raise self.create_raises
        self.label_created = True

    def sprint_label_exists(self, sprint_num, repo_name=None):
        return self.label_created and self.exists

    def get_issue(self, issue_number, repo_name=None):
        return {"labels": [{"name": n} for n in self.applied.get(issue_number, set())]}

    def get_issue_live(self, issue_number, repo_name=None):
        return self.get_issue(issue_number, repo_name)

    def update_labels(self, issue_id, add, remove, repo_name=None):
        s = self.applied.setdefault(issue_id, set())
        for a in add:
            s.add(a)
        for r in remove:
            s.discard(r)
            self.removed.append((issue_id, r))

    def delete_label(self, name, repo_name=None):
        self.deleted.append(name)
        self.label_created = False


def _rate_limit_error():
    return subprocess.CalledProcessError(
        1, ["gh", "label", "create"],
        stderr="HTTP 403: API rate limit exceeded for user (rate limit)",
    )


def test_adapter_rate_limit_is_transient_and_classified_429():
    gh = FakeGH(create_raises=_rate_limit_error())
    deps = SprintCreateDeps(
        github_client=gh, repo="o/r", sprint_num=65, tickets=[],
        write_plan_fn=lambda: None, plan_written_fn=lambda: True,
    )
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert ei.value.status_code == 429                # rate-limit -> 429
    assert gh._create_calls == 2                       # retried exactly once
    assert "create GitHub label" in ei.value.step


def test_adapter_rollback_deletes_label_and_removes_ticket_labels(tmp_path):
    # Step 1 + 2 succeed, step 3 (plan write) fails -> full rollback.
    gh = FakeGH()

    def _boom():
        raise OSError("disk full")

    deps = SprintCreateDeps(
        github_client=gh, repo="o/r", sprint_num=65, tickets=[101, 102],
        write_plan_fn=_boom, plan_written_fn=lambda: False,
    )
    with pytest.raises(SprintCreationError) as ei:
        create_sprint_verified(deps)
    assert ei.value.step == "write sprint plan file"
    assert "sprint-65" in gh.deleted                   # label rolled back
    assert (101, "sprint-65") in gh.removed            # ticket labels removed
    assert (102, "sprint-65") in gh.removed


def test_adapter_happy_path_creates_all_artifacts(tmp_path):
    gh = FakeGH()
    plan_path = tmp_path / "sprint-65.json"

    def _write():
        plan_path.write_text("{}", encoding="utf-8")

    deps = SprintCreateDeps(
        github_client=gh, repo="o/r", sprint_num=65, tickets=[101],
        write_plan_fn=_write, plan_written_fn=plan_path.exists,
    )
    create_sprint_verified(deps)
    assert gh.label_created is True
    assert "sprint-65" in gh.applied.get(101, set())
    assert plan_path.exists()
    assert gh.deleted == []                            # nothing rolled back


# ===========================================================================
# AC5 + AC7 — frontend: the New Sprint dialog surfaces the failure loudly and
# never silently closes / returns to success after a failed attempt.
# ===========================================================================
@pytest.fixture(scope="module")
def project_html() -> str:
    assert _PROJECT_HTML.exists(), "project.html must exist"
    return _PROJECT_HTML.read_text(encoding="utf-8")


def _ns_submit_handler(html: str) -> str:
    start = html.find("getElementById('ns-submit-btn').addEventListener")
    assert start >= 0, "New Sprint submit handler not found"
    end = html.find("New Ticket modal wiring", start)
    assert end > start
    return html[start:end]


def test_ac5_error_element_is_announced(project_html):
    # The error banner must be an assertive live region (non-dismissible, read
    # out by assistive tech) so the failure is surfaced loudly.
    idx = project_html.find('id="ns-error"')
    assert idx >= 0
    div = project_html[project_html.rfind("<div", 0, idx):idx + 60]
    assert 'role="alert"' in div


def test_ac5_submit_handler_shows_backend_detail_verbatim(project_html):
    handler = _ns_submit_handler(project_html)
    # The named-step reason from the backend (data.detail) must be surfaced
    # to the user, not replaced by a generic message.
    assert "data.detail" in handler
    assert "_nsShowError" in handler


def test_ac7_failed_attempt_does_not_close_modal_or_show_success(project_html):
    handler = _ns_submit_handler(project_html)
    # Target the error handler specifically (not the inline .json().catch()).
    catch_idx = handler.find("} catch (err) {")
    assert catch_idx >= 0
    catch_block = handler[catch_idx:]
    # In the failure path the modal must NOT be hidden (no silent success) and
    # the submit button must be re-enabled so the user can retry.
    assert "add('hidden')" not in catch_block, "modal must stay open on failure"
    assert "disabled = false" in catch_block
