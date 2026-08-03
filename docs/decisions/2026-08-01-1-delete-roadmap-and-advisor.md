# 2026-08-01-1-delete-roadmap-and-advisor

> Status: decided

## Context

Roadmap and Advisor were unreachable in the live dashboard — hidden behind a CSS kill-switch (#stab-roadmap, .hnav-milestone { display: none !important }) and a feature flag COMMANDER_DISABLE_ADVISOR. Together they account for ~1000+ lines of JS/CSS/Python across routers/advisor.py, advisor_service.py, roadmap.py, roadmap_service.py, suggestions.py, suggestions_service.py, and large chunks of project.html. The Advisor ADR (2026-07-02-1) already authorised its deletion. Roadmap milestone data is ~50% test-generated noise from earlier sprints and has no active owner; re-enabling it would require a separate data-cleanup sprint first.

## Options

<!-- list options considered -->

## Decision

Delete both Roadmap and Advisor in full: remove all Python router modules, unregister from server.py and routers/__init__.py, remove the feature flag from config.py, remove all CSS blocks and HTML panes from project.html, remove the Planning nav group, remove the hnav-milestone header element, strip the dead advisor flag from conftest.py, and rewrite features.js to omit advisorEnabled.

## Consequences

Approx 1000+ lines removed across Python and frontend. The /api/roadmap/*, /api/advisor/*, and /api/suggestions/* endpoints are gone. Any external caller relying on these endpoints will receive 404. Milestone-header indicator (_milestoneRefresh) is removed. Settings page no longer shows the disable_advisor toggle. The Planning nav group in the tab strip is gone.

## Implemented-by (#N)

#2075
