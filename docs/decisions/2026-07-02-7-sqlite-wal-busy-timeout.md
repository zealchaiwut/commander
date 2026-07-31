# 2026-07-02-7-sqlite-wal-busy-timeout

> Status: decided | provisional

## Context

Server and sprint-manager subprocess both write `commander.db`. `get_conn()`
sets no WAL, no busy timeout; manager lifecycle writes are best-effort with
swallowed exceptions — a lost write is silent and plan.json ends up newer than
the "authoritative" DB. Under concurrent write contention, writes can be lost
silently.

## Options

- **A ★ Enable WAL + `busy_timeout` in `get_conn()`** and log (not swallow)
  failed lifecycle writes. Small, low-risk hardening.
- **B Also add a drift alarm:** reconcile flags plan.json-newer-than-DB.
- **C Accept as-is** — reconcile sweep eventually heals.

## Decision

**A — enable WAL + `busy_timeout` in `get_conn()`** (provisional — auto-adopted
★ recommendation after interactive timeouts; operator may veto) and log (not
swallow) failed lifecycle writes.

## Consequences

- `get_conn()` in `apps/dashboard/db.py` now sets `PRAGMA journal_mode=WAL` and
  `busy_timeout` so concurrent readers do not block writers.
- Lifecycle write failures are logged rather than silently swallowed.
- Low-risk: WAL is backward-compatible; busy_timeout is advisory.

## Implemented-by (#N)

#1688 (`fix/1686-1698-flow-decisions`)
