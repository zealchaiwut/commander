"""Tests for issue #894 — Remove the unreachable exhausted-retries raise in _run_step.

Follow-up NIT from the sprint-65 review of #857 (see #889). The
``raise SprintCreationError(step, 'exhausted retries', 502)`` after the ``for``
loop in ``services/sprint_manager/sprint_creation.py:_run_step`` is unreachable —
the loop always returns or raises — so it is dead code. This ticket removes it.

Acceptance criteria derived from the issue:

  AC1 — The dead, unreachable ``exhausted retries`` raise is removed from
        ``_run_step`` (the source no longer contains it).
  AC2 — Behaviour is preserved: a transient failure that fails on every attempt
        (including the single retry) still raises ``SprintCreationError`` named
        for the step, surfacing the real classified reason — NOT the removed
        ``exhausted retries`` guard text.
  AC3 — Behaviour is preserved: a clean, verified run still returns ``None``.
"""
from __future__ import annotations

import inspect
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
    SprintCreationError,
    _run_step,
)

# Reuse the established fake-deps recorder from the #857 suite.
from test_857__verify_sprint_creation import FakeDeps, _Err


# ---------------------------------------------------------------------------
# AC1 — the unreachable raise is gone from the source
# ---------------------------------------------------------------------------
def test_ac1_exhausted_retries_raise_removed():
    src = inspect.getsource(_run_step)
    assert "exhausted retries" not in src, (
        "_run_step still contains the dead 'exhausted retries' raise")


def test_ac1_no_exhausted_retries_anywhere_in_module():
    module_src = Path(sprint_creation.__file__).read_text()
    assert "exhausted retries" not in module_src


# ---------------------------------------------------------------------------
# AC2 — exhausting the retry still raises the real classified error,
#       not the removed guard
# ---------------------------------------------------------------------------
def test_ac2_transient_failure_on_every_attempt_still_raises_real_reason():
    # fail on the original attempt AND the single retry (fail_times high).
    deps = FakeDeps(fail_step="label", fail_times=999, transient=True)

    def action():
        deps.create_label()

    with pytest.raises(SprintCreationError) as ei:
        _run_step(deps, "create GitHub label", action, deps.label_exists,
                  retry=True)

    # The error must name the step and carry the classified reason, never the
    # deleted "exhausted retries" guard text.
    assert ei.value.step == "create GitHub label"
    assert ei.value.reason == "network exploded"
    assert "exhausted retries" not in ei.value.reason
    # Action ran the original attempt + exactly one retry.
    assert deps.calls.count("create_label") == 2


def test_ac2_non_retry_step_failure_still_raises():
    deps = FakeDeps(fail_step="plan", fail_times=999)

    def action():
        deps.write_plan()

    with pytest.raises(SprintCreationError) as ei:
        _run_step(deps, "write sprint plan file", action, deps.plan_written,
                  retry=False)
    assert ei.value.step == "write sprint plan file"
    assert "exhausted retries" not in ei.value.reason


# ---------------------------------------------------------------------------
# AC3 — a clean, verified run still returns None
# ---------------------------------------------------------------------------
def test_ac3_clean_verified_run_returns_none():
    deps = FakeDeps()

    def action():
        deps.create_label()

    assert _run_step(deps, "create GitHub label", action, deps.label_exists,
                     retry=True) is None
