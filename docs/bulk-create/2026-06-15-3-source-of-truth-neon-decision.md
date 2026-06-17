# Source of truth — finish SQLite path (Neon stays export-only)

**Date:** 2026-06-15
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

## Notes

**Decision:** Do not revive Neon/Postgres as the primary store. Recent count/state drift
(PRs #1086/#1105) was algorithmic — two code paths computed "done" differently — not
a storage-layer problem. Postgres would add network dependency and dual-write complexity
without fixing the drift class.

**SoT contract (target):**

| Store | Owns | Written by |
|-------|------|------------|
| GitHub | ticket/sprint state (labels, membership, PRs) | `state_machine.transition()` |
| SQLite (`commander.db`) | metrics — `agent_runs`, `token_usage`, `events`, `sprints`, mirrors | sprint manager + dashboard |
| Disk JSON (`.commander/sprints/*`) | write-once run artifacts | once at end-of-run |
| Neon/Postgres | disabled (`COMMANDER_DISABLE_NEON=1`); export-only via `scripts/export_to_neon.py` | on demand |

Conflict rule: GitHub wins state · SQLite wins metrics · disk is write-once audit, ingested
to DB at end-of-run, not read at render.

Revisit Neon as primary only if: multi-user auth, multi-machine concurrent writes, or
hosted live BI — none apply today.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Lazy-ingest the Sprint History read path so render never forks disk vs DB. Mirror the outcome/finish-card lazy-ingest pattern: when a History row lacks run_ingested_at but the sprints row exists, ingest from the on-disk artifact so the next read is DB-only. Remove any History-specific disk read that bypasses ingest. Acceptance: opening History for a finished sprint with disk artifact but no run_ingested_at triggers ingest once; subsequent reads hit SQLite only; no dual read path at render; existing History tests pass or are updated for the single path.
---
Delete disk-read branches once all sprint-summary readers lazy-ingest. After History ingest lands, remove disk-fallback code in outcome, history, and finish-card readers so there is exactly one read path (SQLite). Keep disk JSON as write-once audit only — never returned in API responses. Acceptance: grep for disk-read fallbacks in outcome/history/finish paths finds none; API responses for terminal sprints come from DB rows only; disk files still written at run finish; regression tests for outcome, history, and finish-card pass.
---
Extend background reconcile to fix stored counts, not just lifecycle state. Background reconcile currently fixes lifecycle state only. Have it re-derive issues_json/counts from agent_runs for terminal sprints and wire the existing db.update_sprint_reconciliation(). Closes the stored-row side of count drift (outcome-side union already shipped). Acceptance: reconcile job updates denormalized counts on sprints rows when agent_runs disagree with issues_json; update_sprint_reconciliation() is called from the reconcile path; test covers a drifted row being corrected.
---
Optional: materialized sprint_summary row on run finish. Compute and store a denormalized summary in ingest_sprint_run_artifact() (or adjacent finish hook): settled done count, UAT count, failure count, token totals — one fixed shape. Point outcome and history reads at this row for O(1) stable reads instead of inline re-derivation. Acceptance: finish ingest writes sprint_summary (or equivalent denormalized columns); outcome and history prefer the materialized row when present; count shape matches _settled_done_from_columns / canonical formula; test asserts stable read after ingest.
---
Document the source-of-truth contract in architecture docs. Update docs/architecture/1_state-and-source-of-truth.md: GitHub = state, SQLite = metrics (single DB-only read path at render), disk = write-once audit, Neon = optional export. State the one canonical settled-done count definition and which panes use which metric (donut = completed; everything else = settled). Acceptance: doc section exists with the four-store table, conflict rules, canonical count helper name, and explicit "do not read disk at render"; links from sprint-lifecycle or dashboard architecture if helpful.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
