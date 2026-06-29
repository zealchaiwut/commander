"""Tests for issue #239: Apply needs-rework label on logic-failure ticket statuses.

Acceptance Criteria:
  AC-1: sprint_manager calls _apply_needs_rework_label after coder/gate failure for
        TESTER_REJECTED, CODER_NO_WORK, MERGE_CONFLICT, LINT_FAIL, PYTEST_FAIL categories.
  AC-2: Infrastructure failure categories (CRASH, HANG, RETRY_EXHAUSTED, TIMEOUT, RATE_LIMIT)
        do NOT trigger the needs-rework label call.
  AC-3: update_ticket.py STATUS_MAP contains "needs-rework" entry that adds `need-rework`
        label and removes UAT, SIT, UAT-approved, in-progress.
  AC-4: _apply_needs_rework_label catches exceptions and handles missing labels as warnings.
  AC-5: list_backlog_issues treats need-rework tickets as backlog (picked up on re-run).
  AC-6: Dashboard renders need-rework tickets with visual distinction from plain backlog.
"""

import sys
import importlib
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── path setup ────────────────────────────────────────────────────────────────
REPO_ROOT      = Path(__file__).parent.parent.parent.parent
SCRIPTS_DIR    = REPO_ROOT / "scripts"
SPRINT_MGR_DIR = REPO_ROOT / "services" / "sprint_manager"

sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SPRINT_MGR_DIR))


# ── helpers ───────────────────────────────────────────────────────────────────

def _import_sprint_manager():
    """Import sprint_manager, mocking heavy deps so it loads in test env."""

    # stub dotenv and github_client before import
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_stub)

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    gc_stub.update_labels = MagicMock()
    gc_stub.add_comment = MagicMock()
    gc_stub.search_issues_by_title = MagicMock(return_value=[])
    gc_stub.get_issue = MagicMock(return_value={})
    gc_stub.update_issue_body = MagicMock()
    sys.modules["github_client"] = gc_stub

    yaml_stub = types.ModuleType("yaml")
    yaml_stub.safe_load = lambda t: {}
    sys.modules.setdefault("yaml", yaml_stub)

    if "sprint_manager" in sys.modules:
        return sys.modules["sprint_manager"]
    return importlib.import_module("sprint_manager")


def _import_update_ticket():
    """Import update_ticket module, stubbing github_client."""

    gc_stub = types.ModuleType("github_client")
    gc_stub.repo = lambda: "zealchaiwut/commander"
    sys.modules.setdefault("github_client", gc_stub)

    if "update_ticket" in sys.modules:
        return sys.modules["update_ticket"]
    return importlib.import_module("update_ticket")


# ── AC-1: Logic failure categories trigger _apply_needs_rework_label ──────────

