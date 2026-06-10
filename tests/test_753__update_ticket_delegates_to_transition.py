"""Tests for issue #753 — update_ticket.py delegates status writes to transition().

Each test is anchored to a specific acceptance criterion:

  AC1  update_ticket.py contains zero direct `gh issue edit --add-label /
       --remove-label` calls; all status writes route through transition().
  AC2  CLI --status values map correctly to TicketState:
       sit→SIT, uat→UAT, blocked→BLOCKED, in-progress→IN_PROGRESS,
       needs-rework→NEEDS_REWORK.
  AC3  transition() is called with actor="update_ticket:<caller hint>".
  AC4  Exit codes / flags / stdout format unchanged (callers unaffected).
  AC5  Any duplicate status→label map inside update_ticket.py is deleted.
  AC7  grep for STATUS_LABELS / --add-label / --remove-label in update_ticket.py
       returns no status-writing hits.
  AC8  Activity events for status changes are still emitted (via transition()).

Strategy: import update_ticket.py as a module, monkeypatch its `transition`
symbol with a recorder, and drive main() with a fake argv. No network / gh
calls are made — we assert update_ticket *delegates* rather than writes labels
itself.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "update_ticket.py"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))

from services.sprint_manager.state_machine import TicketState  # noqa: E402


def _load_update_ticket():
    """Import scripts/update_ticket.py fresh as a module."""
    spec = importlib.util.spec_from_file_location("update_ticket_753", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Recorder:
    """Stand-in for transition() that records its call args."""

    def __init__(self):
        self.calls = []

    def __call__(self, issue, target_state, *, actor, note=None, repo=None):
        self.calls.append(
            {"issue": issue, "target_state": target_state,
             "actor": actor, "note": note, "repo": repo}
        )
        return True  # labels changed


def _run(monkeypatch, status, extra_argv=None):
    """Run update_ticket.main() for a status, returning (recorder, exit_code)."""
    mod = _load_update_ticket()
    rec = _Recorder()
    monkeypatch.setattr(mod, "transition", rec)
    # Avoid auto repo resolution / gh calls.
    argv = ["update_ticket.py", "--issue", "42", "--status", status,
            "--repo", "owner/name"]
    if extra_argv:
        argv += extra_argv
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("COMMANDER_SPRINT_RUNNING", raising=False)
    code = 0
    try:
        mod.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    return rec, code


# ── AC1 / AC7: no direct gh label writes in the source ────────────────────────

def test_source_has_no_gh_add_remove_label():
    src = SCRIPT.read_text()
    assert "--add-label" not in src, (
        "update_ticket.py must contain zero `--add-label` references (AC1/AC7)"
    )
    assert "--remove-label" not in src, (
        "update_ticket.py must contain zero `--remove-label` references (AC1/AC7)"
    )


def test_source_does_not_invoke_gh_issue_edit():
    src = SCRIPT.read_text()
    assert "gh issue edit" not in src, (
        "update_ticket.py must not reference `gh issue edit` for label writes (AC1)"
    )


# ── AC5 / AC7: no duplicate status→label map ──────────────────────────────────

def test_source_has_no_status_labels_map():
    src = SCRIPT.read_text()
    assert "STATUS_LABELS" not in src, (
        "update_ticket.py must not define/use a STATUS_LABELS map — "
        "label sets live in state_machine.py only (AC5/AC7)"
    )


# ── AC2: status → TicketState mapping ─────────────────────────────────────────

@pytest.mark.parametrize("status,expected_state", [
    ("sit",          TicketState.SIT),
    ("uat",          TicketState.UAT),
    ("blocked",      TicketState.BLOCKED),
    ("in-progress",  TicketState.IN_PROGRESS),
    ("needs-rework", TicketState.NEEDS_REWORK),
])
def test_status_maps_to_correct_state(monkeypatch, status, expected_state):
    rec, code = _run(monkeypatch, status)
    assert code == 0, f"status {status!r} should exit 0"
    assert len(rec.calls) == 1, f"transition() must be called exactly once for {status!r}"
    assert rec.calls[0]["target_state"] is expected_state, (
        f"--status {status} must map to {expected_state} (AC2)"
    )


def test_blocked_routes_through_transition(monkeypatch):
    """blocked must delegate to transition() — not error out as a passthrough."""
    rec, code = _run(monkeypatch, "blocked")
    assert code == 0, "blocked must succeed via transition(), not exit non-zero"
    assert rec.calls and rec.calls[0]["target_state"] is TicketState.BLOCKED


# ── AC3: actor attribution ────────────────────────────────────────────────────

def test_actor_has_update_ticket_prefix_with_hint(monkeypatch):
    rec, _ = _run(monkeypatch, "sit")
    actor = rec.calls[0]["actor"]
    assert actor.startswith("update_ticket:"), (
        f"actor must be 'update_ticket:<caller hint>', got {actor!r} (AC3)"
    )
    hint = actor.split(":", 1)[1]
    assert hint, "actor must carry a non-empty caller hint after the colon (AC3)"


def test_actor_hint_reflects_agent_role_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_AGENT_ROLE", "tester")
    rec, _ = _run(monkeypatch, "uat")
    assert rec.calls[0]["actor"] == "update_ticket:tester", (
        "caller hint should derive from CLAUDE_AGENT_ROLE when set (AC3)"
    )


# ── AC4: flags / exit codes preserved ─────────────────────────────────────────

def test_legacy_flags_still_accepted(monkeypatch):
    """--force / --merge-sha / --target-branch must remain accepted (no-op) so
    existing agent callers do not break (AC4)."""
    rec, code = _run(monkeypatch, "uat", extra_argv=[
        "--force", "--merge-sha", "abc123", "--target-branch", "develop",
    ])
    assert code == 0, "legacy flags must be accepted without error (AC4)"
    assert rec.calls and rec.calls[0]["target_state"] is TicketState.UAT


def test_success_prints_issue_url(monkeypatch, capsys):
    """stdout still prints the issue url line (unchanged output contract, AC4)."""
    _run(monkeypatch, "sit")
    out = capsys.readouterr().out
    assert "owner/name/issues/42" in out, "issue url must still be printed (AC4)"


# ── AC8: activity events emitted via transition() ─────────────────────────────

def test_delegates_event_emission_to_transition(monkeypatch):
    """update_ticket must NOT emit its own label-change activity event — it
    delegates to transition(), which owns the ticket_transition event (AC8)."""
    rec, code = _run(monkeypatch, "sit")
    assert code == 0
    # The single side effect for the label write is the transition() call.
    assert len(rec.calls) == 1
