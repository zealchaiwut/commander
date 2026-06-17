# perf-coach — Sync UX: progress notice, dual source tags, per-source last-sync

**Project:** perf-coach
**Date:** 2026-06-17
**Sprint label:** NEW
**Default labels:** frontend, enhancement
**Status:** drafted

## Notes

**Goal:** make sync legible — show progress while syncing/reconciling, show BOTH source
tags on a merged workout, and surface per-source last-sync + a manual sync.

**What already exists (don't rebuild):**
- A global **sync-status bar** in `frontend/js/nav.js` (`#sync-status-bar`) with
  running/success/error states, a spinner, and phase labels
  (`pulling_strava` → `pulling_stryd` → `reconciling`). It polls `GET /api/sync/status`.
- `GET /api/sync/strava/latest` and `GET /api/sync/stryd/latest` (last-sync summary).
- `POST /api/strava/sync` and `POST /api/stryd/sync` (start a sync; 202; 409 if running).
- Reconcile sets `workout.source` to `"strava"`, `"stryd"`, or `"strava,stryd"` and links
  `strava_activity_pk` / `stryd_activity_pk`.

**Known bug (ticket 2):** in `frontend/js/training-log.js`, `isStravaWorkout()` does
`workout.source === 'strava'` (exact match), so a merged `source="strava,stryd"` workout
fails the check and only the Stryd badge shows. Tags should reflect BOTH sources.

**Frontend:** `frontend/js/training-log.js` (list rows + `renderRunView`), `frontend/js/nav.js`.
No bundler — ships on page refresh.

**Rollout:** 3 tickets, all S/M, independent.

## Prompts

Paste into the Bulk Create textarea. `---`-separated.

```
perf-coach Sync notice — make the global sync-status bar reliably visible while a new user's first sync + reconcile runs. In frontend/js/nav.js ensure the #sync-status-bar polling of GET /api/sync/status starts on every authenticated page load (not only after a manual trigger), so a sync started from Settings shows app-wide. Confirm/soften the phase copy: "Syncing Strava…", "Syncing Stryd…", "Reconciling activities…", and a success line like "Sync complete — N workouts updated" before auto-hiding. Acceptance: starting a sync from Settings shows the running bar with the correct phase label on any page (training log, home); the bar transitions pulling_strava → pulling_stryd → reconciling → success; it auto-hides on success and is dismissible; no console errors when no sync is running.
---
perf-coach Workout tags — show BOTH Strava and Stryd badges on a merged workout (source contains both). Backend: in _workout_list_dict (backend/main.py) expose has_strava and has_stryd booleans derived from source membership and the *_activity_pk links (has_strava = 'strava' in source or strava_activity_pk is not None; has_stryd = 'stryd' in source or stryd_activity_pk is not None). Frontend: in training-log.js list rows use w.has_strava / w.has_stryd for the source badges instead of the exact-match isStravaWorkout()/is_stryd_synced, and fix isStravaWorkout to treat 'strava' as a substring of source. The run-view header (renderRunView) already substring-checks source — keep it. This is tag-presence only; do not change which source's metric values are used. Acceptance: a workout with source "strava,stryd" shows BOTH the St and Stryd badges in the training-log list AND the detail header; a strava-only or stryd-only workout shows just its one badge; a manual workout still shows the manual badge; no change to displayed metric values.
---
perf-coach Sync controls — compact per-source last-sync + manual sync. Add a small sync chip to the training-log page header (where the old sync buttons were) showing each source's last sync from GET /api/sync/strava/latest and GET /api/sync/stryd/latest, e.g. "Strava · 2h ago · Stryd · 1h ago", with relative-time formatting and "Never" when null. Add a single "Sync" button that POSTs /api/strava/sync and /api/stryd/sync (ignore 409 = already running) and lets the existing global sync-status bar show progress; on completion refresh the last-sync labels. Keep it compact (one row, small text). Also replace the run-view "Source & sync" strip's faked "last sync HH:MM" (currently derived from start_time) with the real per-source last-sync values. Acceptance: the header chip shows real last-sync times per source (relative, "Never" when unsynced); clicking Sync starts both syncs and the global status bar reflects progress; last-sync labels refresh after the sync; the run-view source strip shows real last-sync per source, not the workout start time; 409 (already running) does not error.
```
