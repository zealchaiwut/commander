"""Tests for issue #1798: log swallowed exceptions in _enrich_home_artifact.

AC coverage:
  AC1 — _enrich_home_artifact logs WARNING when load_projects() raises
  AC2 — _enrich_home_artifact logs WARNING when get_or_create_project_summary() raises
  AC3 — both WARNINGs include exc_info so the traceback is visible in logs
  AC4 — safe fallbacks still returned in both cases (no exception propagated)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
for _p in (str(REPO_ROOT), str(DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from routers.brief import _enrich_home_artifact  # noqa: E402

_LOGGER_NAME = "routers.brief"


def _make_artifact(projects=None):
    return {
        "available": True,
        "brief": {"projects": projects or [{"project": "my-proj"}]},
        "date": "2026-01-01",
    }


# ── AC1: load_projects failure logs WARNING ───────────────────────────────────

class TestAC1LoadProjectsWarning:
    def test_load_projects_raises_logs_warning(self, caplog):
        """_enrich_home_artifact must log a WARNING when load_projects() raises (AC1)."""
        artifact = _make_artifact()
        with patch("routers.brief._projects_module.load_projects",
                   side_effect=RuntimeError("projects.json missing")):
            with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                _enrich_home_artifact(artifact)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, (
            "_enrich_home_artifact must emit at least one WARNING when load_projects raises"
        )

    def test_load_projects_raises_logs_exc_info(self, caplog):
        """WARNING from load_projects failure must include exc_info (AC3)."""
        artifact = _make_artifact()
        with patch("routers.brief._projects_module.load_projects",
                   side_effect=RuntimeError("projects.json missing")):
            with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                _enrich_home_artifact(artifact)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, "Expected at least one WARNING record"
        assert warning_records[0].exc_info is not None, (
            "WARNING for load_projects failure must include exc_info=True"
        )


# ── AC2: get_or_create_project_summary failure logs WARNING ──────────────────

class TestAC2SummaryWarning:
    def test_summary_raises_logs_warning(self, caplog):
        """_enrich_home_artifact must log a WARNING when get_or_create_project_summary raises (AC2)."""
        artifact = _make_artifact()
        with patch("routers.brief._projects_module.load_projects", return_value=[
            {"repo": "org/my-proj", "name": "My Proj", "icon": "ti-folder", "color": "gray"}
        ]):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       side_effect=RuntimeError("LLM service unavailable")):
                with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                    _enrich_home_artifact(artifact)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, (
            "_enrich_home_artifact must emit at least one WARNING when summary service raises"
        )

    def test_summary_raises_logs_exc_info(self, caplog):
        """WARNING from summary failure must include exc_info (AC3)."""
        artifact = _make_artifact()
        with patch("routers.brief._projects_module.load_projects", return_value=[
            {"repo": "org/my-proj", "name": "My Proj"}
        ]):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       side_effect=RuntimeError("LLM service unavailable")):
                with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                    _enrich_home_artifact(artifact)

        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warning_records, "Expected at least one WARNING record"
        assert warning_records[0].exc_info is not None, (
            "WARNING for summary failure must include exc_info=True"
        )


# ── AC4: safe fallbacks still returned ───────────────────────────────────────

class TestAC4Fallbacks:
    def test_load_projects_exception_falls_back_to_empty_list(self, caplog):
        """When load_projects raises, all_projects falls back to [] and no exception propagates (AC4)."""
        artifact = _make_artifact([{"project": "x"}])
        with patch("routers.brief._projects_module.load_projects",
                   side_effect=RuntimeError("disk error")):
            with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                _enrich_home_artifact(artifact)

        proj = artifact["brief"]["projects"][0]
        assert proj.get("name") == "x", (
            "Project name must fall back to the slug when load_projects raises"
        )

    def test_summary_exception_falls_back_to_empty_string(self, caplog):
        """When get_or_create_project_summary raises, briefSummary is '' (AC4)."""
        artifact = _make_artifact([{"project": "my-proj"}])
        with patch("routers.brief._projects_module.load_projects", return_value=[
            {"repo": "org/my-proj", "name": "My Proj"}
        ]):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       side_effect=RuntimeError("timeout")):
                with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                    _enrich_home_artifact(artifact)

        proj = artifact["brief"]["projects"][0]
        assert proj.get("briefSummary") == "", (
            "briefSummary must fall back to '' when get_or_create_project_summary raises"
        )

    def test_load_projects_exception_does_not_propagate(self, caplog):
        """_enrich_home_artifact must not raise when load_projects raises (AC4)."""
        artifact = _make_artifact()
        with patch("routers.brief._projects_module.load_projects",
                   side_effect=RuntimeError("disk error")):
            with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                _enrich_home_artifact(artifact)  # must not raise

    def test_summary_exception_does_not_propagate(self, caplog):
        """_enrich_home_artifact must not raise when summary service raises (AC4)."""
        artifact = _make_artifact([{"project": "my-proj"}])
        with patch("routers.brief._projects_module.load_projects", return_value=[
            {"repo": "org/my-proj", "name": "My Proj"}
        ]):
            with patch("routers.brief.brief_summary.get_or_create_project_summary",
                       side_effect=RuntimeError("timeout")):
                with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
                    _enrich_home_artifact(artifact)  # must not raise
