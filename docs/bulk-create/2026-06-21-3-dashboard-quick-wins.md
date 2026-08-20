# Dashboard quick-wins — visual, low-logic UX batch

**Date:** 2026-06-21
**Sprint label:** NEW
**Default labels:** enhancement, frontend
**Status:** drafted

A batch of small, **verify-by-looking** dashboard improvements — deliberately low
on logic so they're easy to confirm without reading AC. Test bed for the lighter
review loop (skim the result, not the spec). Mostly `apps/dashboard/static/`
(project.html is disk-served → live on page refresh; `static/src/*` needs
`npm run build`). Two touch the Deploy tab backend (`routers/environments.py`).

**Conventions to reuse (don't reinvent):**
- BKK time helpers already exist (`fmtHHMM`, `_fmtStoppedAt` in project.html) —
  extend, don't duplicate.
- Status chips / badges already have CSS classes (`.slp-*`, `.smgmt-*`,
  READY TO MERGE / NEEDS MERGE / FAILED, etc.).
- Deploy run-state already comes from `environment_run_state`; sprint-running is
  already known via `/api/sprints/running-all` / `_all_sprints_running`.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add loading skeletons in place of "Loading…" text. Replace the bare "Loading…" / "Loading activity…" placeholders across the dashboard (Board, Running, History, Home, Logs) with lightweight CSS skeleton rows (shimmering grey blocks matching each list's row shape). Add one reusable `.skel` / `.skel-row` CSS (keyframe shimmer) and a tiny `skeleton(n)` JS helper that returns n placeholder rows; swap it into the existing "loading" branches of the render functions. No data/logic changes — purely the pre-data visual state. Verify by looking: opening any tab shows skeleton rows that are replaced by real content on load.

Acceptance: (1) Board/History/Running/Home show skeleton rows while fetching, not "Loading…"; (2) skeletons match the real row height so there's no layout jump; (3) prefers-reduced-motion disables the shimmer; (4) no console errors.
---
Add a card density toggle (compact / comfortable). Add a small toggle (icon button in the sub-nav or settings) that switches sprint cards + ticket rows between "comfortable" (current) and "compact" (tighter padding, smaller line-height). Implement as a `data-density` attribute on the board/history container + CSS overrides; persist the choice in localStorage and apply on load. No layout rewrite — only spacing/font-size via CSS. Verify by looking: toggle visibly tightens/loosens the cards and survives refresh.

Acceptance: (1) toggle switches density live; (2) choice persists across reload; (3) compact mode noticeably reduces vertical space without clipping text; (4) applies to both Board and History.
---
Add one-click copy buttons. Add small copy-icon buttons (ti-copy) that copy to clipboard via navigator.clipboard: the sprint label (on sprint card headers), the issue number `#N` (on ticket rows), and the log path (on Logs entries). Show a 1s "Copied" tooltip/checkmark on success; fall back to a hidden textarea + execCommand if clipboard API is unavailable. Reuse one `copyToClipboard(text, el)` helper. Verify by looking: click → checkmark flash → paste works.

Acceptance: (1) copy buttons on sprint label, issue #, and log path; (2) success shows a brief "Copied" affordance; (3) graceful fallback when clipboard API is blocked; (4) buttons are unobtrusive (appear on hover or as muted icons).
---
Show relative timestamps with absolute on hover. Add a `fmtRelative(iso)` helper ("just now", "2m ago", "3h ago", "2d ago") and apply it to the visible timestamps (sprint started/ended, history durations' end times, activity events, last-deploy). Each relative time has a `title` attribute with the absolute BKK time (reuse the existing Asia/Bangkok formatting). Auto-refresh the relative labels every 60s so they stay current. Verify by looking: timestamps read "2h ago", hover shows the exact BKK time.

Acceptance: (1) relative labels render across Board/History/Activity/Deploy; (2) hover tooltip shows absolute Asia/Bangkok time; (3) labels tick (e.g. "1m ago" → "2m ago") without a full re-render; (4) invalid/empty dates render nothing, not "NaN".
---
Add a status legend and tooltips for chips/badges. Add concise `title` tooltips to every status chip/badge (DRAFT, READY TO MERGE, NEEDS MERGE, FAILED, PARTIAL, COMPLETED, RUNNING, UAT, SIT, agent colors) explaining what each means and the action it implies. Add a small collapsible "Legend" (a `?` button near the board/history header) listing the states + their colors in one place. Pure presentation — strings + a static legend block; no state logic. Verify by looking: hover any chip → plain-language meaning; open Legend → the full key.

Acceptance: (1) each status chip has a meaningful tooltip; (2) a Legend popover lists all states with colors; (3) wording matches the lifecycle (UAT = done/awaiting sign-off; needs-rework, etc.); (4) legend is collapsed by default and remembers open/closed.
---
Show a "sprint running" badge on the Deploy tab. On each project's Deploy env cards (and/or the Deploy tab header), show a `● Sprint N running` badge when a sprint is active for that project — distinct from the env run-state (STOPPED/RUNNING is the deployed service; this is the sprint process). Source it from the existing `/api/sprints/running-all` (match by project) on the client; no new backend needed. Pulse the dot while running; hide when no sprint runs. Verify by looking: while a sprint runs, the Deploy tab clearly shows it, separate from the green/grey service state.

Acceptance: (1) badge appears on Deploy when a sprint runs for that project and clears when it ends; (2) visually distinct from the env STOPPED/RUNNING state; (3) shows the sprint label/number; (4) no extra polling beyond the existing running-all fetch.
---
Add a Logs panel to the Deploy tab. Add a collapsible "Logs" section to each Deploy env card that tails that environment's log (deploy/restart output and/or the env's server log). Backend: `GET /api/projects/{owner}/{repo_name}/environments/{env}/logs?tail=N` in routers/environments.py that returns the last N lines of the env's log file (resolve the path from the env config / known runtime log location; 404 if unknown, never read outside the project dir). Frontend: a "Logs" expander showing the tail in a monospace box with a refresh button (reuse the Activity log styling). Verify by looking: expand → recent deploy/server log lines appear.

Acceptance: (1) endpoint returns the last N lines for a configured env, scoped safely to the project (no path traversal); (2) Deploy card has a Logs expander rendering them in mono; (3) refresh button re-tails; (4) graceful empty/permission/missing-file states; (5) unit test for the tail endpoint (temp log file + traversal attempt rejected).
```

## Notes

- **All verify-by-looking** — open the tab, the change is visible; no AC reading
  required. This is the point.
- **Deploy split:** the **sprint-running badge** is client-only (reuse
  running-all). **Deploy logs** is the only one with a real (tiny) backend
  endpoint — keep it read-only + path-safe.
- **Deploy model:** project.html / `static/*.html` go live on refresh; anything
  under `static/src/` needs `npm run build`. Note which per ticket in the PR.
- **Low collision risk:** these touch presentation, not lifecycle/state — safe to
  run alongside Sprint 94 (composite key) and the Definition-of-Ready work later.
- **Next:** the **Sprint Intent Map** (3-box visual flow per ticket) is the
  bigger visual-verification feature — its own sprint once you pick the flavor.
