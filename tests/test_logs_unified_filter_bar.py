"""Logs unified filter bar + Activity severity bucketing (HTML contract tests)."""
from __future__ import annotations

from pathlib import Path

PROJECT_HTML = (Path(__file__).resolve().parent.parent / "apps/dashboard/static/project.html").read_text()


def test_single_filter_bar_replaces_pill_rows():
    """Pill rows and separate runs filter bar are removed; one bar remains."""
    assert 'id="logs-toolbar-filters"' in PROJECT_HTML
    assert 'id="logs-runs-filters"' not in PROJECT_HTML
    assert 'id="logs-chips-sprint"' not in PROJECT_HTML
    assert 'data-group="event_level"' not in PROJECT_HTML
    assert 'data-group="agent_role"' not in PROJECT_HTML


def test_filter_bar_has_dropdowns_and_severity_seg():
    html = PROJECT_HTML
    assert 'id="logs-filter-sprint"' in html
    assert 'id="logs-filter-agent"' in html
    assert 'id="logs-filter-source"' in html
    assert 'id="logs-severity-seg"' in html
    assert 'value="estimator"' in html
    assert 'value="documenter"' in html
    assert 'value="reviewer"' in html
    assert 'value="sprint">Sprint</option>' in html or ">Sprint</option>" in html


def test_raw_level_dropdown_for_unbucketed_raw_view():
    html = PROJECT_HTML
    assert 'id="logs-filter-raw-level"' in html
    assert '_LOGS_RAW_LEVELS' in html
    assert 'logsFilterRawLevelSelect' in html


def test_activity_severity_bucketing_helpers():
    html = PROJECT_HTML
    for sym in (
        '_evlSeverityBucket',
        '_evlRawLevel',
        '_LOGS_INFO_LEVELS',
        '_LOGS_WARN_LEVELS',
        '_LOGS_ERROR_LEVELS',
        'retry_exhausted',
    ):
        assert sym in html, f"missing {sym}"
