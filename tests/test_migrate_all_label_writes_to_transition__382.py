"""Tests for issue #382: Migrate all label writes to transition() in sprint_manager.

Verifies that sprint_manager is the sole label writer via transition(), scripts
no longer touch labels directly, and agent prompts carry the explicit no-label instruction.
"""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPRINT_MANAGER = REPO_ROOT / "services" / "sprint_manager" / "sprint_manager.py"
STATE_MACHINE  = REPO_ROOT / "services" / "sprint_manager" / "state_machine.py"
FINISH_FEATURE = REPO_ROOT / "scripts" / "finish_feature.py"
START_FEATURE  = REPO_ROOT / "scripts" / "start_feature.py"
UPDATE_TICKET  = REPO_ROOT / "scripts" / "update_ticket.py"
CODER_AGENT    = REPO_ROOT / ".claude" / "agents" / "coder.md"
TESTER_AGENT   = REPO_ROOT / ".claude" / "agents" / "tester.md"


# ── AC-1: sprint_manager.py has no direct github_client.add_label/remove_label/update_ticket calls ──

def test_sprint_manager_no_direct_add_label():
    """AC-1: sprint_manager.py contains zero github_client.add_label calls."""
    text = SPRINT_MANAGER.read_text()
    assert "github_client.add_label" not in text, (
        "sprint_manager.py must not call github_client.add_label directly"
    )


def test_sprint_manager_no_direct_remove_label():
    """AC-1: sprint_manager.py contains zero github_client.remove_label calls."""
    text = SPRINT_MANAGER.read_text()
    assert "github_client.remove_label" not in text, (
        "sprint_manager.py must not call github_client.remove_label directly"
    )


def test_sprint_manager_no_update_ticket_subprocess():
    """AC-1: sprint_manager.py does not call scripts/update_ticket.py as a subprocess."""
    text = SPRINT_MANAGER.read_text()
    # Allow comment-only references; check for actual subprocess/exec calls
    code_lines = [
        line for line in text.splitlines()
        if "update_ticket.py" in line and not line.strip().startswith("#")
    ]
    assert not code_lines, (
        "sprint_manager.py must not shell out to update_ticket.py (found non-comment references):\n"
        + "\n".join(code_lines)
    )


# ── AC-2: sprint_manager calls transition(ticket, IN_PROGRESS) before coder dispatch ──

def test_sprint_manager_transitions_in_progress():
    """AC-2: sprint_manager calls transition(..., IN_PROGRESS) before coder dispatch."""
    text = SPRINT_MANAGER.read_text()
    assert "TicketState.IN_PROGRESS" in text, (
        "sprint_manager.py must call transition() with TicketState.IN_PROGRESS"
    )
    # Confirm transition import is present
    assert "from services.sprint_manager.state_machine import" in text, (
        "sprint_manager.py must import transition from state_machine"
    )


# ── AC-3: sprint_manager calls transition(ticket, SIT) before tester dispatch ──

def test_sprint_manager_transitions_sit_before_tester():
    """AC-3: sprint_manager calls transition(ticket, SIT) before tester dispatch."""
    text = SPRINT_MANAGER.read_text()
    assert "TicketState.SIT" in text, (
        "sprint_manager.py must call transition() with TicketState.SIT"
    )
    # Confirm the SIT transition precedes tester dispatch
    sit_idx     = text.find("TicketState.SIT")
    tester_dispatch_idx = text.find("_dispatch_tester(")
    assert sit_idx < tester_dispatch_idx, (
        "transition(SIT) must appear before _dispatch_tester() in sprint_manager.py"
    )


# ── AC-4: On tester exit + merge confirmation, sprint_manager transitions to UAT ──

