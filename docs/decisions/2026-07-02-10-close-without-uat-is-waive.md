# 2026-07-02-10-close-without-uat-is-waive

> Status: decided | provisional

## Context

`_has_rework_tickets` only scans open issues. A failed ticket someone closes
manually vanishes from the rework signal — the sprint promotes to
`ready_to_merge` even if it had unresolved failures. Is this intended?

## Options

- **A ★ Intended:** closing a ticket is an explicit human "drop it" — document
  as the sanctioned way to waive a failed ticket.
- **B Not intended:** reconcile should also check closed tickets that never
  got `UAT` and keep the sprint `needs_rework` (or flag it).

## Decision

**A — intended** (provisional — auto-adopted ★ recommendation after interactive
timeouts; operator may veto): closing a ticket is the sanctioned human "drop it";
document as the waive mechanism.

## Consequences

- Documented in `1_state-and-source-of-truth.md` (as part of #1698).
- No code change required; the existing behavior is correct-by-design once
  clearly documented.
- Operators who want to waive a failed ticket close it; the sprint then
  re-evaluates rework status on the next reconcile.

## Implemented-by (#N)

#1698 (`fix/1686-1698-flow-decisions`) — docs only
