"""Tests for issue #1943: Add configurable token/cost ceiling to sprint runs (UAT)"""
import os
import json
import pytest


BASE_URL = os.environ.get("UAT_BASE_URL", "http://localhost:8001")


@pytest.fixture
def client():
    """HTTP client pointing at UAT environment."""
    import httpx
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        yield c


# ── AC-1: CLI flag and SprintConfig field exist ──────────────────────────────

def test_token_ceiling__cli_flag_accepted(client):
    """AC-1: --token-ceiling flag is recognized by sprint_manager.py CLI."""
    # This is a code-level check; we verify via direct module import
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import argparse
    import services.sprint_manager.sprint_manager as sm

    # Verify the main() function sets up the --token-ceiling argument
    # by checking the argparse definition (AC-1 is CLI surface)
    code = open(repo_root / "services/sprint_manager/sprint_manager.py").read()
    assert "--token-ceiling" in code, "CLI must define --token-ceiling flag"
    assert "token_ceiling" in code, "CLI must reference token_ceiling in main()"


def test_token_ceiling__sprint_config_field_exists():
    """AC-1: SprintConfig.token_ceiling field exists with default=0."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    assert hasattr(cfg, "token_ceiling"), "SprintConfig must have token_ceiling field"
    assert cfg.token_ceiling == 0, "Default token_ceiling must be 0 (disabled)"


# ── AC-2: After each ticket, ceiling is compared ──────────────────────────────

def test_token_ceiling__applied_during_dispatch():
    """AC-2: _apply_token_ceiling is called after each ticket's token accounting."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    # Verify that run_sprint_loop calls _apply_token_ceiling
    code = open(repo_root / "services/sprint_manager/sprint_manager.py").read()
    assert "_apply_token_ceiling(state, token_ceiling)" in code, (
        "_apply_token_ceiling must be called with current state and ceiling"
    )


# ── AC-3: Current ticket finishes; no further tickets dispatched ─────────────

def test_token_ceiling__current_ticket_completes():
    """AC-3: When ceiling is breached, the current ticket completes before stopping."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    # _apply_token_ceiling returns True when breached, False otherwise.
    # Dispatch loop checks this and breaks AFTER the current ticket is processed.
    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 500
    st.total_tokens_out = 501  # 1001 total, ceiling = 1000

    result = sm._apply_token_ceiling(st, 1000)
    assert result is True, "Must return True when ceiling is exceeded"


def test_token_ceiling__no_dispatch_after_breach():
    """AC-3: Dispatch stops after _ceiling_stop is set (no second ticket)."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    # Verify the logic in run_sprint_loop that breaks the loop
    code = open(repo_root / "services/sprint_manager/sprint_manager.py").read()
    # The loop should check _ceiling_stop and break if true
    assert "_ceiling_stop = _apply_token_ceiling(state, token_ceiling)" in code
    assert "if _ceiling_stop:" in code


# ── AC-4: ceiling_hit boolean flag is set ────────────────────────────────────

def test_token_ceiling__ceiling_hit_flag_set_on_breach():
    """AC-4: SprintState.ceiling_hit is set to True when ceiling is exceeded."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 600
    st.total_tokens_out = 400  # 1000 total, ceiling = 1000 → breach at equal

    sm._apply_token_ceiling(st, 1000)
    assert st.ceiling_hit is True, "ceiling_hit must be set when ceiling is breached"


def test_token_ceiling__ceiling_hit_persisted_in_state(tmp_path):
    """AC-4: ceiling_hit is serialized and restored from state.json."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    from services.sprint_manager.state import SprintState

    state_path = tmp_path / "state.json"

    # Create and save state with ceiling_hit=True
    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.ceiling_hit = True
    st.save(state_path)

    # Reload and verify
    raw = json.loads(state_path.read_text())
    restored = SprintState.from_dict(raw)
    assert restored.ceiling_hit is True, "ceiling_hit must persist in saved state"


# ── AC-5: Clear log message on breach ────────────────────────────────────────

def test_token_ceiling__log_message_on_breach(capsys):
    """AC-5: A clear message is emitted when ceiling is breached."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 700
    st.total_tokens_out = 301  # 1001 total, ceiling = 1000

    sm._apply_token_ceiling(st, 1000)
    captured = capsys.readouterr()

    assert "[token-ceiling]" in captured.out, "Log must include [token-ceiling] marker"
    assert "ceiling" in captured.out.lower(), "Log must mention ceiling"
    assert ("1001" in captured.out or "1,001" in captured.out), (
        "Log must show actual tokens spent"
    )
    assert ("1000" in captured.out or "1,000" in captured.out), (
        "Log must show ceiling limit"
    )


def test_token_ceiling__log_message_single_instance(capsys):
    """AC-5: Log message appears only once, even on repeated calls."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 2000
    st.total_tokens_out = 0  # 2000 total, ceiling = 1000

    sm._apply_token_ceiling(st, 1000)
    sm._apply_token_ceiling(st, 1000)  # ceiling_hit already True
    captured = capsys.readouterr()

    count = captured.out.count("[token-ceiling]")
    assert count == 1, f"Log message should appear exactly once, appeared {count} times"


# ── AC-6: When disabled, no behavior change ──────────────────────────────────

def test_token_ceiling__disabled_by_default():
    """AC-6: token_ceiling defaults to 0 (disabled) with no behavior change."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    from services.sprint_manager.config import SprintConfig
    cfg = SprintConfig()
    assert cfg.token_ceiling == 0, "Must default to 0 (disabled)"


def test_token_ceiling__returns_false_when_disabled():
    """AC-6: _apply_token_ceiling returns False when ceiling is 0."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 1_000_000
    st.total_tokens_out = 1_000_000  # huge spend

    result = sm._apply_token_ceiling(st, 0)
    assert result is False, "Disabled ceiling must return False (no dispatch stop)"


def test_token_ceiling__ceiling_hit_false_when_disabled(capsys):
    """AC-6: ceiling_hit remains False when ceiling is disabled."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 999_999_999
    st.total_tokens_out = 999_999_999

    sm._apply_token_ceiling(st, 0)
    assert st.ceiling_hit is False, "ceiling_hit must stay False when ceiling is disabled"

    captured = capsys.readouterr()
    assert "[token-ceiling]" not in captured.out, (
        "No ceiling message should be emitted when ceiling is disabled"
    )


# ── AC-7: Unit tests for three scenarios ─────────────────────────────────────

def test_token_ceiling__unit_not_set():
    """AC-7a: When not set (0), no dispatch stopping occurs."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 100_000
    st.total_tokens_out = 100_000

    result = sm._apply_token_ceiling(st, 0)
    assert result is False
    assert st.ceiling_hit is False


def test_token_ceiling__unit_not_reached():
    """AC-7b: When set but not reached, dispatch continues normally."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 400
    st.total_tokens_out = 100  # 500 total, ceiling = 1000

    result = sm._apply_token_ceiling(st, 1000)
    assert result is False
    assert st.ceiling_hit is False


def test_token_ceiling__unit_breached():
    """AC-7c: When breached, return True and set ceiling_hit."""
    import sys
    from pathlib import Path
    repo_root = Path(__file__).parent.parent
    sys.path.insert(0, str(repo_root))

    import services.sprint_manager.sprint_manager as sm
    from services.sprint_manager.state import SprintState

    st = SprintState(sprint_label="sprint-test", sprint_number=1)
    st.total_tokens_in = 800
    st.total_tokens_out = 201  # 1001 total, ceiling = 1000

    result = sm._apply_token_ceiling(st, 1000)
    assert result is True
    assert st.ceiling_hit is True