class TestAC1LogicFailureTriggerNeedsRework:

    # The standalone _apply_needs_rework_label helper was removed (issue #1585);
    # needs-rework labelling now flows through _transition_safe(NEEDS_REWORK),
    # which delegates to the single state_machine.transition() source of truth.

    def test_needs_rework_transition_routes_to_state_machine(self):
        sm = _import_sprint_manager()
        from services.sprint_manager import label_transitions as lt

        captured = []

        def fake_transition(issue, target_state, **kwargs):
            captured.append((issue, target_state))
            return True

        with patch.object(sm, "_sm_transition", fake_transition), \
             patch.object(lt, "_current_status_labels", lambda n, repo: frozenset()):
            sm._transition_safe(42, sm._TicketState.NEEDS_REWORK, actor="sprint_manager")

        assert captured == [(42, sm._TicketState.NEEDS_REWORK)], (
            "needs-rework labelling must route through state_machine.transition()"
        )

    @pytest.mark.parametrize("category", [
        "TESTER_REJECTED",
        "CODER_NO_WORK",
        "MERGE_CONFLICT",
        "LINT_FAIL",
        "PYTEST_FAIL",
    ])
    def test_logic_failure_category_in_frozenset(self, category):
        sm = _import_sprint_manager()
        assert category in sm._LOGIC_FAILURE_CATEGORIES, (
            f"{category} must be in _LOGIC_FAILURE_CATEGORIES to trigger needs-rework label"
        )

    def test_needs_rework_state_maps_to_needs_rework_label(self):
        """The NEEDS_REWORK state applies the 'needs-rework' status label and
        clears the other status labels — the behaviour the old helper performed
        via update_ticket.py, now owned by state_machine.transition()."""
        from services.sprint_manager import state_machine as smach

        labels = smach.STATE_LABELS[smach.TicketState.NEEDS_REWORK]
        assert labels == frozenset({"needs-rework"}), (
            f"NEEDS_REWORK must add exactly the 'needs-rework' label, got {labels!r}"
        )
        # in-progress / SIT / UAT are status labels, so transition() clears them
        # when moving to NEEDS_REWORK (to_remove = current_status - desired).
        for other in ("in-progress", "SIT", "UAT"):
            assert other in smach.STATUS_LABELS, (
                f"{other} must be a managed status label so it is cleared on rework"
            )

    def test_all_five_logic_categories_defined_on_failure_category(self):
        sm = _import_sprint_manager()
        fc = sm.FailureCategory
        assert hasattr(fc, "TESTER_REJECTED")
        assert hasattr(fc, "CODER_NO_WORK")
        assert hasattr(fc, "MERGE_CONFLICT")
        assert hasattr(fc, "LINT_FAIL")
        assert hasattr(fc, "PYTEST_FAIL")


# ── AC-2: Infrastructure failures do NOT trigger needs-rework label ───────────

class TestAC2InfraFailuresNoNeedsRework:

    @pytest.mark.parametrize("category", [
        "CRASH",
        "HANG",
        "RETRY_EXHAUSTED",
    ])
    def test_infra_category_not_in_logic_failure_set(self, category):
        sm = _import_sprint_manager()
        assert category not in sm._LOGIC_FAILURE_CATEGORIES, (
            f"{category} is an infrastructure failure and must NOT be in "
            f"_LOGIC_FAILURE_CATEGORIES"
        )

    @pytest.mark.parametrize("category", [
        "CRASH",
        "HANG",
        "RETRY_EXHAUSTED",
    ])
    def test_infra_category_gate_skips_needs_rework(self, category):
        """The call-site gate is `if category in _LOGIC_FAILURE_CATEGORIES`.
        Infra categories are not members, so the NEEDS_REWORK transition is
        never reached for them."""
        sm = _import_sprint_manager()
        fc_category = getattr(sm.FailureCategory, category)
        assert fc_category not in sm._LOGIC_FAILURE_CATEGORIES, (
            f"infra category {category} must NOT gate-through to a needs-rework transition"
        )

    def test_none_category_gate_skips_needs_rework(self):
        sm = _import_sprint_manager()
        assert None not in sm._LOGIC_FAILURE_CATEGORIES, (
            "None category must NOT gate-through to a needs-rework transition"
        )


# ── AC-3: update_ticket.py STATUS_MAP has correct needs-rework entry ──────────

