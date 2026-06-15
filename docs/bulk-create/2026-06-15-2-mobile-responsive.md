# Bulk Create — Mobile Responsive Pass (5 core screens)

> **Goal:** make the dashboard usable on a phone (iPad-remote + phone over
> Tailscale is the whole point). Focus the five screens you live in:
> **Sprint Management · Logs · Bulk Create · Running Sprint · Sprint History.**
>
> **Sizing:** every ticket is **S or M** — each is a scoped CSS change (one
> component family, one or two breakpoints). No XL "redesign the page" tickets.
>
> All markup + inline CSS lives in `apps/dashboard/static/project.html`
> (~26,865 lines). Frontend-only → ships on page refresh, **no server restart**.

## Breakpoint convention (use these — already present in the file)

- Phone: `@media (max-width: 600px)` (primary), `480px`/`560px` for ultra-narrow.
- Tablet: `700–1024px` (sidebar shrink already handled).
- Touch: `@media (hover: none)` for tap-target sizing.
- Stick to a 40px grid: `480, 520, 560, 600, 640, 680, 720, 768, 860`.

## Definition of done (put in each issue's AC)

- No horizontal scroll of the page at **375px** and **600px** widths.
- All interactive controls ≥ **44px** tap target on touch.
- Long text truncates (`text-overflow: ellipsis`) instead of overflowing.
- Grids/rows collapse to a single column where they'd squeeze.
- Verify at 375 / 600 / 768 / 1024px. Desktop (≥1024px) layout unchanged.

---

## Screen 1 — Sprint Management (board)

**Now:** has `@media` at 699/860/720px (sidebar hide, backlog grid → 1 col,
filter gaps) + `(hover:none)` checkbox sizing. Sprint cards, capacity bars,
filter pills, mini-rail badges have no mobile rules.

### SM-MB-1 — Stack sprint-card header on phones (S)
`@media (max-width:480px)` → `.smgmt-sprint-header { flex-direction:column;
align-items:flex-start; }` so title + actions don't squeeze onto one line.

### SM-MB-2 — Wrap backlog filter pills (S)
`@media (max-width:620px)` → `.bl-filter { flex-wrap:wrap; gap:6px; }`, allow
`.bl-pill` to shrink. Stops the 6+ pill row from overflowing.

### SM-MB-3 — 44px tap targets for backlog header controls (M)
Extend `(hover:none)` → `min-height:44px` on `.bl-target`, `.bl-add-header-btn`,
`.bl-select`, `.smgmt-bulk-est-btn`.

### SM-MB-4 — Truncate mini-rail / progress badges (S)
`@media (max-width:600px)` → `.hist-card-mini`, `.hist-progress`
`white-space:nowrap; max-width:80px; overflow:hidden; text-overflow:ellipsis`.

---

## Screen 2 — Logs

**Now:** only `@media (max-width:768px)` (toolbar padding, search → 100%,
search-icon hidden). Chip groups, activity rows, raw stream lack mobile rules.

### LOG-MB-1 — Two-column chip groups on phones (M)
`@media (max-width:640px)` → `.logs-filter-bar { display:grid;
grid-template-columns:1fr 1fr; gap:8px; }`; cap chip-group width so the ~15
filter chips don't sprawl.

### LOG-MB-2 — Stack search + view toggle (S)
`@media (max-width:620px)` → `.logs-toolbar-row2 { flex-direction:column; }`,
`.logs-search-wrap { max-width:100%; }`, `.logs-view-toggle` full width.

### LOG-MB-3 — Truncate run IDs / fail titles (S)
`@media (max-width:600px)` → `.logs-run-id`, `.logs-ticket-fail`
`max-width:80px; overflow:hidden; text-overflow:ellipsis`.

### LOG-MB-4 — Horizontal-scroll the raw/monospace stream (M)
`@media (max-width:640px)` → `.logs-raw-stream { overflow-x:auto; }`,
`.logs-raw-line { white-space:pre; }` so code lines scroll instead of breaking
the page width. Style the scrollbar.

---

## Screen 3 — Bulk Create

**Now:** only `@media (max-width:480px)` (tips panel → 1 col). Settings bar,
draft cards, estimate badges, textarea row controls lack mobile rules.

