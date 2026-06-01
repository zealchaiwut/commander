# Sprint board drag-and-drop smoke tests

Manual checklist for issue #458. Run all steps against a local dev environment
(`localhost:8000`). Pass requires 3 consecutive clean runs with no step failing.

> **No Playwright detected** — this checklist is the automated-test substitute
> per the acceptance criteria. When Playwright is added to the project, convert
> these steps to `tests/e2e/sprint-drag.spec.ts`.

---

## Prerequisites

1. Local dev server running (`uvicorn apps.dashboard.app:app --reload`).
2. At least two sprints visible on the sprint management board, each containing
   at least two tickets.
3. GitHub CLI authenticated (`gh auth status` passes) — required for label
   verification steps.

---

## TC-1: Cross-sprint ticket drag updates GitHub label

**Goal:** Drag a ticket from Sprint A to Sprint B; verify the GitHub label
updates within 2 seconds and survives a page reload.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Open `/` and navigate to **Sprint Management** tab. | Board renders both sprints with their tickets. |
| 2 | Drag any ticket from Sprint A and drop it onto Sprint B. | Ticket disappears from Sprint A and appears in Sprint B immediately (optimistic render). |
| 3 | Within 2 seconds, run `gh issue view <ticket-number> --json labels --jq '.labels[].name'` in a terminal. | Output includes `sprint-<B>` and does NOT include `sprint-<A>`. |
| 4 | Reload the page. | Ticket is still in Sprint B; it has not reverted to Sprint A. |

**Pass criteria:** Steps 3 and 4 both confirm the correct label.

---

## TC-2: Within-sprint ticket reorder persists after reload

**Goal:** Reorder tickets inside a single sprint; verify position survives
a reload.

> **Implementation note (as of issue #458):** The current drop handler
> (`smgmtDropOnSprint`, app.js line 4707) treats same-sprint drops as a no-op
> (`if (fromSprint === targetSprintLabel) return`). Within-sprint ordering is
> not yet persisted to the server. If this step fails, record the result as
> **KNOWN LIMITATION** rather than a regression.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Note the current order of tickets in Sprint B (e.g., #10, #20, #30). | — |
| 2 | Drag ticket at position 1 to position 3 within Sprint B. | Ticket moves to position 3 in the list immediately. |
| 3 | Reload the page. | Ticket remains at position 3; order is not reset to the pre-drag state. |

**Pass criteria:** Step 3 shows the new order. Mark KNOWN LIMITATION if the
reload reverts to the original order (within-sprint persistence not yet
implemented).

---

## TC-3: Ghost-pane create flow uses the next sequential sprint number

**Goal:** Dropping a ticket on the ghost pane creates a new sprint with the
correctly auto-incremented number.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | On the sprint board, note the highest existing sprint number (e.g., Sprint 4). | — |
| 2 | Begin dragging any ticket. | Ghost pane appears at the bottom of the board labelled "Drop here to create Sprint 5" (one higher than the current max). |
| 3 | Drop the ticket onto the ghost pane. | Confirmation modal opens showing "Sprint 5" and the ticket to be moved. |
| 4 | Click **Create sprint** in the modal. | Modal closes. A new "Sprint 5" block appears on the board containing the ticket. |
| 5 | Run `gh label list --repo <owner>/<repo> \| grep sprint-5`. | Label `sprint-5` exists in the repo. |
| 6 | Run `gh issue view <ticket-number> --json labels --jq '.labels[].name'`. | Output includes `sprint-5`. |

**Pass criteria:** Sprint number increments correctly; GitHub label exists and
is applied to the moved ticket.

---

## TC-4: Multi-select bulk drag moves all selected tickets

**Goal:** Select multiple tickets across sprints, drag the selection, and
confirm all tickets land in the destination sprint with correct labels.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | On the sprint board, check the checkboxes for at least three tickets across Sprint A and Sprint B. | Bulk bar appears at the bottom showing "N selected". |
| 2 | Drag any one of the selected tickets to the destination sprint (Sprint C or the ghost pane). | All selected tickets move to the destination sprint simultaneously (optimistic render). Bulk bar clears. |
| 3 | For each moved ticket, run `gh issue view <n> --json labels --jq '.labels[].name'`. | Every ticket's labels include `sprint-<destination>` and do NOT include their prior sprint label. |
| 4 | Reload the page. | All three tickets remain in the destination sprint; none have reverted. |

**Pass criteria:** Steps 3 and 4 both confirm every ticket has the correct label.

---

## TC-5: Bulk move via "Move to" modal (keyboard/mouse alternative path)

**Goal:** Use the bulk-bar "Move to" button instead of drag to cover the modal
code path.

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Multi-select two tickets using checkboxes. | Bulk bar shows "2 selected" with a **Move to** button. |
| 2 | Click **Move to**. | "Move to sprint" modal opens listing all available sprints. |
| 3 | Choose a destination sprint and click **Move tickets**. | Modal closes; both tickets appear in the destination sprint. |
| 4 | Run `gh issue view <n> --json labels ...` for both tickets. | Both carry the destination sprint label. |

**Pass criteria:** Both tickets have correct labels without a page reload.

---

## Flakiness gate

Run the full checklist (TC-1 through TC-5) three times in a row with no
browser cache (`Cmd+Shift+R` between each run). All five test cases must pass
on all three runs for the checklist to be considered flakiness-free.

If any step fails intermittently, record the failure mode and file a follow-up
bug before considering this checklist closed.

---

## Notes

- The `/api/sprint-planning/assign` endpoint is the single integration point
  for all drag-and-drop label mutations. All TCs ultimately verify that this
  API call succeeds and that the resulting GitHub label matches the destination.
- Sprint reordering (dragging sprint header blocks, not tickets) is out of
  scope for this checklist; it is covered separately by `smgmtReorderSprints`.
- Mobile / touch drag-and-drop is explicitly out of scope (see issue #458).
