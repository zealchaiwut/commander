# Calibration analytics fix — size resolution, cache rebuild, live refresh

**Date:** 2026-06-17
**Sprint label:** NEW
**Default labels:** enhancement, backend
**Status:** drafted

Incident: Analytics → Calibration shows ~18 tickets (M:3, L:5, XL:10) frozen
for weeks while 60+ sprint state files and 250+ completed tickets exist on
both UAT and PRD. Root cause is **not** “we need sprint-start estimation
again” — per-ticket estimation already runs at ticket create (and on
follow-ups after review). Two separate bugs:

1. **Write path split** — `estimate_issue.py` resolves `.commander/` by
   walking up from subprocess CWD. Dashboard runs from the `uat/` clone, so
   JSON often lands in `uat/.commander/estimates/` while calibration reads
   `~/dev/<project>/.commander/estimates/` (project root per nested layout).
2. **Read path too narrow** — calibration only ingests
   `estimates/issue-N.json` at project root. It ignores GitHub `size-*`
   labels (already applied at create time) and `state.estimates{}` on sprint
   state files. Tickets with a label but no JSON at the canonical path are
   silently skipped.

**Design constraint:** Do **not** add a second estimation pass. Keep
`sprint_estimator` skipped at run start (`skip_estimator=True`). Fix
canonical write location + read-side resolution only.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Fix calibration size resolution and canonical estimate JSON writes (no second estimation). Today Analytics → Calibration ingests completed tickets only when `~/dev/<project>/.commander/estimates/issue-N.json` exists, but per-ticket estimation at create time often writes JSON to the wrong `.commander/` (e.g. `uat/.commander/estimates/` because `find_commander_dir()` walks up from dashboard CWD). Many tickets already have `size-S/M/L/XL` GitHub labels from the single create-time estimator — calibration ignores those. **Do not enable sprint-start estimation** (`skip_estimator` stays true); do not re-run Haiku for tickets that already have a label.

**Write path (canonical estimates dir):** Resolve the project-root `.commander/` directory the same way sprint dispatch does (`_project_root_path(repo)` / sprint.yaml `paths`), not first `.commander` found walking from CWD. Apply to: `estimate_issue.py` (new `--commander-dir` flag or env `COMMANDER_PROJECT_ROOT`), dashboard background estimator subprocesses (`_run_estimator_for_issue`, bulk estimator), and follow-up `_dispatch_estimator_for_followup` (pass explicit project root from sprint config). After create-time estimation succeeds, `issue-N.json` must exist at `<project-root>/.commander/estimates/`. Add a one-time maintenance helper (can be a script, not a user-facing button yet) to copy/merge stray JSON from clone-local `.commander/estimates/` into project root when the canonical file is missing — **copy only, no re-estimation**.

**Read path (calibration ingestion):** Update `_calibration_issue_sample()` to use the same precedence as `_resolve_issue_estimate()` / preflight: (1) canonical `estimates/issue-N.json`, (2) `state.estimates[issue_num].size` on the same state file, (3) local issues-mirror DB `size-*` labels (`issues` table, no live GitHub call per ticket). Still require done-equivalent status (`done`, `uat`, `merged`, `passed`) and coder/tester timestamps for actual minutes. Update `_calibration_absorb_state_file`, `_compute_calibration_from_files`, and mis-sizing history rebuild (same estimate-only bug) to share one helper.

**Tests:** Extend `tests/test_649__calibration_analytics_endpoint.py` and `tests/test_718__analytics_local_files.py` — ticket with mirror label only (no JSON), ticket with JSON only in clone-local dir after canonical fix, ticket with `state.estimates` only. **UAT:** `curl /api/projects/commander/analytics/calibration` returns >>18 points after fix without re-running any estimator.

