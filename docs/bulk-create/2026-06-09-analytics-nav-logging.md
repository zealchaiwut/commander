# Analytics tabs, nav moves, global notes, analytics calcs, agent logging

**Date:** 2026-06-09
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

Seven items from a working session: finish the analytics tab set, reshuffle nav,
make Notes a global left-nav feature with local storage, implement the analytics
calculations, and fix the activity-log gaps (issue links, agent roles, label
edits, sprint lifecycle).

## Codebase findings (verified before drafting)

- **Agent log** (`/api/agent-event`, server.py): events store `role` in `detail`
  (parsed from the agent `name` on `·`) but the activity feed renders the
  **session UUID** as the target. **No issue number** is captured anywhere.
- **Label changes**: `_transition_safe()` in sprint_manager.py applies
  in-progress/SIT/UAT/needs-rework but **never calls `record_event`** → label
  edits are invisible in the activity feed.
- **Sprint lifecycle**: old `run_sprint` emits a `sprint_run` event but with
  `project="dashboard"` (wrong scope — won't show under the project's feed);
  **`finish_sprint` and `rerun_sprint` emit no events at all.**
- **Analytics data** lives in `<project>/.commander/sprints/sprint-N-status.json`
  (per-ticket: `tokens_in`, `tokens_out`, `coder_started_at`,
  `coder_finished_at`, `tester_started_at`, `tester_finished_at`, `category`,
  `failure_reason`, `dispatch_level`, `status`) and
  `<project>/.commander/estimates/issue-N.json` (`estimated_size`, `minutes`).
  The calibration endpoint currently reads Neon (`sprint_tickets`, empty when
  Neon is disabled) instead of these files — which is why it shows no data.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Add Status and Trends as analytics sub-tabs. The Analytics tab currently has two sub-tabs (Calibration, Metrics) inside pane-metrics in apps/dashboard/static/project.html, driven by anlShowTab(). Add two more sub-tabs so the bar reads: Calibration | Metrics | Status | Trends. (1) Status: move the existing Status view (pane-status / statusInit) to render as a sub-tab inside the Analytics page, styled to match the analytics mock cards (anl-card / anl-card-head / anl-card-body). Remove Status from the Analytics dropdown group in the nav (it becomes a sub-tab, not a top nav entry). (2) Trends: re-introduce the sprint-trend charts that were removed — Sprint Velocity, Failure Rate Trend, Avg Dispatch Duration (the #476 charts that fetch /api/metrics/sprints and render via Chart.js into metrics-velocity-chart / metrics-failure-chart / metrics-duration-chart). anlShowTab('status') and anlShowTab('trends') must init/fetch their data on first open. Acceptance: the Analytics tab shows 4 sub-tabs; Status and Trends each render with the same card design as the mock; switching tabs lazy-loads each one's data.
---
Move Sprint History into the Sprint Mgmt tab as a sub-view toggle, and remove it from the More menu. Sprint Mgmt (pane-sprint-mgmt) is the kanban board; Sprint History (pane-sprint-history / _smgmtLoadSummaries) lists finished-sprint summaries. Add a view switch at the top of the Sprint Mgmt tab — e.g. a segmented control "Board | History" — that toggles between the board and the sprint-history list within the same tab, without a full page nav. Remove the "Sprint history" entry from the More dropdown in the nav (apps/dashboard/static/project.html). Preserve deep-linking: /project/{slug}/sprint-mgmt opens Board; keep history reachable via the toggle. Acceptance: Sprint Mgmt has a Board/History switch; History shows the same summaries it does today; "Sprint history" no longer appears in More.
---
Make Notes a global left-sidebar feature with local persistence. Today Notes is a per-project tab. Move it to the left navigation sidebar (the aside in project.html / the dashboard shell), placed directly ABOVE the Settings item, and make it global (not per-project). Clicking it opens a Notes view/panel. Persist notes to LOCAL storage first: add a backend store at .commander/notes.json (single global notes document) with GET /api/notes and PUT /api/notes (read/write the markdown/text body); the frontend autosaves on edit (debounced) and loads on open. Do not use Neon — mirror the settings_repo JSON-fallback pattern so it works with Neon disabled. Remove Notes from the project More dropdown. Acceptance: a Notes item sits above Settings in the left sidebar; editing notes persists across reloads and server restarts via .commander/notes.json; notes are shared across all projects (global).
---
Implement the analytics calculations end to end, sourced from local sprint state + estimate files (not Neon). For each metric below, compute from <project>/.commander/sprints/sprint-N-status.json and .commander/estimates/issue-N.json, honoring the scope filter (since/until/sprint). METRICS PANEL: (a) First-pass rate = completed tickets that reached UAT with zero tester rejections ÷ total completed; infer rejections from status history / category / failure_reason, and ALSO add a per-ticket tester_attempt_count to the status file going forward for an exact count. (b) Rework rate = tickets tester-rejected at least once ÷ total; plus count of tickets needing 2+ rounds. (c) Avg duration by agent = mean(coder_finished_at − coder_started_at) and mean(tester_finished_at − tester_started_at); breakdown by estimated size via the estimate files. (d) Throughput = avg tickets per sprint, and avg sprint length = max(finished_at) − min(started_at) per sprint. (e) Cost/sprint and (f) cost/ticket = (tokens_in × in_price + tokens_out × out_price) using MODEL_PRICE_MAP; join the token_usage table for per-ticket model_name (status files only store token counts, not the model). CALIBRATION PANEL: rewire /api/projects/{slug}/analytics/calibration to build points (estimated_minutes from estimate file, actual_minutes from coder+tester elapsed, estimated_size) and by_size aggregates (count/min/avg/max + configured_minutes from settings) from the status + estimate files instead of Neon. TRENDS: velocity = tickets completed per sprint over time; failure-rate = failed ÷ total per sprint. Acceptance: with real finished-sprint data present, every analytics card and the scatter/table populate; cards show the empty state only when no sprints match the scope.
---
Make the agent activity log link to the issue and name the agent role. In /api/agent-event (server.py) the agent_started/agent_finished events record target=session_id (a UUID) and role inside detail (parsed from the agent name). Thread the issue number through: when sprint_manager dispatches a coder/tester/estimator/reviewer it should include the issue number and role in the agent name/payload (e.g. name "coder·648·<session>") so the event captures issue_num and role; persist issue_num on the event. In the activity-log frontend (Logs → Activity / evl rendering), render the line as "<role> <action> #<issue>" e.g. "coder finished #700" with #700 as a clickable GitHub issue link, instead of the raw UUID. Fall back to the UUID only when issue/role are absent. Acceptance: agent rows in the activity log read like "tester finished #700" with a working issue link and the agent role shown; the four roles coder/tester/estimator/reviewer are all labeled.
---
Log label changes (add/remove) to the activity feed. _transition_safe() in sprint_manager.py changes ticket labels (in-progress, SIT, UAT, needs-rework) but emits no activity event. Emit a structured event for every label transition — actor = the agent role making the change, target = issue number, detail = {from_label, to_label, added: [...], removed: [...]}. Post it to the dashboard activity feed the same way agent events are recorded (POST to the dashboard event endpoint, or record_event with source="agent"). Render it in the activity log as e.g. "coder moved #700 → SIT" / "sprint_manager labeled #700 needs-rework". Acceptance: applying or removing a label during a sprint produces an activity-log entry naming the issue, the label change, and who made it.
---
Emit activity-log events for sprint start, finish, and rerun, scoped to the correct project. Verified gaps: the old run_sprint emits sprint_run with project="dashboard" (wrong scope, so it never shows in the project's feed), and finish_sprint and rerun_sprint emit nothing. Fix: (1) the managed sprint run (POST /api/sprints/run) emits a sprint_started event with project = the actual target repo; (2) finish_sprint (POST .../sprints/{label}/finish) emits sprint_finished with the project and summary issue link; (3) rerun_sprint (POST .../sprints/{label}/rerun) emits sprint_rerun naming the new sub-sprint label (e.g. sprint-52.1). All three via _emit_dashboard_event with the real project (not the literal "dashboard"). Acceptance: starting, finishing, or rerunning a sprint each adds a correctly project-scoped row to that project's activity log.
```

## Item 4 — analytics calculations: what we compute & data availability

| Metric | Formula | Data source | Have data? |
|--------|---------|-------------|-----------|
| First-pass rate | passed-first-try ÷ completed | status.json category/failure history | ⚠️ partial — infer rejections; add `tester_attempt_count` for exact |
| Rework rate | rejected≥1 ÷ completed (+2-round count) | status.json | ⚠️ partial — same as above |
| Avg duration / agent | mean(coder/tester finished−started); by size | status.json timestamps + estimate files | ✅ yes |
| Throughput | tickets/sprint; sprint length | status.json | ✅ yes |
| Cost / sprint & ticket | tokens × MODEL_PRICE_MAP | status.json tokens + token_usage model | ⚠️ partial — model from token_usage join |
| Calibration (est vs actual) | est (estimate file) vs actual (elapsed) | estimate + status files | ✅ yes — just rewire off Neon |
| Velocity trend | completed per sprint over time | status.json per sprint | ✅ yes |
| Failure-rate trend | failed ÷ total per sprint | status.json | ✅ yes |

**Gaps to close (in ticket 4):** add per-ticket `tester_attempt_count` to the status file for exact first-pass/rework; join `token_usage.model_name` for exact cost. Everything else is already in the local status + estimate files — the calibration endpoint just needs to read those instead of empty Neon.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
