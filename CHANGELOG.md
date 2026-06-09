# Changelog

## Sprint 51.1

- #639: Add Settings REST API with effective read and override write

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