Acceptance: (1) create-time estimation writes JSON to project-root `.commander/estimates/` regardless of which clone the dashboard runs from; (2) calibration counts all completed tickets that have a size label OR canonical JSON, without invoking estimation again; (3) sprint-start estimator remains skipped; (4) existing tests pass + new cases above.
---
Rebuild calibration cache so historical sprints appear immediately. The incremental `calibration_cache.json` on disk is stuck at ~18 `processed` keys from the old narrow ingestion. Bump `_CALIBRATION_CACHE_VERSION` to 2 (invalidates old cache on first read) OR add `POST /api/maintenance/calibration/rebuild?project=<slug>` plus CLI `scripts/rebuild_calibration_cache.py --project <slug>` that: clears `processed`, `by_size`, and `points`; rescans all `sprint-*-state.json` under `.commander/sprints/` and `archive/` using the **new** size resolver from the prior ticket; writes fresh `calibration_cache.json`. Rebuild is idempotent and safe to run on UAT and PRD after deploy. Dry-run mode reports how many tickets would be added per size tier without writing.

Acceptance: (1) after deploy + one rebuild on UAT, Calibration scatter/table shows 100+ tickets (not ~18) matching completed sprint history; (2) rebuild on PRD produces consistent counts for the same project data; (3) subsequent GET `/analytics/calibration` incrementally adds only new tickets (no double-count); (4) pytest covers version bump invalidation and rebuild endpoint/script.
---
Refresh calibration cache automatically when a sprint finishes. Today cache refresh only happens on Analytics tab load (`_refresh_calibration_cache` inside `_compute_calibration`). Wire `_refresh_calibration_cache(project_root, configured_minutes)` into the existing sprint-finish path — when reconciliation marks a sprint finished, summary is posted, or `sprint-*-state.json` is finalized with all tickets done-equivalent — so calibration data is current before anyone opens Analytics. Must be cheap (incremental merge, not full rescan) and must not block finish-sprint UX (run inline only if fast, otherwise background task). Log one dashboard event when new samples are absorbed (`calibration_cache_updated` with counts).

Acceptance: (1) complete a sprint with sized tickets, do not open Analytics, then open Calibration — new tickets appear without manual rebuild; (2) finish-sprint latency does not regress noticeably (<500ms added on incremental path); (3) activity log or debug endpoint shows cache update occurred; (4) pytest covers hook invocation with a temp project root.
---
Calibration hygiene — mis-sizing rebuild, preflight parity, docs. (a) Fix `_rebuild_mis_sizing_history` in `server.py` — today it skips tickets without estimate JSON (same bug as calibration); use the shared size resolver from Phase 1. (b) When preflight auto-fix runs `estimate_issue.py` and applies a `size-*` label, assert canonical JSON exists at project-root afterward (if subprocess succeeded but JSON missing, log warning with issue number). (c) Document in `docs/features/` or architecture: single estimation at ticket create; sprint-start estimator off by default; canonical estimates path is `<project-root>/.commander/estimates/`; calibration reads JSON + mirror labels. (d) Optional Global Settings banner on Analytics Calibration tab when `processed` count is zero but finished sprint state files exist — “Calibration cache empty or stale — Rebuild” linking to maintenance action from ticket 2.

Acceptance: (1) mis-sizing history rebuild finds tickets with label-only sizes; (2) preflight estimate fix leaves canonical JSON, not label-only; (3) docs state clearly that sprint-start estimation is not required for calibration; (4) stale-cache banner appears only when data mismatch detected.
```

## Notes

- **Phase 1 is not “run estimation at sprint start.”** Per-ticket estimation
  already runs at create (`POST /api/tickets/create` → `_run_estimator_for_issue`)
  and on reviewer follow-ups (`_dispatch_estimator_for_followup`). Sprint-start
  `sprint_estimator` stays skipped.
- **Phase 1 fixes two things without a second Haiku pass:**
  - **Write:** JSON always goes to project-root `.commander/estimates/`
  - **Read:** calibration also accepts existing `size-*` labels + state estimates
- **Phase 2** is required after Phase 1 deploy — otherwise old 18-ticket cache
  persists until manually cleared.
- **Phase 3–4** are polish; can ship in the same sprint after Phase 1+2 land.
- UAT evidence: 266 done tickets in state files, 18 in cache; issues #819/#822
  have `size-S` on GitHub but no JSON in either estimates dir.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
