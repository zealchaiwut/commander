"""Tests for issue #2311 — scripts/retry_ticket.py.

AC coverage:
  AC1 — Resetting a failed ticket does NOT mint a child sprint label
  AC2 — Ticket order is left to the operator, never auto-reversed
  AC3 — The script swaps labels (needs-rework -> backlog) without touching
         sprint lifecycle state
  AC4 — Stale failure sidecar is cleared if present
  AC5 — The script prints /coder and /tester invocations

All tests use in-process mocking — no live HTTP calls (CLAUDE.md #1746).
"""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_module(unique_name: str, mock_gc: MagicMock):
    """Load retry_ticket.py as *unique_name* with github_client mocked.

    The mock must be in sys.modules BEFORE exec_module runs because the script
    executes ``import github_client`` at module load time.
    """
    # Patch dotenv.load_dotenv to be a no-op so we don't need a real .env
    mock_dotenv = MagicMock()
    mock_dotenv.load_dotenv = MagicMock()

    with patch.dict(sys.modules, {"github_client": mock_gc, "dotenv": mock_dotenv}):
        spec = importlib.util.spec_from_file_location(unique_name, SCRIPTS_DIR / "retry_ticket.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def _make_issue(labels: list[str]) -> dict:
    return {
        "number": 42,
        "title": "A failed ticket",
        "state": "open",
        "url": "https://github.com/zealchaiwut/commander/issues/42",
        "labels": [{"name": lbl} for lbl in labels],
    }


def _make_mock_gc(labels: list[str] | None = None) -> MagicMock:
    mock_gc = MagicMock()
    mock_gc.repo.return_value = "zealchaiwut/commander"
    mock_gc.get_issue_live.return_value = _make_issue(labels or ["needs-rework", "sprint-42"])
    return mock_gc


# ---------------------------------------------------------------------------
# AC1 — No child sprint labels are minted
# ---------------------------------------------------------------------------

def test_no_child_sprint_label_minted(tmp_path, capsys):
    """AC1: update_labels() is called; ensure_sprint_label / create_sprint_label_strict
    are never called — so no child labels (sprint-N.1, sprint-N.2) can be created."""
    mock_gc = _make_mock_gc()
    mod = _load_module("retry_ticket_ac1", mock_gc)
    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "42"]):
        mod.main()

    assert not mock_gc.ensure_sprint_label.called, (
        "ensure_sprint_label must NOT be called — it would mint a sprint label"
    )
    assert not mock_gc.create_sprint_label_strict.called, (
        "create_sprint_label_strict must NOT be called — it would mint a sprint label"
    )
    # update_labels IS called (the label swap)
    mock_gc.update_labels.assert_called_once()


# ---------------------------------------------------------------------------
# AC2 — Ticket order untouched
# ---------------------------------------------------------------------------

def test_no_reorder_or_reassignment(tmp_path, capsys):
    """AC2: the script must not re-sequence or reassign sprint numbers — it
    only touches labels on the one specified issue."""
    mock_gc = _make_mock_gc()
    mod = _load_module("retry_ticket_ac2", mock_gc)
    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "42"]):
        mod.main()

    # assign_sprint must never be called (would change sprint ordering)
    assert not mock_gc.assign_sprint.called, (
        "assign_sprint must NOT be called — it could change ticket order"
    )
    # update_labels is only called once, on issue 42
    assert mock_gc.update_labels.call_count == 1
    first_arg = mock_gc.update_labels.call_args.args[0]
    assert first_arg == 42, "update_labels must only touch the requested issue"


# ---------------------------------------------------------------------------
# AC3 — No sprint lifecycle state touched
# ---------------------------------------------------------------------------