def test_sprint_manager_transitions_uat_after_finish_feature():
    """AC-4: sprint_manager calls transition(ticket, UAT) when FINISH_FEATURE_OUTCOME merged detected."""
    text = SPRINT_MANAGER.read_text()
    assert "TicketState.UAT" in text, (
        "sprint_manager.py must call transition() with TicketState.UAT"
    )
    assert "FINISH_FEATURE_OUTCOME merged" in text, (
        "sprint_manager.py must detect FINISH_FEATURE_OUTCOME merged before applying UAT"
    )
    # Both must appear together in _call_finish_feature context
    uat_idx      = text.find("TicketState.UAT")
    outcome_idx  = text.find("FINISH_FEATURE_OUTCOME merged")
    assert outcome_idx < uat_idx or abs(outcome_idx - uat_idx) < 500, (
        "FINISH_FEATURE_OUTCOME check and UAT transition should be near each other"
    )


# ── AC-5: On logic failure, sprint_manager calls transition(ticket, NEEDS_REWORK) ──

def test_sprint_manager_transitions_needs_rework_on_failure():
    """AC-5: sprint_manager calls transition(ticket, NEEDS_REWORK) for logic failures."""
    text = SPRINT_MANAGER.read_text()
    assert "TicketState.NEEDS_REWORK" in text, (
        "sprint_manager.py must call transition() with TicketState.NEEDS_REWORK"
    )
    # Confirm it is called inside a function linked to failure categories
    assert "_apply_needs_rework_label" in text, (
        "sprint_manager.py must define _apply_needs_rework_label that calls transition(NEEDS_REWORK)"
    )
    assert "_LOGIC_FAILURE_CATEGORIES" in text, (
        "sprint_manager.py must define _LOGIC_FAILURE_CATEGORIES to gate NEEDS_REWORK transitions"
    )


# ── AC-6: finish_feature.py does not touch GitHub labels ──

def test_finish_feature_no_label_calls():
    """AC-6: finish_feature.py does not apply or remove any GitHub labels."""
    text = FINISH_FEATURE.read_text()
    forbidden = [
        "add_label", "remove_label", "update_labels",
        "--add-label", "--remove-label",
        "update_ticket",
    ]
    for pattern in forbidden:
        assert pattern not in text, (
            f"finish_feature.py must not reference '{pattern}' (label mutations belong in sprint_manager)"
        )


def test_finish_feature_outcome_line():
    """AC-6: finish_feature.py writes FINISH_FEATURE_OUTCOME merged sha=<sha> branch=<branch> on success."""
    text = FINISH_FEATURE.read_text()
    assert "FINISH_FEATURE_OUTCOME merged sha=" in text, (
        "finish_feature.py must print 'FINISH_FEATURE_OUTCOME merged sha=<sha> branch=<branch>' on success"
    )


def test_finish_feature_exits_nonzero_with_stderr_on_failure():
    """AC-6: finish_feature.py exits non-zero and writes to stderr on merge failure."""
    text = FINISH_FEATURE.read_text()
    # Merge conflict path should print to stderr and sys.exit(1)
    assert "sys.exit(1)" in text or "sys.exit(" in text, (
        "finish_feature.py must exit non-zero on failure"
    )
    assert "file=sys.stderr" in text, (
        "finish_feature.py must write structured error to stderr on failure"
    )


# ── AC-7: start_feature.py does not touch GitHub labels ──

def test_start_feature_no_label_calls():
    """AC-7: start_feature.py does not apply or remove any GitHub labels."""
    text = START_FEATURE.read_text()
    forbidden = [
        "add_label", "remove_label", "update_labels",
        "--add-label", "--remove-label",
        "update_ticket",
    ]
    for pattern in forbidden:
        assert pattern not in text, (
            f"start_feature.py must not reference '{pattern}' — branch creation only"
        )


# ── AC-8: Coder agent prompt has explicit no-label instruction ──

def test_coder_agent_has_do_not_modify_label_instruction():
    """AC-8: coder.md contains explicit 'DO NOT modify any GitHub label' instruction."""
    text = CODER_AGENT.read_text()
    assert "DO NOT modify any GitHub label" in text, (
        "coder.md must contain the explicit instruction: "
        "'DO NOT modify any GitHub label on this issue or any other issue. "
        "Label transitions are managed by sprint_manager.'"
    )