### BC-MB-1 — Stack settings fields (S)
`@media (max-width:600px)` → `.bc-settings-bar { flex-direction:column; }`,
`.bc-settings-field { width:100%; }`, selects/inputs full width.

### BC-MB-2 — Reflow draft-card header + actions (S)
`@media (max-width:600px)` → `.bc-card-head { flex-direction:column;
align-items:flex-start; }`, `.bc-card-actions { flex-wrap:wrap; }`.

### BC-MB-3 — Wrap estimate-badge progress entries (M)
`@media (max-width:560px)` → `.bc-pg-entry { flex-wrap:wrap; }`,
`.bc-pg-entry-badge { flex:1 1 auto; }` so estimate badges stack, not overflow.

### BC-MB-4 — Textarea + prompt-row overflow (S)
`.bc-textarea { max-width:100%; }`; `@media (max-width:600px)` →
`.bc-prompt-row { flex-direction:column; }` (handle / num / text / actions stack).

---

## Screen 4 — Running Sprint

**Now:** `@media` at 560px (metrics → 2-col) + 620px (lanes → 1 col) +
reduced-motion. Rail nodes, inspector, lane capacity lack mobile rules.

### RUN-MB-1 — Finer metrics-card wrapping (M)
`@media (max-width:640px)` → `.metrics { gap:6px; }`, `.metric { flex:1 1 auto;
min-width:120px; }`, `.metric-label { font-size:9px; }`. The 5-card strip
(incl. 230px time card) currently overflows below 768px.

### RUN-MB-2 — Make rail nodes wrap + truncate (M)
`@media (max-width:600px)` → `.node { flex-wrap:wrap; }`, `.node-title { flex:1 1
100%; min-width:0; overflow:hidden; text-overflow:ellipsis; }`, badges
`flex:0 1 auto`.

### RUN-MB-3 — Stack lane capacity dots/text (S)
`@media (max-width:640px)` → `.lane-capacity { flex-direction:column; gap:4px; }`,
`.lane-dots { flex-wrap:wrap; }`.

### RUN-MB-4 — Inspector as bottom-sheet on phones (M)
`@media (max-width:640px)` → `.inspector { position:fixed; left:0; right:0;
bottom:0; max-height:55vh; overflow-y:auto; }` (overlay instead of cramped
sidebar); head actions wrap. Ensure `.log-tabs` scroll horizontally.

---

## Screen 5 — Sprint History

**Now:** no history-specific `@media` at all — base flex only. Card headers,
metrics, action verbs, fold groups, issue rows, reconciliation all squeeze.

### HIST-MB-1 — Stack history card header (M)
`@media (max-width:600px)` → `.hist-card-head { flex-direction:column; gap:8px; }`,
`.hist-card-head-left/right { flex-basis:100%; }` (the `minmax(0,1fr) auto` grid
crushes title + badges + actions onto one line today).

### HIST-MB-2 — Wrap metrics + est-bar (S)
`@media (max-width:640px)` → `.hist-metrics { flex-wrap:wrap; gap:4px; }`,
`.est-bar { flex:1 1 100%; }`, `.hist-badge { flex:0 1 auto; }`.

### HIST-MB-3 — Wrap action verbs / head buttons (M)
`@media (max-width:600px)` → `.hist-head-actions, .hist-verbs { flex-wrap:wrap;
gap:6px; }`, `.hist-head-btn, .hist-verb { flex:1 1 auto; min-width:80px;
min-height:44px; }` (5+ buttons overflow today; also fixes tap targets).

### HIST-MB-4 — Reflow issue-list rows (S)
`@media (max-width:600px)` → `.iss-row { flex-wrap:wrap; }`,
`.iss-title { flex:1 1 100%; }`, `.iss-time { flex:0 0 100%; margin-left:0;
margin-top:4px; }`, ellipsis on title. Also covers gantt/fold-chip overflow —
add `overflow-x:auto` to `.hist-gantt` if SVG exceeds width.

---

## Totals

20 tickets (4 per screen), all **S or M**. Independent — can be split across
2–3 sprints or cherry-picked by screen. Recommend doing **Sprint Management +
Running Sprint first** (the two you watch live on your phone during a run), then
History, Logs, Bulk Create.