def test_no_lifecycle_state_transition(tmp_path, capsys):
    """AC3: state_machine.transition() is never called — the script does a
    direct label swap, leaving sprint lifecycle state untouched."""
    mock_gc = _make_mock_gc()
    mock_sm = MagicMock()

    with patch.dict(sys.modules, {
        "github_client": mock_gc,
        "services.sprint_manager.state_machine": mock_sm,
    }):
        mod = _load_module("retry_ticket_ac3", mock_gc)
        mod._REPO_ROOT = tmp_path

        with patch("sys.argv", ["retry_ticket.py", "--issue", "42"]):
            mod.main()

    assert not mock_sm.transition.called, (
        "state_machine.transition() must NOT be called — it touches sprint lifecycle state"
    )


# ---------------------------------------------------------------------------
# AC4 — Stale failure sidecar cleared
# ---------------------------------------------------------------------------

def test_sidecar_cleared_if_present(tmp_path, capsys):
    """AC4: the script deletes the last-failure-<N>.json sidecar if present."""
    mock_gc = _make_mock_gc()
    mod = _load_module("retry_ticket_ac4", mock_gc)

    # Create a fake sidecar under tmp_path
    runtime_dir = tmp_path / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True)
    sidecar = runtime_dir / "last-failure-42.json"
    sidecar.write_text(json.dumps({"gate": "test-gate", "issue": 42}))
    assert sidecar.exists()

    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "42"]):
        mod.main()

    assert not sidecar.exists(), "Sidecar must be deleted by retry_ticket.py"


def test_no_sidecar_does_not_fail(tmp_path, capsys):
    """AC4: if no sidecar exists, the script still exits cleanly."""
    mock_gc = _make_mock_gc()
    mod = _load_module("retry_ticket_ac4b", mock_gc)
    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "999"]):
        mod.main()  # must not raise


# ---------------------------------------------------------------------------
# AC5 — /coder and /tester invocations are printed
# ---------------------------------------------------------------------------

def test_prints_coder_and_tester_invocations(tmp_path, capsys):
    """AC5: stdout contains the exact /coder and /tester invocations."""
    mock_gc = _make_mock_gc()
    mod = _load_module("retry_ticket_ac5", mock_gc)
    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "42"]):
        mod.main()

    out = capsys.readouterr().out
    assert "/coder work on issue 42" in out, (
        f"Expected '/coder work on issue 42' in output; got:\n{out}"
    )
    assert "/tester verify issue 42" in out, (
        f"Expected '/tester verify issue 42' in output; got:\n{out}"
    )


# ---------------------------------------------------------------------------
# dry-run mode — prints without mutating
# ---------------------------------------------------------------------------

def test_dry_run_no_mutation(tmp_path, capsys):
    """AC5/dry-run: --dry-run prints actions but does not call update_labels."""
    mock_gc = _make_mock_gc()
    mod = _load_module("retry_ticket_dry", mock_gc)
    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "42", "--dry-run"]):
        mod.main()

    assert not mock_gc.update_labels.called, (
        "update_labels must NOT be called in --dry-run mode"
    )
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()


# ---------------------------------------------------------------------------
# AC1 — label swap spec: adds backlog, removes needs-rework (no new sprint label)
# ---------------------------------------------------------------------------

def test_label_swap_spec(tmp_path, capsys):
    """AC1/AC3: update_labels receives add=['backlog'] remove=['needs-rework']."""
    mock_gc = _make_mock_gc(["needs-rework", "sprint-1026"])
    mod = _load_module("retry_ticket_swap", mock_gc)
    mod._REPO_ROOT = tmp_path

    with patch.dict(sys.modules, {"github_client": mock_gc}), \
         patch("sys.argv", ["retry_ticket.py", "--issue", "42"]):
        mod.main()

    mock_gc.update_labels.assert_called_once()
    c = mock_gc.update_labels.call_args
    # Positional args: (issue_id, add, remove) or keyword
    add_arg    = c.kwargs.get("add")    or (c.args[1] if len(c.args) > 1 else None)
    remove_arg = c.kwargs.get("remove") or (c.args[2] if len(c.args) > 2 else None)
    assert add_arg == ["backlog"],          f"Expected add=['backlog'], got {add_arg!r}"
    assert remove_arg == ["needs-rework"],  f"Expected remove=['needs-rework'], got {remove_arg!r}"
