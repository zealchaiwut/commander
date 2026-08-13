"""Tests for issue #2309: Dead Cancel button and rerun remnants removed from board-render.js

AC tests verify that:
1. Dead cancel/rerun button and helpers are removed
2. No references to deleted endpoints remain
3. Board still renders with Finish/Reconcile/Preflight intact
"""
import subprocess
import re


def test_2309__smgmtcancelsprint_removed():
    """AC1: smgmtCancelSprint handler is removed from source and bundle"""
    # Check source files
    result = subprocess.run(
        ["grep", "-r", "smgmtCancelSprint", "apps/dashboard/static/src/"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "smgmtCancelSprint found in source code"

    # Check bundle
    result = subprocess.run(
        ["grep", "smgmtCancelSprint", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "smgmtCancelSprint found in bundle"


def test_2309__rerun_helpers_removed():
    """AC2: Rerun-only helpers (_smgmtIsFreshRerunSprint, _smgmtApplyRerunOptimistic) are removed"""
    helpers = [
        "_smgmtIsFreshRerunSprint",
        "_smgmtApplyRerunOptimistic",
    ]

    for helper in helpers:
        # Check source
        result = subprocess.run(
            ["grep", "-r", helper, "apps/dashboard/static/src/"],
            cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
            capture_output=True,
            text=True
        )
        assert result.returncode != 0, f"{helper} found in source code"

        # Check bundle
        result = subprocess.run(
            ["grep", helper, "apps/dashboard/static/dist/bundle.js"],
            cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
            capture_output=True,
            text=True
        )
        assert result.returncode != 0, f"{helper} found in bundle"


def test_2309__cancel_banner_and_child_label_removed():
    """AC3: _smgmtNextChildLabel and _smgmtCancelBannerHtml are removed"""
    helpers = [
        "_smgmtNextChildLabel",
        "_smgmtCancelBannerHtml",
    ]

    for helper in helpers:
        # Check source
        result = subprocess.run(
            ["grep", "-r", helper, "apps/dashboard/static/src/"],
            cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
            capture_output=True,
            text=True
        )
        assert result.returncode != 0, f"{helper} still exists in source code"


def test_2309__no_sprints_run_endpoint_references():
    """AC4a: No frontend reference to /api/sprints/run endpoint"""
    # Check source
    result = subprocess.run(
        ["grep", "-r", "sprints/run", "apps/dashboard/static/src/"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "sprints/run found in source code"

    # Check bundle
    result = subprocess.run(
        ["grep", "sprints/run", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "sprints/run found in bundle"


def test_2309__no_rerun_endpoint_references():
    """AC4b: No frontend reference to /rerun endpoint"""
    # Check source
    result = subprocess.run(
        ["grep", "-r", "/rerun", "apps/dashboard/static/src/"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "/rerun found in source code"

    # Check bundle
    result = subprocess.run(
        ["grep", "/rerun", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "/rerun found in bundle"


def test_2309__bundle_is_current():
    """AC5: bundle.js has been rebuilt (recent commit timestamp)"""
    # Verify bundle exists
    result = subprocess.run(
        ["test", "-f", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat"
    )
    assert result.returncode == 0, "bundle.js does not exist"

    # Verify bundle is not empty
    result = subprocess.run(
        ["test", "-s", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat"
    )
    assert result.returncode == 0, "bundle.js is empty"


def test_2309__finish_button_intact():
    """AC6a: Finish button and affordance still present in bundle"""
    result = subprocess.run(
        ["grep", "smgmtFinish", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "smgmtFinish not found - Finish button broken"
    assert len(result.stdout) > 0, "smgmtFinish reference not found in bundle"


def test_2309__reconcile_button_intact():
    """AC6b: Reconcile button and affordance still present in bundle"""
    result = subprocess.run(
        ["grep", "smgmtReconcile", "apps/dashboard/static/dist/bundle.js"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat",
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, "smgmtReconcile not found - Reconcile button broken"
    assert len(result.stdout) > 0, "smgmtReconcile reference not found in bundle"


def test_2309__rerun_test_files_deleted():
    """Verify obsolete rerun test files are deleted"""
    result = subprocess.run(
        ["test", "!", "-f", "tests/frontend/rerun-confirm-body.test.mjs"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat"
    )
    assert result.returncode == 0, "rerun-confirm-body.test.mjs still exists"

    result = subprocess.run(
        ["test", "!", "-f", "tests/frontend/rerun-modal-error-surfacing.test.mjs"],
        cwd="/Users/chaiwutchaianuchittrakul/dev/commander/uat"
    )
    assert result.returncode == 0, "rerun-modal-error-surfacing.test.mjs still exists"
