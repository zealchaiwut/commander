# perf-coach — Training thresholds settings + run-view Zone-2 from prefs

**Project:** perf-coach
**Date:** 2026-06-17
**Sprint label:** NEW
**Default labels:** frontend, enhancement
**Status:** drafted

## Notes

**Goal:** ship the UI for the athlete-thresholds backend (PR #571) and stop the run
view from using a hardcoded Zone-2 band.

**Backend already exists (PR #571) — do NOT add endpoints/migrations:**
- `GET /api/user-preferences` → `{ "row": {...}, "defaults": {...} }`. `row` fields are
  null until saved; prefill the form from `row[field] ?? defaults[field]`.
- `PATCH /api/user-preferences` accepts any of (with server validation / 422):
  `ftp_w` (50–600), `threshold_hr` (100–220), `threshold_pace_seconds_per_km` (180–540),
  `max_hr` (120–230), `zone2_hr_min` (80–200), `zone2_hr_max` (80–210),
  `weekly_zone2_target_min` (0–2000).
- Defaults: FTP 280 W · threshold HR 170 · threshold pace 270 s/km (4:30) · max HR 190 ·
  Zone 2 130–155 · weekly target 150 min.

**Frontend:** `frontend/pages/settings.html` (sections via the avatar→Settings nav, e.g.
Profile / Security / Integrations) and `frontend/js/training-log.js` (`renderRunView`,
which already has `ZONE2_HR_MIN=130 / ZONE2_HR_MAX=155` constants). No bundler — ships on
page refresh, no server restart.

**Zone-2 habit (ticket 4):** the habit pipeline already supports this — `POST /api/habits`
accepts `tracking_type="weekly_minutes"`, `auto_fill_source="workout.zone2_minutes"`,
`weekly_target`, `unit`. `auto_fill_source=workout.zone2_minutes` already aggregates
`workout.zone2_minutes` onto the habits graph. So ticket 4 is an opt-in button that POSTs a
preconfigured Zone-2 habit, seeded with the weekly target from prefs (editable afterwards
via normal habit edit — no ongoing sync).

**Rollout:** 4 tickets, all S/M, independent.

## Prompts

Paste into the Bulk Create textarea. `---`-separated.

```
perf-coach Settings — add a "Performance thresholds" section/card to frontend/pages/settings.html following the existing settings-section pattern (same nav/section mechanics as Profile/Security/Integrations). Fields, each a labelled number input prefilled from GET /api/user-preferences using row[field] ?? defaults[field]: FTP / critical power (W, ftp_w), Threshold HR (bpm, threshold_hr), Max HR (bpm, max_hr). On Save, PATCH /api/user-preferences with only the changed fields; show a success/error feedback line; surface backend 422 field messages inline. Acceptance: section visible from Settings nav; the three fields load prefilled (defaults when no saved value); editing + Save persists (visible after reload); an out-of-range value (e.g. ftp_w 9999) shows the server's 422 message and does not save; valid save shows success.
---
perf-coach Settings — add Threshold pace + Zone 2 + weekly target to the Performance thresholds section in settings.html/js. Threshold pace input as mm:ss per km (convert to/from threshold_pace_seconds_per_km on load/save). Zone 2 HR band as two inputs (zone2_hr_min, zone2_hr_max) with a client check that min < max. Weekly Zone 2 target in minutes (weekly_zone2_target_min). All prefilled from row ?? defaults; Save PATCHes only changed fields and shows inline 422s. Acceptance: pace shows as m:ss (e.g. 4:30) and round-trips to seconds correctly; zone2 min/max + weekly target load prefilled and persist after reload; min ≥ max is blocked client-side with a message; server 422 (e.g. pace out of 180–540) surfaces inline; desktop + mobile layouts usable.
---
perf-coach Run view — read the Zone 2 HR band from user preferences instead of the hardcoded ZONE2_HR_MIN/MAX constants. In frontend/js/training-log.js renderRunView, fetch GET /api/user-preferences once (cache it) and use row.zone2_hr_min ?? defaults.zone2_hr_min and row.zone2_hr_max ?? defaults.zone2_hr_max for per-lap Zone 2 detection and the "(HR X–Y …)" note; keep the existing 130/155 module constants only as the fallback if the fetch fails. Acceptance: opening a run highlights Zone 2 laps using the user's saved band (verify by changing the band in Settings and reopening a run — the tinted laps + Z2 pills + note range update accordingly); with no saved prefs the defaults (130–155) apply; a failed prefs fetch falls back to the constants without breaking the view.
---
perf-coach Habits — add a "Track Zone 2 in habits" opt-in button that provisions a Zone 2 habit reusing the existing habits graph. Place the button in Settings (the Performance thresholds section) or the Habits page. On click: GET /api/user-preferences for the weekly target, then POST /api/habits with name "Zone 2", tracking_type "weekly_minutes", auto_fill_source "workout.zone2_minutes", unit "min", weekly_target = row.weekly_zone2_target_min ?? defaults.weekly_zone2_target_min (seed only; editable afterwards via normal habit edit). Guard against duplicates: if a habit with auto_fill_source "workout.zone2_minutes" already exists, disable/hide the button (or no-op with a message) and link to it. After create, show success + link to /habits. Acceptance: clicking the button once creates a Zone 2 weekly_minutes habit seeded with the prefs weekly target; it appears on the habits graph auto-filled from workout.zone2_minutes (target vs actual); clicking again does not create a duplicate; editing the habit's weekly_target later works and is not overwritten by prefs.
```
