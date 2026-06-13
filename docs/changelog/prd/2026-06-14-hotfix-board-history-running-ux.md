# PRD Changelog — Hotfix: board-history-running-ux (2026-06-14)

**Branch:** `hotfix/board-history-running-ux`
**Date:** 2026-06-14
**PR:** n/a — operator-driven hotfix merged directly
**Environment:** PRD (port 8000, `master` branch)

---

## What Changed

Eight UX fixes to the Sprint-Mgmt Board / Running / History panes and the
analytics calibration calculation. No GitHub ticket — all changes documented
inline in the diff commit messages.

### Changes

| # | Area | Description |
|---|------|-------------|
| 1 | History pane | Issue rows now show the ticket **title** (not just `#N`) and are clickable — open the GitHub issue in a new tab. `sprint_history_service._normalize_issue` passes `title` through; client falls back to the Board's per-sprint cache; rows gain `.iss-row-link`. |
| 2 | Project header | Removed redundant "Sprint X running" label from `proj-header-pill`. The sprint-nav status pill and sub-nav running dot already signal running state. |
| 3 | Analytics | `_compute_calibration` in `server.py` now counts lifecycle done-equivalent statuses (`done`, `uat`, `merged`, `passed`) — newer sprints no longer blank the per-size record / est-vs-actual plot. |
| 4 | Board — batch-select bar | Bar anchors directly above the card of the sprint whose tickets are selected (single-sprint); cross-sprint falls back to top. New `_smgmtPositionSelectionBar` in `drag-drop.js`. |
| 5 | Board — running sprints | Running sprints default to **collapsed** on the Board; card header gets an **"Open in Running"** deep-link. Collapse pref is now tri-state: `'1'` collapsed / `'0'` explicitly expanded / absent = default-collapsed-for-running. (`board-render.js`) |
| 6 | Sub-view persistence | Board/Running/History sub-view persisted to `sessionStorage` per project; refresh / auto-refresh returns to the last pane. |
| 7 | Board — finished sprints | `completed` / `ready_to_merge` / `partial_finished` cards get a **"History"** deep-link in the card header. |
| 8 | Running metrics strip | Removed sprint-level "Fix rounds X/2" tile (meaningless sum across issues). Strip now shows **"Retrying: N tickets"** only when any ticket is currently in a fix round. |

---

## Files Changed

| File | What changed |
|------|-------------|
| `apps/dashboard/routers/sprint_history_service.py` | Pass `title` through `_normalize_issue` |
| `apps/dashboard/server.py` | `_compute_calibration`: done-equivalent status set |
| `apps/dashboard/static/project.html` | Sub-view persistence, header pill simplification, History row links, finished-sprint History deep-link, Running metrics strip, new CSS for `.iss-row-link` / `.smgmt-hist-link` / `.smgmt-running-link` |
| `apps/dashboard/static/src/sprint-board/board-render.js` | Running sprint tri-state collapse, "Open in Running" button |
| `apps/dashboard/static/src/sprint-board/drag-drop.js` | `_smgmtPositionSelectionBar` |
| `apps/dashboard/static/dist/bundle.js` | Rebuilt from above ES module changes |

---

## What to Test on PRD

Restart PRD after merging: `restart-prd`

### Checklist

- [ ] **History rows show titles and are clickable**
  - Where to look: Sprint Mgmt → History pane → expand any finished sprint
  - Verify: each issue row shows the ticket title beside `#N`; clicking opens `https://github.com/zealchaiwut/commander/issues/N` in a new tab

- [ ] **No "Sprint X running" in project header**
  - Where to look: project header area while a sprint is running
  - Verify: only the sprint-nav pill and sub-nav running dot signal running state; no redundant pill in the project name area

- [ ] **Est-vs-actual plot populated for recent sprints**
  - Where to look: Analytics → Calibration tab
  - Verify: per-size est-vs-actual bars are non-empty for sprints that settled with `uat` / `merged` / `passed` statuses

- [ ] **Batch-select bar position**
  - Where to look: Board pane — select tickets from a single sprint via checkbox or drag
  - Verify: the floating selection bar appears directly above that sprint's card, not at the top of the list

- [ ] **Running sprint collapsed by default on Board**
  - Where to look: Board pane while a sprint is running
  - Verify: the running sprint card is collapsed; header shows an "Open in Running" button; clicking it switches to the Running pane

- [ ] **Sub-view persists across refresh**
  - Where to look: Sprint Mgmt tab — switch to Running or History pane, then refresh the page
  - Verify: the same pane is active after reload

- [ ] **Finished sprint "History" deep-link**
  - Where to look: Board pane — a completed / ready_to_merge sprint card
  - Verify: header shows a "History" button; clicking it switches to History and focuses that sprint

- [ ] **Running metrics strip — "Retrying" tile**
  - Where to look: Running pane metrics strip when a ticket is in a fix round
  - Verify: "Retrying: N tickets" tile is shown; no "Fix rounds X/2" tile ever appears

- [ ] **Regression check**
  - Dashboard loads without errors
  - Agent event feed is live
  - Sprint Mgmt tab opens and shows correct sprint state
  - UAT approve/reject buttons work on any UAT-labelled ticket

---

## Rollback

```bash
git checkout master
git revert HEAD --no-edit
git push origin master
restart-prd
```

---

## Notes

- The bundle was rebuilt from ES module source changes in `board-render.js` and `drag-drop.js`. If any frontend issue surfaces, confirm `static/dist/bundle.js` matches the committed version in this hotfix.
- The tri-state collapse pref (`'0'` = explicit expand) is new; `localStorage` entries for running sprints that had no prior pref will now default to collapsed on first render, then honour any manual expand/collapse.
