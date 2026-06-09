# Changelog

## Sprint 52

- [#672](https://github.com/zealchaiwut/commander/issues/672) Fix API Error on Finish Card Submission — 2026-06-09
- [#671](https://github.com/zealchaiwut/commander/issues/671) Sync sprint progress across all three pill components — 2026-06-09
- [#660](https://github.com/zealchaiwut/commander/issues/660) Fix Multiple Drag-and-Drop Selection Not Working — 2026-06-09
- [#658](https://github.com/zealchaiwut/commander/issues/658) Clear Empty Sprints Up To First Active Sprint — 2026-06-09
- [#657](https://github.com/zealchaiwut/commander/issues/657) Add Sprint and Agent Logs to Activity Tab — 2026-06-09
- [#656](https://github.com/zealchaiwut/commander/issues/656) Fix Bulk Create Status Stuck After Server Restart — 2026-06-09
- [#651](https://github.com/zealchaiwut/commander/issues/651) Add GET analytics/metrics endpoint for projects — 2026-06-09
- [#650](https://github.com/zealchaiwut/commander/issues/650) Build Analytics page with Calibration tab — 2026-06-09
- [#649](https://github.com/zealchaiwut/commander/issues/649) Add calibration analytics endpoint for ticket sizing — 2026-06-09
- [#648](https://github.com/zealchaiwut/commander/issues/648) Build Metrics tab with ANL-3 data cards — 2026-06-09
- [#674](https://github.com/zealchaiwut/commander/issues/674) Fix Duplicate Estimation Labels on Ticket View — 2026-06-09
- [#673](https://github.com/zealchaiwut/commander/issues/673) Limit visible tags to 10 most recently used — 2026-06-09
- [#659](https://github.com/zealchaiwut/commander/issues/659) Fix false failure when tester subprocess exits 0 — 2026-06-09

## Sprint 51

- #637: Add project_events table and recorder to dashboard DB
- #638: Add settings KV table and sprint_tickets.estimated_size
- #639: Add Settings REST API with effective read and override write
- #640: Estimator reads per-project config and writes estimated_size
- #641: Build global settings screen behind header gear icon
- #642: Build Project Settings tab under More
- #643: Add editable env paths with server-side folder browser
- #644: Add directional settings sync with diff preview

## Sprint 24

- #244: Add Neon DB connection module and Alembic scaffolding
- #245: Add SQLAlchemy models and migration for sprints + sprint_tickets
- #246: Add sprint repository layer for DB access
- #247: Dual-write sprint state to Neon and JSON
- #248: Add one-shot backfill script for sprints to Neon
- #325: Add structured JSON-lines logging module (disk-first)
- #326: Mint run_id at all agent entry points
- #327: Migrate failure-path print()s to structured logger
- #328: Fix missing .env entry in .gitignore
- #329: Add /api/version endpoint and surface build stamp in sidebar
- #330: Backup project identity fields from projects.json to Neon
- #331: Fix estimation failure on bulk create operation
- #335: Recreating Failed Ticket Creates Empty Issue Instead
- #336: Show label and attachment warnings after draft generation
- #337: Add Delete Action for Selected Closed/Unplanned Issues
- #339: Replace Goal Field with Sprint Label on New Sprint Creation
- #340: Auto-create next sprint on drag below last sprint
- #344: [follow-up] app.js: Add timeout to preview endpoint fetch in rerun modal
- #345: [follow-up] check_neon_connection.py: Consolidate psycopg2 import error handling
- #346: [follow-up] app.js: Remove unused RERUN_STRIP_LABELS constant
