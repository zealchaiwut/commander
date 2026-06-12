# Settings consolidation + machine-local timezone

**Date:** 2026-06-10
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

From a settings cleanup pass. The estimation-removal part of that pass shipped
directly (Global Settings no longer duplicates the project estimation config).
These three are larger and become tickets.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Move Global Settings onto the left sidebar as a slide panel, not a modal. Today the left sidebar has a "Settings" link that opens a project-preferences slide panel, plus a gear icon in the footer that opens Global Settings as a centered modal. Consolidate: rename the sidebar "Settings" link to "Global Settings" and have it open Global Settings as a slide panel (reuse the existing slide-panel pattern, not a centered modal overlay). Remove the gear icon from the sidebar footer. IMPORTANT: the current preferences panel holds the theme toggle (light/dark/system) and the board auto-refresh interval; these must be preserved, so move them into the Global Settings panel (for example a small Appearance and Refresh section at the top) so the theme control is never lost. Leave the project Settings tab in the project view unchanged. Acceptance: the sidebar shows a single "Global Settings" entry that opens a slide panel containing the global config (agent models, defaults, secrets, settings sync) plus the theme toggle and auto-refresh; the gear icon is gone; the old centered modal is no longer used; switching theme still works from here.
---
Show the last-saved datetime in Global Settings. settings_repo already stamps updated_at when a setting is written, and the JSON fallback store should record a saved-at time too. Expose a last-saved timestamp for the global scope from GET /api/settings, and display "Last saved: <time>" in the Global Settings save bar, refreshing it after each successful save. Render the time in the machine's local timezone. Acceptance: Global Settings shows when it was last saved in local time, and the value updates immediately after I save a change.
---
Render all user-facing datetimes in the machine's local timezone. Commander currently shows timestamps in UTC or mixed zones, which is confusing when operating from a non-UTC timezone (for example Asia/Bangkok). Make every datetime shown to the user render in the machine's local timezone, while keeping stored and transmitted values as UTC ISO strings. Add one small shared frontend formatter and route the datetime displays through it: the activity log timeline, sprint start and elapsed times, the Deploy tab last-deploy and the settings-sync last-synced, project last-activity, and any other visible timestamps (about 31 frontend spots). For the few server-rendered datetime strings, format in local time at render or send ISO and let the frontend format. Do not change how timestamps are stored. Acceptance: no UTC time is shown to the user anywhere; every displayed datetime reads in the machine's local timezone; stored data and APIs still use UTC.
```

## Notes

- **Already shipped** (not in this batch): removing the Estimation Defaults card
  from Global Settings, since it duplicated the project Settings estimation
  config. PR on `design/analytics-nav-flatten`.
- The consolidation ticket's sharp edge: the **theme toggle lives in the panel
  being replaced** — preserve it, or the project view loses theme switching.
- Timezone: storage stays UTC; only display changes. A single shared formatter
  keeps it consistent and avoids re-introducing UTC renders later.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
