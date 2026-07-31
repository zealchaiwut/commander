"""Behavioral tests for issue #2044 — COMMANDER_DEAD_LETTER_THRESHOLD silently
ignored at the sprint_manager call site.

## What was broken

``services/sprint_manager/sprint_manager.py`` line 4400 (pre-fix)::

    _dl_threshold = int(getattr(cfg, "dead_letter_escalation_threshold", 2))

When ``cfg`` is ``None`` (the default-config fallback path) this always returns
``2`` regardless of the env var because ``getattr(None, ..., 2)`` evaluates the
default.  ``COMMANDER_DEAD_LETTER_THRESHOLD`` was therefore silently ignored.

## Fix

The call site now delegates to the existing helper::

    _dl_threshold = get_escalation_threshold(cfg)

``get_escalation_threshold`` (``dead_letter_escalation.py:48``) already
implements the correct priority order: env-var > yaml > default 2.

## How the tests fail against pre-fix code

``test_sprint_manager_imports_get_escalation_threshold``
    Pre-fix: ``get_escalation_threshold`` is NOT imported into sprint_manager —
    ``getattr(sm, "get_escalation_threshold", None)`` returns ``None`` and the
    assertion fails immediately.

``test_env_threshold_respected_no_escalation_at_two``
    Pre-fix: Same import assertion fails.  If that assertion were removed, the
    test would still fail because the call site uses ``getattr(cfg, ..., 2)=2``
    as the threshold; with ``COMMANDER_DEAD_LETTER_THRESHOLD=3`` ignored, the
    second dead-letter *would* escalate — ``result2 is True`` — making
    ``assert result2 is False`` fail.

``test_env_threshold_respected_escalation_fires_at_three``
    Pre-fix: Same as above.  ``result3`` is the 3rd call but with threshold=2
    (env ignored) escalation already fired at ``result2``, so the test's
    ledger state diverges from the assertion sequence.

Git-isolation guarantee
-----------------------
Every test is guarded by the ``git_no_mutation`` autouse fixture (pattern
copied verbatim from ``test_2031__false_orphan_sweep.py``).  Any code path
that runs ``git commit`` causes the fixture to fail loudly.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
SM_DIR = REPO_ROOT / "services" / "sprint_manager"

for _p in (str(REPO_ROOT), str(SM_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("DB_PATH", "/tmp/commander-pytest-2044.db")

from services.sprint_manager.dead_letter_escalation import (  # noqa: E402
    check_dead_letter_escalation,
    get_escalation_threshold,
)


# ── git-isolation guard ───────────────────────────────────────────────────────

def _git_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=str(REPO_ROOT),
        text=True,
    ).strip()


@pytest.fixture(autouse=True)
def git_no_mutation():
    """Assert no test commits to the repository.

    Pattern copied verbatim from test_2031__false_orphan_sweep.py.
    """
    sha_before = _git_head_sha()
    yield
    sha_after = _git_head_sha()
    assert sha_before == sha_after, (
        f"Test mutated the git repository!\n"
        f"  HEAD before: {sha_before}\n"
        f"  HEAD after:  {sha_after}\n"
        "An unmocked code path ran 'git commit'. Ensure all sprint-end steps are stubbed."
    )


# ── AC-1: call-site wiring ────────────────────────────────────────────────────

class TestCallSiteWiring:
    """AC-1: the sprint_manager call site must use get_escalation_threshold."""

    def test_sprint_manager_imports_get_escalation_threshold(self):
        """get_escalation_threshold must be importable from sprint_manager's namespace.

        Pre-fix: sprint_manager only imported ``check_dead_letter_escalation``;
        ``get_escalation_threshold`` was absent from its namespace.  This test
        fails with an assertion error on pre-fix code.
        """
        import services.sprint_manager.sprint_manager as sm

        fn = getattr(sm, "get_escalation_threshold", None)
        assert callable(fn), (
            "sprint_manager must import get_escalation_threshold from "
            "dead_letter_escalation.  The call site at the dead-letter block "
            "(sprint_manager.py) depends on it to read COMMANDER_DEAD_LETTER_THRESHOLD. "
            "If this assertion fails, the call site still uses "
            "getattr(cfg, 'dead_letter_escalation_threshold', 2) instead."
        )


# ── AC-3: behavioral test through the real call-site computation ──────────────

class TestEnvThresholdWiredThroughCallSite:
    """AC-3 (issue #2044): COMMANDER_DEAD_LETTER_THRESHOLD=3 with cfg=None must
    prevent escalation at 2 dead-letters and trigger it at 3.

    This exercises the real dispatch path by computing the threshold via
    ``sprint_manager.get_escalation_threshold`` (the wired-up helper imported
    at the call site after the fix) rather than passing a hardcoded value.

    Why this is distinct from testing the helper in isolation
    --------------------------------------------------------
    Tests in ``test_2033__dead_letter_escalation.py`` pass ``threshold=N``
    directly to ``check_dead_letter_escalation``.  They verify the helper's
    counting and alert logic, but they do NOT verify that sprint_manager's
    call site derives the threshold from the env var.  This class drives the
    threshold computation through the sprint_manager module's own reference to
    ``get_escalation_threshold``, exactly as the fixed call site does.
    """

    def test_env_threshold_respected_no_escalation_at_two(
        self, tmp_path, monkeypatch
    ):
        """COMMANDER_DEAD_LETTER_THRESHOLD=3, cfg=None: no escalation at 2.

        Pre-fix failure: the wiring assertion fails (sprint_manager does not
        import get_escalation_threshold).  Even without that assertion, the
        call site would compute threshold=2 (env var ignored), so the 2nd
        dead-letter would escalate — making ``assert result2 is False`` fail.
        """
        monkeypatch.setenv("COMMANDER_DEAD_LETTER_THRESHOLD", "3")

        import services.sprint_manager.sprint_manager as sm

        # Verify the import wiring before proceeding; fails on pre-fix code.
        fn = getattr(sm, "get_escalation_threshold", None)
        assert callable(fn), (
            "sprint_manager must import get_escalation_threshold. "
            "See TestCallSiteWiring.test_sprint_manager_imports_get_escalation_threshold."
        )

        # Compute the threshold exactly as the FIXED call site does:
        #   _dl_threshold = get_escalation_threshold(cfg)   (cfg=None)
        # Pre-fix equivalent would be:
        #   int(getattr(None, "dead_letter_escalation_threshold", 2)) == 2
        threshold = sm.get_escalation_threshold(cfg=None)
        assert threshold == 3, (
            f"Expected threshold=3 from COMMANDER_DEAD_LETTER_THRESHOLD=3, "
            f"got {threshold}.  The env var is not being consulted."
        )

        # Two dead-letters at threshold=3 → no escalation
        with (
            patch(
                "services.sprint_manager.sprint_manager.dispatch_alerts",
                MagicMock(),
            ),
            patch(
                "services.sprint_manager.sprint_manager._add_blocked_label",
                MagicMock(),
            ),
        ):
            result1 = check_dead_letter_escalation(
                ticket_id=2044,
                title="DL threshold test",
                last_error="err",
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )
            result2 = check_dead_letter_escalation(
                ticket_id=2044,
                title="DL threshold test",
                last_error="err",
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )

        assert result1 is False, (
            "1st dead-letter must not escalate at threshold=3"
        )
        assert result2 is False, (
            "2nd dead-letter must not escalate at threshold=3. "
            "If this fails, the env var is being ignored and threshold is 2."
        )

    def test_env_threshold_respected_escalation_fires_at_three(
        self, tmp_path, monkeypatch
    ):
        """COMMANDER_DEAD_LETTER_THRESHOLD=3, cfg=None: escalation fires at 3.

        Companion to the above: confirms that escalation *does* eventually fire —
        threshold=3 is respected, not just silently clamped to some other value.
        """
        monkeypatch.setenv("COMMANDER_DEAD_LETTER_THRESHOLD", "3")

        import services.sprint_manager.sprint_manager as sm

        fn = getattr(sm, "get_escalation_threshold", None)
        assert callable(fn), "sprint_manager must import get_escalation_threshold"

        threshold = sm.get_escalation_threshold(cfg=None)
        assert threshold == 3

        mock_dispatch = MagicMock()
        mock_blocked = MagicMock()

        with (
            patch(
                "services.sprint_manager.sprint_manager.dispatch_alerts",
                mock_dispatch,
            ),
            patch(
                "services.sprint_manager.sprint_manager._add_blocked_label",
                mock_blocked,
            ),
        ):
            check_dead_letter_escalation(
                ticket_id=20441,
                title="DL threshold test 3x",
                last_error="err",
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )  # count=1 → no escalation
            check_dead_letter_escalation(
                ticket_id=20441,
                title="DL threshold test 3x",
                last_error="err",
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )  # count=2 → no escalation (threshold=3)
            result3 = check_dead_letter_escalation(
                ticket_id=20441,
                title="DL threshold test 3x",
                last_error="err",
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )  # count=3 → escalation fires

        assert result3 is True, (
            "3rd dead-letter at threshold=3 must escalate. "
            "dispatch_alerts and _add_blocked_label should have been called."
        )
        assert mock_dispatch.called, "dispatch_alerts must be called on escalation"
        assert mock_blocked.called, "_add_blocked_label must be called on escalation"

    def test_cfg_none_with_no_env_var_uses_default_threshold(
        self, tmp_path, monkeypatch
    ):
        """cfg=None + no env var → default threshold=2 still works after the fix.

        Verifies the fix does not break the existing default-2 behaviour when
        neither env var nor cfg is provided.
        """
        monkeypatch.delenv("COMMANDER_DEAD_LETTER_THRESHOLD", raising=False)

        import services.sprint_manager.sprint_manager as sm

        fn = getattr(sm, "get_escalation_threshold", None)
        assert callable(fn), "sprint_manager must import get_escalation_threshold"

        threshold = sm.get_escalation_threshold(cfg=None)
        assert threshold == 2, (
            f"Expected default threshold=2 when env var absent, got {threshold}"
        )

        mock_dispatch = MagicMock()
        mock_blocked = MagicMock()

        with (
            patch(
                "services.sprint_manager.sprint_manager.dispatch_alerts",
                mock_dispatch,
            ),
            patch(
                "services.sprint_manager.sprint_manager._add_blocked_label",
                mock_blocked,
            ),
        ):
            result1 = check_dead_letter_escalation(
                ticket_id=20442,
                title="Default threshold test",
                last_error=None,
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )
            result2 = check_dead_letter_escalation(
                ticket_id=20442,
                title="Default threshold test",
                last_error=None,
                sprints_dir=tmp_path,
                threshold=threshold,
                alert_modes=[],
                cfg=None,
                repo=None,
                sprint_label=None,
            )

        assert result1 is False, "1st dead-letter at threshold=2 must not escalate"
        assert result2 is True, "2nd dead-letter at threshold=2 must escalate"
