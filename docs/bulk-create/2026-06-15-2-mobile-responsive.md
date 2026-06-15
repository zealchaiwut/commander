# Mobile responsive pass — five core screens

**Date:** 2026-06-15
**Sprint label:** NEW
**Default labels:** frontend, enhancement
**Status:** drafted

## Notes

**Goal:** make the dashboard usable on a phone over Tailscale. Scope: Sprint Management,
Logs, Bulk Create, Running Sprint, Sprint History. All markup + inline CSS in
`apps/dashboard/static/project.html` (~26k lines). Frontend-only — ships on page refresh,
no server restart.

**Breakpoints:** phone `@media (max-width: 600px)` primary; `480px`/`560px` ultra-narrow;
tablet `700–1024px`; touch `@media (hover: none)` for 44px targets. Grid: 480, 520, 560,
600, 640, 680, 720, 768, 860.

**Rollout:** Sprint Management + Running Sprint first (live during runs), then History,
Logs, Bulk Create. 20 tickets, all S or M — independent, can span 2–3 sprints.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Sprint Management — stack sprint-card header on phones. In project.html, add @media (max-width:480px) so .smgmt-sprint-header uses flex-direction:column and align-items:flex-start so title and actions do not squeeze onto one line. Acceptance: at 375px and 480px no horizontal page scroll from the sprint card header; title and action row stack vertically; desktop layout at ≥1024px unchanged.
---
Sprint Management — wrap backlog filter pills on narrow screens. Add @media (max-width:620px): .bl-filter { flex-wrap:wrap; gap:6px; }, allow .bl-pill to shrink so the 6+ pill row does not overflow. Acceptance: at 375px and 600px filter pills wrap instead of overflowing; no horizontal page scroll; desktop unchanged.
---
Sprint Management — 44px tap targets for backlog header controls. Extend existing (hover:none) rules: min-height:44px on .bl-target, .bl-add-header-btn, .bl-select, .smgmt-bulk-est-btn. Acceptance: on touch devices (hover:none) all listed controls meet 44px minimum tap target; desktop mouse layout unchanged.
---
Sprint Management — truncate mini-rail and progress badges on phones. @media (max-width:600px): .hist-card-mini, .hist-progress { white-space:nowrap; max-width:80px; overflow:hidden; text-overflow:ellipsis; }. Acceptance: long badge text truncates with ellipsis at 375px/600px; no overflow breaking page width; desktop unchanged.
---
Logs — two-column chip groups on phones. @media (max-width:640px): .logs-filter-bar { display:grid; grid-template-columns:1fr 1fr; gap:8px; } cap chip-group width so ~15 filter chips do not sprawl horizontally. Acceptance: at 375px and 640px chip area uses two columns; no horizontal page scroll; desktop unchanged.
---
Logs — stack search row and view toggle on narrow screens. @media (max-width:620px): .logs-toolbar-row2 { flex-direction:column; }, .logs-search-wrap { max-width:100%; }, .logs-view-toggle full width. Acceptance: search and view toggle stack at 375px/620px; search input usable full width; desktop unchanged.
---
Logs — truncate run IDs and fail titles on phones. @media (max-width:600px): .logs-run-id, .logs-ticket-fail { max-width:80px; overflow:hidden; text-overflow:ellipsis; }. Acceptance: long run IDs and failure titles truncate at 375px/600px; no page-width overflow; desktop unchanged.
---
Logs — horizontal-scroll the raw monospace stream on phones. @media (max-width:640px): .logs-raw-stream { overflow-x:auto; }, .logs-raw-line { white-space:pre; } so code lines scroll inside the panel instead of breaking page width; style scrollbar if needed. Acceptance: at 375px/640px long log lines scroll horizontally within the stream panel; page body does not scroll horizontally; desktop unchanged.
---
Bulk Create — stack settings bar fields on phones. @media (max-width:600px): .bc-settings-bar { flex-direction:column; }, .bc-settings-field { width:100%; }, selects/inputs full width. Acceptance: settings fields stack at 375px/600px; no horizontal overflow; desktop unchanged.
---
Bulk Create — reflow draft-card header and actions on phones. @media (max-width:600px): .bc-card-head { flex-direction:column; align-items:flex-start; }, .bc-card-actions { flex-wrap:wrap; }. Acceptance: draft card title and actions stack/wrap at 375px/600px; no squeeze overflow; desktop unchanged.
---
Bulk Create — wrap estimate-badge progress entries on narrow screens. @media (max-width:560px): .bc-pg-entry { flex-wrap:wrap; }, .bc-pg-entry-badge { flex:1 1 auto; } so estimate badges stack instead of overflowing. Acceptance: at 375px/560px progress badges wrap; no horizontal page scroll; desktop unchanged.
---
Bulk Create — fix textarea and prompt-row overflow on phones. .bc-textarea { max-width:100%; }; @media (max-width:600px) .bc-prompt-row { flex-direction:column; } so handle, num, text, and actions stack. Acceptance: prompt rows and textarea fit viewport at 375px/600px; no horizontal page scroll; desktop unchanged.
---
Running Sprint — finer metrics-card wrapping on phones. @media (max-width:640px): .metrics { gap:6px; }, .metric { flex:1 1 auto; min-width:120px; }, .metric-label { font-size:9px; } so the 5-card strip (including 230px time card) does not overflow below 768px. Acceptance: metrics strip wraps cleanly at 375px/640px; no horizontal page scroll; desktop unchanged.
---
Running Sprint — wrap rail nodes and truncate titles on phones. @media (max-width:600px): .node { flex-wrap:wrap; }, .node-title { flex:1 1 100%; min-width:0; overflow:hidden; text-overflow:ellipsis; }, badges flex:0 1 auto. Acceptance: node titles truncate; badges wrap at 375px/600px; no page-width overflow; desktop unchanged.
---
Running Sprint — stack lane capacity dots and text on phones. @media (max-width:640px): .lane-capacity { flex-direction:column; gap:4px; }, .lane-dots { flex-wrap:wrap; }. Acceptance: lane capacity stacks at 375px/640px; dots wrap; desktop unchanged.
---
Running Sprint — inspector as bottom sheet on phones. @media (max-width:640px): .inspector { position:fixed; left:0; right:0; bottom:0; max-height:55vh; overflow-y:auto; } overlay instead of cramped sidebar; head actions wrap; .log-tabs scroll horizontally if needed. Acceptance: inspector usable as bottom sheet at 375px/640px; content scrolls inside sheet; page does not scroll horizontally; desktop sidebar layout unchanged.
---
Sprint History — stack history card header on phones. @media (max-width:600px): .hist-card-head { flex-direction:column; gap:8px; }, .hist-card-head-left/right { flex-basis:100%; } so title, badges, and actions do not crush onto one line. Acceptance: card header stacks at 375px/600px; no horizontal overflow; desktop unchanged.
---
Sprint History — wrap metrics row and est-bar on narrow screens. @media (max-width:640px): .hist-metrics { flex-wrap:wrap; gap:4px; }, .est-bar { flex:1 1 100%; }, .hist-badge { flex:0 1 auto; }. Acceptance: metrics and est-bar wrap at 375px/640px; desktop unchanged.
---
Sprint History — wrap action verbs and head buttons with tap targets. @media (max-width:600px): .hist-head-actions, .hist-verbs { flex-wrap:wrap; gap:6px; }, .hist-head-btn, .hist-verb { flex:1 1 auto; min-width:80px; min-height:44px; }. Acceptance: 5+ buttons wrap at 375px/600px; controls ≥44px on touch; desktop unchanged.
---
Sprint History — reflow issue-list rows and gantt overflow. @media (max-width:600px): .iss-row { flex-wrap:wrap; }, .iss-title { flex:1 1 100%; ellipsis on title; }, .iss-time { flex:0 0 100%; margin-left:0; margin-top:4px; }. Add overflow-x:auto to .hist-gantt if SVG exceeds width. Acceptance: issue rows stack at 375px/600px; gantt scrolls horizontally if wider than viewport; no page horizontal scroll; desktop unchanged.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