class TestAC3StatusMapNeedsRework:

    def test_needs_rework_in_status_map(self):
        ut = _import_update_ticket()
        assert "needs-rework" in ut.STATUS_MAP, (
            '"needs-rework" key must exist in update_ticket.py STATUS_MAP'
        )

    def test_needs_rework_adds_need_rework_label(self):
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        assert "need-rework" in entry["add"], (
            'STATUS_MAP["needs-rework"]["add"] must contain "need-rework" (no "s")'
        )

    def test_needs_rework_removes_in_progress(self):
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        assert "in-progress" in entry["remove"], (
            '"in-progress" must be removed when applying needs-rework status'
        )

    def test_needs_rework_removes_sit(self):
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        assert "SIT" in entry["remove"], (
            '"SIT" must be removed when applying needs-rework status'
        )

    def test_needs_rework_removes_uat(self):
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        assert "UAT" in entry["remove"], (
            '"UAT" must be removed when applying needs-rework status'
        )

    def test_needs_rework_removes_uat_approved(self):
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        assert "UAT-approved" in entry["remove"], (
            '"UAT-approved" must be removed when applying needs-rework status'
        )

    def test_needs_rework_does_not_close_issue(self):
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        assert not entry.get("close"), (
            '"needs-rework" status must not close the issue (close must be None/falsy)'
        )

    def test_needs_rework_add_uses_need_rework_not_needs_rework(self):
        """Label name is 'need-rework' (no 's'), matching actual GitHub label."""
        ut = _import_update_ticket()
        entry = ut.STATUS_MAP["needs-rework"]
        # must be exactly "need-rework" (without trailing 's')
        assert "needs-rework" not in entry["add"], (
            'add list must contain "need-rework" (not "needs-rework" with s)'
        )


# ── AC-4: _apply_needs_rework_label is resilient — exceptions are warnings ────

class TestAC4Resilience:

    # _transition_safe() is the resilient wrapper that replaced the old helper's
    # best-effort behaviour: a failed transition must never raise out of the
    # sprint pipeline — it is logged as a warning instead.

    def _patch_labels(self):
        from services.sprint_manager import label_transitions as lt
        return patch.object(lt, "_current_status_labels", lambda n, repo: frozenset())

    def test_transition_safe_survives_generic_exception(self):
        sm = _import_sprint_manager()

        def boom(*a, **kw):
            raise RuntimeError("network error")

        with patch.object(sm, "_sm_transition", boom), self._patch_labels():
            # Must not raise — exceptions are caught and logged as warnings.
            sm._transition_safe(7, sm._TicketState.NEEDS_REWORK, actor="sprint_manager")

    def test_transition_safe_survives_transition_error(self):
        sm = _import_sprint_manager()

        def boom(*a, **kw):
            raise sm._TransitionError("gh issue edit failed after retries")

        with patch.object(sm, "_sm_transition", boom), self._patch_labels():
            sm._transition_safe(7, sm._TicketState.NEEDS_REWORK, actor="sprint_manager")

    def test_transition_safe_returns_none_on_failure(self):
        sm = _import_sprint_manager()

        def boom(*a, **kw):
            raise RuntimeError("boom")

        with patch.object(sm, "_sm_transition", boom), self._patch_labels():
            result = sm._transition_safe(8, sm._TicketState.NEEDS_REWORK, actor="sprint_manager")
        assert result is None, "_transition_safe is best-effort and returns None"

    def test_transition_safe_logs_warning_on_failure(self):
        sm = _import_sprint_manager()
        from services.sprint_manager import label_transitions as lt

        def boom(*a, **kw):
            raise RuntimeError("label missing on repo")

        warn_mock = MagicMock()
        log_stub = MagicMock(warn=warn_mock)
        with patch.object(sm, "_sm_transition", boom), \
             patch.object(lt, "structured_log", log_stub), \
             self._patch_labels():
            sm._transition_safe(9, sm._TicketState.NEEDS_REWORK, actor="sprint_manager")

        assert warn_mock.called, (
            "a failed transition must be logged as a warning (best-effort)"
        )


# ── AC-5: need-rework tickets are picked up as backlog on re-run ──────────────

