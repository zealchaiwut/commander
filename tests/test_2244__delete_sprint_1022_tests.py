"""Tests for issue #2244: Delete tests covering the sprint-1022 removals.

AC1: Tests importing deleted modules (scheduler, post_sprint, document_issue,
     resolve_conflict, alerts, dead_letter_escalation, brief_generator,
     label_transitions, events, ica_preflight, api_client, sprint_manager.failures,
     sprint_webhook_service, dispatch_service, run_browser) are gone.
AC2: Tests for retained modules (routers/failures*, Finish flow, Deploy) are untouched.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

_SHOULD_BE_DELETED = [
    # alerts / AlertMode
    "tests/test_ntfy_alert__ac2_function_signature.py",
    "tests/test_ntfy_alert__ac3_priority_mapping.py",
    "tests/test_ntfy_alert__ac4_dispatch_routing.py",
    "tests/test_ntfy_alert__ac6_silent_return_if_unset.py",
    "tests/test_ntfy_alert__ac7_exception_handling.py",
    "tests/test_ntfy_dispatch_wiring__463.py",
    "tests/test_1271__extract_alert_channels_to_alerts_py.py",
    "tests/test_rerun_dispatch_error__1839.py",
    # api_client
    "tests/test_api_client.py",
    "tests/test_extend_rate_limit_ica_gateway__1669.py",
    "tests/test_1676__api_client_import_hoisted.py",
    # brief_generator
    "tests/test_documentor_writes_per_sprint_brief__860.py",
    # dead_letter_escalation
    "tests/test_2033__dead_letter_escalation.py",
    "tests/test_2044__deadletter_threshold_callsite.py",
    "tests/test_1953__dead_letter_source_of_truth.py",
    # dispatch_service (routers)
    "tests/test_gh_token_injection.py",
    "tests/test_795__extract_sprints_tickets_routers.py",
    # events
    "tests/test_1275__extract_sprint_manager_event_emission.py",
    "tests/test_event_extraction__1275.py",
    # failures (sprint_manager.failures)
    "tests/test_extract_failure_handling__1279.py",
    "tests/test_extract_sprint_manager_failure_handling__1279.py",
    "tests/test_1279__extract_failure_handling.py",
    "tests/test_1511__dedupe_failure_category_import.py",
    "tests/test_pytest_gate_skip_and_env.py",
    # ica_preflight
    "tests/test_ica_per_run_provider.py",
    "tests/test_ica_preflight__validation.py",
    "tests/test_oauth_preflight__2029.py",
    "tests/test_2045__auth_preflight_end_reason.py",
    # label_transitions
    "tests/test_label_transitions.py",
    "tests/test_label_transitions__1282.py",
    "tests/test_mirror_stale_labels__1814.py",
    "tests/test_latest_active_sprint_mirror__1783.py",
    "tests/test_506__run_mutable_label_guard.py",
    "apps/dashboard/tests/test_apply_needs_rework_label_on_logic_failure__239.py",
    # post_sprint
    "tests/test_post_sprint__1288.py",
    # rerun tests importing alerts
    "tests/test_rerun_dispatch_race__1827.py",
    # scheduler (routers/scheduler)
    "tests/test_863__scheduled_sprint_queue.py",
    # sprint_webhook_service (routers)
    "tests/test_1865__sprint_finished_webhook.py",
    "tests/test_1945__commander_report_file.py",
    "tests/test_1951__atomic_write_unique_temp.py",
    "tests/test_1952__commander_report_commits_tests.py",
    "tests/test_1954__commander_report_needs_review_summary.py",
]

_SHOULD_SURVIVE = [
    # routers/failures* — retained
    "tests/test_failures_endpoint__2019.py",
    "tests/test_2041__failures_category_filter.py",
    "tests/test_2042__failures_scratchpad_filter.py",
    "tests/test_2200__ticket_failures_display_override.py",
    "tests/test_2206__complete_guards_ticket_failures.py",
    # Finish flow — retained
    "tests/test_1161__finish_card_db_only.py",
    "tests/test_2086__finish_merge_safety.py",
    "tests/test_2170__finish_flow_project_scoped.py",
    "tests/test_2230__finish_feature_uat_transition.py",
    # Deploy — retained
    "tests/test_722__deploy_config.py",
    "tests/test_726__deploy_tab.py",
    "tests/test_746__seed_only_deploy_config.py",
]


def test_ac1_deleted_module_tests_are_absent():
    """AC1: No test file that imports a sprint-1022 deleted module remains on disk."""
    still_present = [p for p in _SHOULD_BE_DELETED if (REPO_ROOT / p).exists()]
    assert not still_present, (
        "These test files should have been deleted but still exist:\n"
        + "\n".join(f"  {p}" for p in still_present)
    )


def test_ac2_retained_module_tests_untouched():
    """AC2: Tests for routers/failures*, Finish flow, and Deploy are still present."""
    missing = [p for p in _SHOULD_SURVIVE if not (REPO_ROOT / p).exists()]
    assert not missing, (
        "These retained test files are unexpectedly missing:\n"
        + "\n".join(f"  {p}" for p in missing)
    )
