# 2026-07-02-9-bless-disk-fallbacks

> Status: decided | provisional

## Context

Five render paths still reading disk: history label discovery, live-snapshot
state.json fallback, nav-pill fallback, home summary-md glob, run roster
fallback. These are documented as deviations from "DB is authority" but serve
real failure modes (e.g. port-coupled status POSTs from the manager).

## Options

- **A ★ Bless the fallbacks explicitly** (they exist for real failure modes,
  e.g. port-coupled status POSTs) and rewrite §1.7 as "DB first, sanctioned
  disk fallback, never disk-only" — then migrate opportunistically.
- **B Hard migration effort:** tickets to remove each fallback; strict §1.7.
- **C Status quo** (documented as deviations).

## Decision

**A — bless the fallbacks explicitly** (provisional — auto-adopted ★
recommendation after interactive timeouts; operator may veto); rewrite §1.7 as
"DB first, sanctioned disk fallback, never disk-only"; migrate opportunistically.

Also covers Q10: closing a ticket without UAT is the sanctioned human "drop it"
mechanism (see `2026-07-02-10-close-without-uat-is-waive.md`).

## Consequences

- `1_state-and-source-of-truth.md` §1.7 rewritten as "DB first; disk is a
  sanctioned fallback, never disk-only" with a table naming each of the five
  fallbacks, its trigger, and why it is sanctioned.
- History's merge-rank logic (`_merge_history_record`) flagged as the one
  genuine hardening target in the set.

## Implemented-by (#N)

#1698 (`fix/1686-1698-flow-decisions`)
