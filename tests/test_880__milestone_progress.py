"""Tests for issue #880: Show active milestone progress on home and sprint nav.

Each test is anchored to a specific acceptance criterion:

- AC1  Each home project card displays the project's active milestone name
- AC2  Indicator shows compact progress count "X done + Y UAT / Z total"
- AC3  No active milestone -> no indicator (card renders normally)
- AC4  Clicking the home card indicator navigates to that project's Roadmap tab
- AC5  Project header (sprint nav) shows the milestone name + same progress count
- AC6  Clicking the header indicator navigates to the Roadmap tab
- AC7  Progress counts reflect real-time milestone issue states (done and UAT)
- AC8  Indicator is readable and visually distinct (high-contrast, not dominating)

The service tests inject fakes for the GitHub/settings boundary so they are
deterministic and never touch the network. The frontend tests assert the
rendered template wires the indicator and navigation correctly.
"""
import sys
from pathlib import Path

import pytest

_DASH = Path(__file__).parent.parent / "apps" / "dashboard"
_SVCS = Path(__file__).parent.parent / "services" / "sprint_manager"
for _p in (_DASH, _SVCS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from routers import milestone_progress_service as svc  # noqa: E402

STATIC = _DASH / "static"
HOME_HTML = (STATIC / "home-preview.html").read_text(encoding="utf-8")
PROJECT_HTML = (STATIC / "project.html").read_text(encoding="utf-8")
SYSTEM_PY = (_DASH / "routers" / "system.py").read_text(encoding="utf-8")


@pytest.fixture
def fake_milestone(monkeypatch):
    """Active milestone #5 with a known mix of done / UAT / other issues."""
    monkeypatch.setattr(
        svc._settings, "get_setting_scoped",
        lambda scope, key, project=None: {"active_milestone": 5},
    )
    monkeypatch.setattr(svc, "_list_milestones",
                        lambda repo: [{"number": 5, "title": "Roadmap v2"}])
    monkeypatch.setattr(svc, "_milestone_issues", lambda repo, number: [
        {"state": "closed", "labels": []},                       # done
        {"state": "closed", "labels": []},                       # done
        {"state": "open", "labels": [{"name": "UAT-approved"}]}, # done (approved)
        {"state": "open", "labels": [{"name": "UAT"}]},          # uat
        {"state": "open", "labels": [{"name": "SIT"}]},          # other
        {"state": "open", "labels": [{"name": "in-progress"}]},  # other
    ])


# ── AC7: progress counts reflect real done/UAT issue states ───────────────────

def test_compute_progress_counts_done_and_uat():
    issues = [
        {"state": "closed", "labels": []},
        {"state": "open", "labels": [{"name": "UAT-approved"}]},
        {"state": "open", "labels": [{"name": "UAT"}]},
        {"state": "open", "labels": [{"name": "backlog"}]},
    ]
    p = svc.compute_progress(issues)
    assert p == {"done": 2, "uat": 1, "total": 4, "completed": 3}


# ── AC1 + AC2 + AC7: active milestone name + counts surface together ───────────

def test_active_milestone_progress_returns_name_and_counts(fake_milestone):
    result = svc.active_milestone_progress("zealchaiwut/commander")
    assert result["title"] == "Roadmap v2"        # AC1: milestone name
    assert result["done"] == 3                     # AC7: done = closed + UAT-approved
    assert result["uat"] == 1                       # AC7: uat column
    assert result["total"] == 6                     # AC7: every milestone issue
    assert result["completed"] == 4                 # done + uat


# ── AC3: no active milestone -> no indicator ──────────────────────────────────

def test_no_active_milestone_returns_none(monkeypatch):
    monkeypatch.setattr(
        svc._settings, "get_setting_scoped",
        lambda scope, key, project=None: {},  # nothing persisted
    )
    assert svc.active_milestone_progress("zealchaiwut/commander") is None


def test_active_milestone_missing_from_github_returns_none(monkeypatch):
    monkeypatch.setattr(
        svc._settings, "get_setting_scoped",
        lambda scope, key, project=None: {"active_milestone": 99},
    )
    monkeypatch.setattr(svc, "_list_milestones", lambda repo: [{"number": 5, "title": "x"}])
    assert svc.active_milestone_progress("zealchaiwut/commander") is None


# ── AC1/AC5: home_milestones maps each project slug to its progress (or None) ──

def test_home_milestones_keyed_by_slug(monkeypatch, fake_milestone):
    monkeypatch.setattr(
        svc.projects_module, "load_projects",
        lambda: [{"repo": "zealchaiwut/commander"}],
    )
    out = svc.home_milestones()
    assert "commander" in out
    assert out["commander"]["title"] == "Roadmap v2"


# ── AC2: compact "X done + Y UAT / Z total" format rendered on both surfaces ───

def test_home_card_renders_compact_count_format():
    # home-preview builds the count string in milestoneCountText()
    assert "' done + '" in HOME_HTML and "' UAT / '" in HOME_HTML and "' total'" in HOME_HTML


def test_header_renders_compact_count_format():
    # project header builds the same string in _msnavRefresh()
    assert "done + ${m.uat} UAT / ${m.total} total" in PROJECT_HTML


# ── AC1: home card shows the active milestone name ────────────────────────────

def test_home_card_has_milestone_name_element():
    assert "proj-milestone-name" in HOME_HTML
    assert "milestoneIndicatorHtml(proj.slug)" in HOME_HTML
    assert "/api/milestones/home" in HOME_HTML


# ── AC4: clicking the home indicator navigates to the project's Roadmap tab ────

def test_home_indicator_navigates_to_roadmap():
    assert "function navigateToRoadmap" in HOME_HTML
    assert "/roadmap'" in HOME_HTML
    assert "wireMilestoneIndicator(card, proj.slug)" in HOME_HTML


# ── AC5: project header shows the milestone name + progress ───────────────────

def test_header_has_milestone_pill_and_fetch():
    assert 'id="msnav-pill"' in PROJECT_HTML
    assert 'id="msnav-name"' in PROJECT_HTML
    assert 'id="msnav-count"' in PROJECT_HTML
    assert "/api/milestones/project?repo=" in PROJECT_HTML


# ── AC6: clicking the header indicator opens the Roadmap tab ───────────────────

def test_header_indicator_opens_roadmap_tab():
    assert "onclick=\"switchTab('roadmap')\"" in PROJECT_HTML


# ── AC7: counts refresh on the live 30s cycle ─────────────────────────────────

def test_header_milestone_refreshes_on_interval():
    assert "_msnavRefresh()" in PROJECT_HTML
    # wired into the same 30s setInterval as the sprint nav refresh
    assert "_msnavRefresh(); }, 30000)" in PROJECT_HTML


# ── AC8: indicator uses high-contrast colors and modest sizing ────────────────

def test_indicator_uses_high_contrast_colors_not_muted():
    # Name/icon use --blue, count uses --text — both meet WCAG AA on the card /
    # pill surfaces. The low-contrast --text-sub / --text-muted are NOT used.
    assert ".proj-milestone-name {\n      font-weight: 600; color: var(--blue);" in HOME_HTML
    assert ".proj-milestone-count { color: var(--text);" in HOME_HTML
    assert ".msnav-name {" in PROJECT_HTML and "color: var(--blue)" in PROJECT_HTML
    assert ".msnav-count { color: var(--text);" in PROJECT_HTML


def test_indicator_not_tiny_text():
    # 12px (not the flagged 11px tiny-text) keeps the indicator readable.
    assert "font-size: 12px" in HOME_HTML.split(".proj-milestone {")[1].split("}")[0]


# ── endpoints are registered on a mounted router (not server.py monolith) ─────

def test_endpoints_registered_on_system_router():
    assert '@router.get("/api/milestones/home")' in SYSTEM_PY
    assert '@router.get("/api/milestones/project")' in SYSTEM_PY
    assert "milestone_progress_service" in SYSTEM_PY