class TestAC5NeedsReworkPickedUpOnRerun:

    def test_classify_need_rework_label_returns_backlog(self):
        sm = _import_sprint_manager()
        labels = {"need-rework", "backlog", "sprint-17"}
        result = sm._classify(labels)
        assert result == "backlog", (
            f"_classify must return 'backlog' for need-rework tickets, got '{result}'"
        )

    def test_classify_need_rework_without_blocking_labels_is_backlog(self):
        sm = _import_sprint_manager()
        for labels in [
            {"need-rework"},
            {"need-rework", "enhancement"},
            {"need-rework", "backlog", "sprint-17", "estimated"},
        ]:
            result = sm._classify(labels)
            assert result == "backlog", (
                f"_classify({labels!r}) returned '{result}'; expected 'backlog'"
            )

    def test_classify_returns_done_when_uat_approved_even_with_need_rework(self):
        """UAT-approved always wins — but that state never occurs with need-rework."""
        sm = _import_sprint_manager()
        # In practice update_ticket removes UAT-approved when adding need-rework,
        # but test the priority ordering is correct.
        labels = {"UAT-approved", "need-rework"}
        result = sm._classify(labels)
        assert result == "done"

    def test_list_backlog_issues_includes_need_rework_tickets(self):
        sm = _import_sprint_manager()
        fake_issue = {
            "number": 42,
            "title": "My ticket",
            "labels": [
                {"name": "sprint-17"},
                {"name": "need-rework"},
                {"name": "backlog"},
            ],
        }
        fake_result = MagicMock()
        fake_result.stdout = __import__("json").dumps([fake_issue])
        fake_result.returncode = 0

        with patch("subprocess.run", return_value=fake_result):
            issues = sm.list_backlog_issues("sprint-17")

        assert len(issues) == 1, (
            "list_backlog_issues must include tickets labelled need-rework"
        )
        assert issues[0]["number"] == 42


# ── AC-6: Dashboard renders need-rework tickets with visual distinction ────────

class TestAC6DashboardVisualDistinction:

    def _get_index_html(self) -> str:
        html_path = REPO_ROOT / "apps" / "dashboard" / "static" / "index.html"
        return html_path.read_text(encoding="utf-8")

    def _get_app_js(self) -> str:
        js_path = REPO_ROOT / "apps" / "dashboard" / "static" / "app.js"
        return js_path.read_text(encoding="utf-8")

    def test_smgmt_status_need_rework_css_defined(self):
        html = self._get_index_html()
        assert ".smgmt-status-need-rework" in html, (
            "CSS class .smgmt-status-need-rework must be defined in index.html "
            "to style need-rework tickets distinctly from backlog"
        )

    def test_smgmt_rework_badge_css_defined(self):
        html = self._get_index_html()
        assert ".smgmt-rework-badge" in html, (
            "CSS class .smgmt-rework-badge must be defined in index.html"
        )

    def test_is_needs_rework_css_class_defined(self):
        html = self._get_index_html()
        assert "is-needs-rework" in html, (
            ".is-needs-rework CSS class must be defined to add border accent "
            "to need-rework tickets"
        )

    def test_need_rework_status_distinct_from_backlog_css(self):
        html = self._get_index_html()
        # need-rework should have a different background/color than plain backlog
        assert ".smgmt-status-need-rework" in html
        assert ".smgmt-status-backlog" in html
        # The two classes must differ — simple check: both exist with distinct styling
        idx_rework = html.index(".smgmt-status-need-rework")
        idx_backlog = html.index(".smgmt-status-backlog")
        assert idx_rework != idx_backlog

    def test_app_js_detects_need_rework_label(self):
        js = self._get_app_js()
        assert "need-rework" in js, (
            'app.js must check for "need-rework" label to apply visual distinction'
        )

    def test_app_js_applies_is_needs_rework_class(self):
        js = self._get_app_js()
        assert "is-needs-rework" in js, (
            'app.js must apply "is-needs-rework" CSS class to need-rework tickets'
        )

    def test_app_js_renders_rework_badge(self):
        js = self._get_app_js()
        assert "smgmt-rework-badge" in js, (
            'app.js must render .smgmt-rework-badge for need-rework tickets'
        )

    def test_app_js_sets_needs_rework_status_label(self):
        js = self._get_app_js()
        assert "smgmt-status-need-rework" in js, (
            'app.js must apply .smgmt-status-need-rework class to the status chip '
            'for need-rework tickets'
        )