def test_coder_agent_label_transitions_managed_by_sprint_manager():
    """AC-8: coder.md states 'Label transitions are managed by sprint_manager'."""
    text = CODER_AGENT.read_text()
    assert "Label transitions are managed by sprint_manager" in text, (
        "coder.md must explicitly state that label transitions are managed by sprint_manager"
    )


def test_coder_agent_no_sit_label_instruction():
    """AC-8: coder.md must not ask the coder to apply the SIT label via update_ticket.py."""
    text = CODER_AGENT.read_text()
    # The coder should no longer call update_ticket.py for SIT
    assert "update_ticket.py --issue" not in text or "--status sit" not in text, (
        "coder.md must not instruct the coder to run 'update_ticket.py --status sit'; "
        "SIT transition belongs to sprint_manager"
    )


# ── AC-9: Tester agent prompt has explicit no-label instruction ──

def test_tester_agent_has_do_not_modify_label_instruction():
    """AC-9: tester.md contains explicit 'DO NOT modify any GitHub label' instruction."""
    text = TESTER_AGENT.read_text()
    assert "DO NOT modify any GitHub label" in text, (
        "tester.md must contain the explicit instruction: "
        "'DO NOT modify any GitHub label on this issue or any other issue. "
        "Label transitions are managed by sprint_manager.'"
    )


def test_tester_agent_finish_feature_no_longer_applies_uat():
    """AC-9: tester.md must not claim finish_feature.py applies the UAT label (sprint_manager does now)."""
    text = TESTER_AGENT.read_text()
    assert "finish_feature.py applies the **UAT** label" not in text, (
        "tester.md must be updated to reflect that sprint_manager (not finish_feature.py) applies the UAT label"
    )


# ── AC-10: update_ticket.py is a thin CLI wrapper delegating to transition() ──

def test_update_ticket_delegates_status_to_transition():
    """AC-10: update_ticket.py maps --status to TicketState and calls transition()."""
    text = UPDATE_TICKET.read_text()
    assert "transition(" in text, (
        "update_ticket.py must delegate status changes to transition()"
    )
    assert "TicketState" in text, (
        "update_ticket.py must map --status values to TicketState enum"
    )


def test_update_ticket_no_uat_safeguard_code():
    """AC-10: internal UAT safeguard code path is removed from update_ticket.py."""
    text = UPDATE_TICKET.read_text()
    # The old safeguard was large blocks checking sprint running state / locking
    assert "sprint_running" not in text.lower(), (
        "update_ticket.py must not contain the old UAT safeguard sprint_running check"
    )
    assert "safeguard" not in text.lower(), (
        "update_ticket.py must not contain UAT safeguard logic"
    )


def test_update_ticket_is_thin_wrapper():
    """AC-10: update_ticket.py is significantly shorter than the old bloated version."""
    text = UPDATE_TICKET.read_text()
    line_count = len(text.splitlines())
    # Old version was 300+ lines; thin wrapper should be under 200 lines
    assert line_count < 200, (
        f"update_ticket.py has {line_count} lines; expected a thin wrapper under 200 lines "
        f"(old version was 300+)"
    )


# ── AC-11: gh issue edit --add-label / --remove-label only in state_machine.py ──

def test_no_gh_label_edit_outside_state_machine():
    """AC-11: 'gh issue edit --add-label|--remove-label' matches zero files outside state_machine.py."""
    result = subprocess.run(
        ["grep", "-r",
         r"gh issue edit --add-label\|gh issue edit --remove-label",
         str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    this_file = Path(__file__).name
    matches = [
        line for line in result.stdout.splitlines()
        if STATE_MACHINE.name not in line
        and ".git" not in line
        and ".pyc" not in line
        and this_file not in line  # exclude the test file itself
    ]
    assert not matches, (
        "Found 'gh issue edit --add-label|--remove-label' outside state_machine.py:\n"
        + "\n".join(matches)
    )
