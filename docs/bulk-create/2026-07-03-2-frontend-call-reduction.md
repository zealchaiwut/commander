# Frontend API-Call Reduction — Polling, Timers, N+1

**Date:** 2026-07-03
**Sprint label:** NEW
**Default labels:** frontend, performance
**Status:** drafted

Source: 2026-07-03 four-agent full review (frontend call-pattern audit against
develop @ 7520d393). Measured steady-state: project page ~26 calls/min idle,
~86 calls/min with a running sprint, home page 17 calls per load (N=5
projects). Achievable: ~6-8/min idle, ~10-26/min running, ~0 backgrounded,
3-4 calls per home load. Line numbers reference project.html/home.html at
7520d393 — re-locate by function name if drifted.

## Prompts

```
Pause all dashboard polling when the tab is hidden (visibilitychange guard).

There is zero document.hidden / visibilitychange handling in project.html, home.html, home-preview.html, diagnostics.html, or static/src/ — every timer keeps firing on a backgrounded tab: the 2s live-board poll (_smgmtLivePollTick, project.html:19258), both 30s sidebar/home timers (:12686, :13670), the 60s status tab poll (:30878), the 15s logs poll (:26913), the 5s inspector poll (:18273), home-preview's 60s loadStaleDocs + 30s pollHealth (home-preview.html:1548/1550), and diagnostics' 30s health poll (diagnostics.html:1166). A backgrounded project tab burns ~26-86 calls/min for nothing.

Fix: add a small shared helper (e.g. static/src/shell/visibility.js or an inline util in each page that lacks bundle access) that wraps setInterval-based pollers: pause on document.hidden, resume on visible with one immediate refresh so the page catches up instantly. Apply it to every timer listed above. SSE EventSource connections stay open (they are push, not poll).

AC must cover: with the tab hidden, no polling requests are issued (assert via a test hook or instrumented fetch counter); on becoming visible each poller fires immediately once then resumes its interval; SSE connections unaffected; all five pages covered.
---
Deduplicate the two overlapping 30s refresh loops on the project page and delete the dead running-all fetch.

Three defects in one area. (a) project.html:12657 loadHomeData destructures runningResp from Promise.all but the variable is never referenced afterwards (confirmed :12653-12679) — a /api/sprints/running-all fetch on every load and every 30s tick whose result is discarded (the endpoint itself is live elsewhere via run-controls.js/board-render.js; only this call site is dead). (b) _snavRefreshAll() runs twice per 30s window: once from loadHomeData (:12672, on timer A :12686) and once from timer B (:13670) — each pass loops every other sidebar project firing /api/sprint-nav-status (:26437), so 2×(N-1) calls/30s. (c) Timer A (/api/home + running-all + _snavRefreshAll) and timer B (snavRefresh + _snavRefreshAll + _milestoneRefresh) are two independent 30s intervals doing overlapping work.

Fix: delete the dead running-all fetch from loadHomeData; merge timers A and B into a single 30s refresh loop that calls each refresher exactly once; make /api/home refresh event-driven off the existing /events SSE (:13912 — it already pushes sprint_finished/sprint_reconciled) with the 30s tick as fallback only, or at minimum ensure only one loop fetches it. Also dedupe /api/sprint-nav-status for the current repo between snavRefresh (:26462, 30s) and the status tab's statusRefresh (:30888, 60s) via a shared short-TTL client cache.

AC must cover: running-all no longer fetched by loadHomeData; exactly one 30s interval exists; _snavRefreshAll executes once per window; nav-status for current repo fetched once per window regardless of open tab; idle project-page call rate drops from ~26/min to ≤10/min (assert via instrumented fetch counter in a test page or documented manual measurement).
---
Drive the live sprint board from the existing SSE stream instead of the 2s snapshot poll.

While a sprint runs, _smgmtLivePollTick (project.html:19258, setInterval 2000) fires /api/sprint-status plus /api/sprints/{label}/live per running sprint every 2s — ~60 calls/min for one sprint. A per-sprint SSE stream already exists (/api/sprints/{label}/live/stream, routers/sprint_live.py:726, consumed at project.html:27672) but only the logs inspector uses it; the board re-polls snapshot data alongside it. The inspector additionally polls /logs/tail or dispatch-log every 5s (:18273/:18307, plus an early-run fallback fetch :18326) duplicating the same stream's log_line events.

Fix: subscribe the board to the live/stream EventSource — have the server push the snapshot payload (or a delta) the board currently polls, either as a new snapshot event type on the existing stream or by reusing current events; keep a slow poll (≥15s) as reconnect fallback only. Wire the inspector's log pane to the same stream's log_line events and drop its 5s poll. If SSE plumbing for board snapshots proves too invasive for one ticket, the fallback scope is: raise the live poll interval 2s→5s and fold /api/sprint-status + per-sprint /live into one batched request — but state in the PR which scope shipped.

AC must cover: with one running sprint and the board open, steady-state polling is ≤4 calls/min (SSE scope) or ≤26/min (fallback scope); board metrics still update within 5s of an agent event; inspector log lines still stream; stream disconnect falls back to polling and recovers.
---
Batch the home-page per-project fan-out: 17 requests per load → 3-4.

home.html fires /api/home (:886) + /api/brief/daily (:918), then loops every project firing three more each (:953-:959): /api/projects/{slug}/brief/summary (loadProjectSummary :1353), /api/home/milestone (loadMilestone :966), and /api/projects/{slug}/todos (project-todo.js:168 via CommanderTodo.mount). For 5 projects that is 17 requests per page load. The daily-brief artifact already carries per-project structure, and /api/home already aggregates per-project data server-side.

Fix: (1) embed per-project brief summary and milestone into the /api/brief/daily response (or /api/home — pick the one whose service already loads the underlying data) so the loop reads from the payload instead of fetching; (2) add a batch todos endpoint (e.g. GET /api/todos?projects=a,b,c returning a slug-keyed map) and have CommanderTodo.mount accept preloaded data; keep the per-project endpoints for the single-project page. (3) While in loadHomeData: /api/projects (:13544 _prefetchFullRepo) and /api/home (:12656) are both fetched on project-page load and both paint the sidebar — add the missing repo field to /api/home project payloads and drop the separate /api/projects prefetch.

AC must cover: home page load issues ≤4 API requests for N projects (assert with instrumented fetch counter); per-project data still renders identically; single-project endpoints still work; project page no longer double-fetches the project list.
---
Small polling tuning: gh-auth login poll 600ms→2s; session-cache the stable config endpoints.

(a) project.html:25932 _gssGhAuthPollTimer polls /api/gh-auth/login/status every 600ms during device login — 100 calls/min for a flow where GitHub's device-code round-trip takes seconds; raise to 2s (device login UX is unaffected). (b) Stable per-session data is refetched: /api/environment (:10027), /api/version (:12565), and /api/settings fetched independently at :25836, :31509 and :31566. Add a tiny client-side session cache (module-level promise memo) so each is fetched once per page session, with explicit invalidation after PUT /api/settings.

AC must cover: login poll interval 2s and login flow still completes; environment/version/settings each fetched at most once per page load (settings refetched after a save); no stale-settings regression after PUT.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
