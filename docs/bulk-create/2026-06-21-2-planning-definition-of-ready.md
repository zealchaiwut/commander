# Planning = Definition of Ready (coder quality via specs + design context)

**Date:** 2026-06-21
**Sprint label:** NEW
**Default labels:** enhancement, backend, frontend
**Status:** drafted

Goal: raise coder output quality by making tickets *ready* before they run and
by feeding the coder the design context it needs — **inside the existing
Planning/preflight flow, with NO new tab** (Planning, Advisor, and the Brief are
already under-used; do not add a fourth surface). This is the first of a 3-sprint
arc: A) Definition of Ready (this); B) Home → all-projects command center
(folds Advisor + Brief in); C) regression & contract gates.

**Build on what exists — do not rebuild:**
- `routers/sprint_preflight.py::get_sprint_preflight` already returns warnings
  `{unestimated, stale_estimates, missing_ac}` + cycle detection. Extend it.
- `sprint_manager.py` already injects an estimate `_paths_block` into the coder
  prompt (~line 4669) — the design-context injection follows the SAME pattern.
- `_design_docs_guard` (sprint_manager ~3918) already checks PRODUCT.md/DESIGN.md
  exist; this sprint makes their *content* reach the coder.
- The board already renders a "Ready to run" line (board-render.js ~1980) and
  93's pre-run checklist — extend, don't replace.

**Out of scope (later sprints):** new Specs tab (rejected — fold into Planning),
Home redesign (Sprint B), regression gates (Sprint C).

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Define a canonical ticket-spec format and parser. Establish the structured sections every ticket body should carry: `## Acceptance Criteria` (checklist), `## Design Refs` (links to DESIGN.md/PRODUCT.md headings, e.g. `DESIGN.md#sprint-board`), `## Test Plan`, `## Out of Scope`. Add `services/sprint_manager/ticket_spec.py::parse_ticket_spec(body) -> {acceptance_criteria: list[str], design_refs: list[str], test_plan: str, out_of_scope: str}` that tolerantly parses these sections from an issue body (case-insensitive headings, missing sections → empty). Reuse/consolidate the existing AC-detection that powers preflight's `missing_ac` so there is ONE parser. Add `scripts/lint_ticket_spec.py --issue N` printing which sections are present/missing. No UI yet.

Acceptance: (1) parse_ticket_spec extracts all four sections from a well-formed body and returns empties for a bare body; (2) preflight's missing_ac now uses this parser (no duplicate AC logic); (3) pytest covers well-formed, partial, and empty bodies.
---
Extend preflight into a Definition-of-Ready readiness check. In `get_sprint_preflight`, add per-ticket readiness using `parse_ticket_spec` + the existing estimate resolution: a ticket is READY when it has >=1 acceptance criterion, >=1 design ref, a non-empty test plan, a resolved size estimate, and is not an un-split XL. Return a `readiness` block: `{ready: [...], not_ready: [{number, missing: [...]}]}` alongside the existing warnings. Keep it a pure read (no writes, no GitHub calls beyond what preflight already does). Add a per-project setting `definition_of_ready_mode = off | warn | block` (default `warn`).

Acceptance: (1) preflight returns readiness with precise per-ticket missing reasons; (2) a fully-specified estimated ticket reads ready; (3) mode setting is read from project settings; (4) pytest covers ready/not-ready/each-missing-reason.
---
Surface readiness on the Planning board and gate Run Sprint. Extend the existing pre-run checklist / "Ready to run" rendering (board-render.js) to show each ticket's readiness ✓/✗ with the missing reasons (AC / design ref / test plan / estimate / XL-split), driven by the new preflight readiness block. The Run Sprint button: when `definition_of_ready_mode=block`, disable it with a tooltip listing not-ready tickets; when `warn`, allow but show a confirm dialog summarizing what's not ready; when `off`, no change. Reuse the existing checklist component and the board's data flow — do not add a new panel or tab.

Acceptance: (1) not-ready tickets show ✗ + reasons on the board; (2) block mode disables Run Sprint with the reason list; (3) warn mode shows a confirm with the summary; (4) the existing board tests still pass + new tests cover the three modes.
---
Inject design context into the coder prompt. Mirror the existing estimate `_paths_block` injection in sprint_manager: build a `_design_block` for each dispatched ticket by resolving its `## Design Refs` (via parse_ticket_spec) to the referenced DESIGN.md/PRODUCT.md sections (anchor by heading slug) and prepend it to the coder prompt the same way paths are. Fallbacks: if the ticket has no design refs, include a short index of available DESIGN.md headings (so the coder knows what exists); if a referenced heading is missing, log a warning (do not fail the dispatch). Keep the existing `_design_docs_guard` existence check. Cap the injected text (e.g. 6k chars) to protect the prompt budget.

Acceptance: (1) a ticket referencing `DESIGN.md#sprint-board` gets that section's text in its coder prompt; (2) a ref to a missing heading logs a warning and dispatch continues; (3) no design refs → the heading index is injected; (4) injected context is capped; (5) pytest covers ref-resolution, missing-heading, and the no-refs index path.
---
Make the BA agent emit Definition-of-Ready specs by construction. Update the BA agent definition (apps/dashboard/.claude/agents/ba.md) and `scripts/create_ticket.py` template so every new ticket body includes the four canonical sections, and the BA picks `## Design Refs` from the actual DESIGN.md headings of the target project (read the file, list headings, choose the relevant ones). The BA's existing AC/UAT output maps into `## Acceptance Criteria` + `## Test Plan`. Tickets created by BA should pass the readiness check out of the box.

Acceptance: (1) a BA-created ticket parses to a ready spec (AC + design ref + test plan present); (2) design refs reference real DESIGN.md headings of the project; (3) the feature.md issue template documents the four sections; (4) a smoke test creates a ticket via the template and asserts readiness.
```

## Notes

- **One parser, one source of truth:** `parse_ticket_spec` is the spine — preflight,
  the board readiness, the coder injection, and the BA all use it. Land ticket 1
  first; the rest depend on it.
- **Sequencing:** 1 (parser) → 2 (preflight readiness) → 3 (board + gate) and
  4 (coder injection) in parallel → 5 (BA). 3 and 4 are the user-visible quality
  wins; 4 (design injection) is the single highest-ROI item and could be pulled
  early/standalone.
- **Default `warn`, not `block`,** so the gate informs without trapping you while
  the older un-specced backlog drains. Flip to `block` per project when ready.
- **Lower bug risk than a new tab:** every change extends an existing function
  (preflight, the prompt builder, the board checklist, the BA template). Pairs
  well with Sprint C (contract/smoke gates) catching anything that slips.
- **Feeds Sprint C:** readiness data + spec structure become inputs to the
  quality signals (rework rate per spec completeness) shown on the new Home.
