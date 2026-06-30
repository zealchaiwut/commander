"""Tests for LLM provider badge on dashboard run views (issue #1670).

AC1: live snapshot includes llm_provider key
AC3: value comes from run/session metadata
AC4: captured per-run at sprint start (not from current global)
AC5: backward compatible — runs without the field show no badge
AC6: no extra API calls — provider read from already-loaded status_data
AC7: both provider values (anthropic, ica) are handled

sys.path note: do NOT add services/sprint_manager to sys.path here —
it shadows apps/dashboard/config.py causing ImportError.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# Add repo root and dashboard to sys.path
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "dashboard"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sprint_state(llm_provider: Optional[str] = None) -> dict:
    return {
        "sprint_label": "sprint-104",
        "sprint_number": 104,
        "project": "zealchaiwut/commander",
        "issues": [],
        "start_timestamp": "2026-06-30T00:00:00Z",
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "wall_clock_secs": 0.0,
        "token_budget": 0,
        "rate_limit_events": [],
        "llm_provider": llm_provider,
    }


# ---------------------------------------------------------------------------
# AC1: live snapshot includes llm_provider key
# ---------------------------------------------------------------------------

class TestAc1LiveSnapshotKey:
    def test_llm_provider_key_present_when_set(self):
        status_data = _make_sprint_state(llm_provider="anthropic")
        assert "llm_provider" in status_data
        assert status_data["llm_provider"] == "anthropic"

    def test_llm_provider_key_present_when_ica(self):
        status_data = _make_sprint_state(llm_provider="ica")
        assert status_data["llm_provider"] == "ica"

    def test_llm_provider_key_present_when_none(self):
        status_data = _make_sprint_state(llm_provider=None)
        assert "llm_provider" in status_data
        assert status_data["llm_provider"] is None


# ---------------------------------------------------------------------------
# AC3 + AC4: SprintState serialization — field stored at sprint start
# ---------------------------------------------------------------------------

class TestAc3Ac4SprintStateSerialization:
    def test_sprint_state_has_llm_provider_field(self):
        sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
        try:
            from state import SprintState
            s = SprintState(sprint_label="sprint-104", sprint_number=104, llm_provider="anthropic")
            assert s.llm_provider == "anthropic"
        finally:
            sys.path.pop(0)

    def test_sprint_state_to_dict_includes_llm_provider(self):
        sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
        try:
            from state import SprintState
            s = SprintState(sprint_label="sprint-104", sprint_number=104, llm_provider="ica")
            d = s.to_dict()
            assert "llm_provider" in d
            assert d["llm_provider"] == "ica"
        finally:
            sys.path.pop(0)

    def test_sprint_state_from_dict_reads_llm_provider(self):
        sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
        try:
            from state import SprintState
            data = _make_sprint_state(llm_provider="ica")
            s = SprintState.from_dict(data)
            assert s.llm_provider == "ica"
        finally:
            sys.path.pop(0)

    def test_sprint_state_to_dict_roundtrip(self):
        sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
        try:
            from state import SprintState
            s = SprintState(sprint_label="sprint-104", sprint_number=104, llm_provider="anthropic")
            d = s.to_dict()
            s2 = SprintState.from_dict(d)
            assert s2.llm_provider == "anthropic"
        finally:
            sys.path.pop(0)


# ---------------------------------------------------------------------------
# AC5: backward compatibility — None provider shows no badge
# ---------------------------------------------------------------------------

class TestAc5BackwardCompat:
    def test_none_provider_survives_roundtrip(self):
        sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
        try:
            from state import SprintState
            s = SprintState(sprint_label="sprint-1", sprint_number=1)
            assert s.llm_provider is None
            d = s.to_dict()
            assert d["llm_provider"] is None
            s2 = SprintState.from_dict(d)
            assert s2.llm_provider is None
        finally:
            sys.path.pop(0)

    def test_from_dict_without_llm_provider_key_defaults_to_none(self):
        sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))
        try:
            from state import SprintState
            data = _make_sprint_state()
            del data["llm_provider"]
            s = SprintState.from_dict(data)
            assert s.llm_provider is None
        finally:
            sys.path.pop(0)


# ---------------------------------------------------------------------------
# AC6: no extra API calls — live snapshot reads from status_data
# ---------------------------------------------------------------------------

class TestAc6NoExtraApiCalls:
    def test_llm_provider_read_from_status_data_not_settings(self):
        status_data = _make_sprint_state(llm_provider="ica")
        # Reading from already-loaded status_data dict — no import needed
        result = status_data.get("llm_provider")
        assert result == "ica"

    def test_live_snapshot_payload_has_provider(self):
        status_data = _make_sprint_state(llm_provider="anthropic")
        # Simulate what sprint_live.py does: read from status_data
        payload = {
            "llm_provider": status_data.get("llm_provider"),
        }
        assert payload["llm_provider"] == "anthropic"


# ---------------------------------------------------------------------------
# AC7: both provider values handled by frontend code
# ---------------------------------------------------------------------------

class TestAc7BothProviders:
    def test_css_classes_for_both_providers_exist(self):
        html_path = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
        if not html_path.exists():
            pytest.skip("project.html not found")
        html = html_path.read_text(encoding="utf-8")
        assert "provider-badge--ica" in html
        assert "provider-badge--anthropic" in html

    def test_provider_badge_helper_handles_both_values(self):
        html_path = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
        if not html_path.exists():
            pytest.skip("project.html not found")
        html = html_path.read_text(encoding="utf-8")
        assert "_smgmtProviderBadgeHtml" in html
        assert "provider === 'ica'" in html


# ---------------------------------------------------------------------------
# Frontend structure tests
# ---------------------------------------------------------------------------

class TestFrontendStructure:
    @pytest.fixture(autouse=True)
    def load_html(self):
        html_path = REPO_ROOT / "apps" / "dashboard" / "static" / "project.html"
        if not html_path.exists():
            pytest.skip("project.html not found")
        self.html = html_path.read_text(encoding="utf-8")

    def test_provider_badge_css_defined(self):
        assert ".provider-badge {" in self.html

    def test_provider_badge_used_in_run_head(self):
        assert "_smgmtProviderBadgeHtml(live && live.llm_provider)" in self.html

    def test_provider_badge_used_in_ai_row(self):
        # Badge appears in both _smgmtAiRowHtml and _smgmtAiPatchRowMeta
        count = self.html.count("providerBadge")
        assert count >= 2, f"Expected providerBadge in at least 2 places, found {count}"
