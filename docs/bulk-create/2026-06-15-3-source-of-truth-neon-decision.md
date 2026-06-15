# Decision Memo — Single Source of Truth: revive Neon?

> **Question (operator):** the count/state drift keeps coming back — should we
> bring back Neon/Postgres as the single source of truth?
>
> **Short answer: No — don't revive Neon as the primary store.** The drift you
> hit was *algorithmic* (two code paths computed "done" differently), not
> *storage-layer*. A Postgres SoT would not have prevented it — it would move
> the complexity and add a hard network dependency to a single-user local tool.
> **Do** finish the SQLite-as-primary path instead (you're ~80% there).
>
> This is a memo, not a ticket batch — but actionable S/M follow-ups are at the
> end. Decision is yours; evidence below.

## What is true today (as implemented)

| Store | Owns | Written by |
|-------|------|-----------|
| **GitHub** | ticket/sprint **state** (labels, membership, PRs, open/closed) | `state_machine.transition()` — single writer |
| **SQLite** (`commander.db`) | **metrics** — `agent_runs`, `token_usage`, `events`, `sprints` (lifecycle + run artifacts), `sprint_history`, issues/milestones mirrors | sprint manager + dashboard |
| **Disk JSON** (`.commander/sprints/*`) | **write-once** run artifacts (`{label}-state.json`, estimates) | once at end-of-run |
| **Neon/Postgres** | **disabled** (`COMMANDER_DISABLE_NEON=1`), schema unmigrated; only `scripts/export_to_neon.py` writes, on demand | nothing live |

Conflict rule (design): GitHub wins state · SQLite wins metrics · disk is
write-once, ingested to DB at end-of-run, not read at render.

## Why the recent drift was NOT a storage problem

The three bugs fixed this week (PRs #1086/#1105):

1. **Pill vs board count** — GitHub-fallback used `total − backlog`; live path
   used `total − backlog − in-progress − sit`. Two formulas. Fix: one canonical
   `_settled_done_from_columns()` / `_snavSettledDone()`.
2. **Outcome vs History count** — outcome read `issues_json` verbatim; History
   unioned `agent_runs`. Fix: outcome now unions `agent_runs` too.
3. **Disk-vs-DB dual read** — `run_ingested_at` gate forked the read path. Fix:
   lazy-ingest on read.

All three are **derived-count coordination bugs**. The authoritative inputs
(GitHub labels, `agent_runs` dispatch logs) were fine. Putting them in Postgres
changes nothing — you'd *still* pick a formula, and *still* need the GitHub
fallback when the row is stale/missing. **Neon would not have prevented any of
these.**

## What reviving Neon-as-primary would cost

| Factor | SQLite-only (today) | Neon **primary** | Neon **export-only** (today's design) |
|---|---|---|---|
| Dashboard uptime | 100% (local) | **500s when Neon/network down** | 100% |
| Sprint-create latency | ~50ms | ~150ms + network (dual-write) | ~50ms |
| Schema migration | none | Alembic + PG enums per machine | none |
| Maintenance | low (1 DB) | **high** (SQLite↔Neon sync + fallback) | low |
| Fixes the drift? | — | **No** (algorithmic, not storage) | No |
| Multi-machine sync | manual | automatic | manual / async push |
| Cost/mo | $0 | ~$5–20 | ~$5–20 (optional) |
| Single-user fit | perfect | **overkill** (no auth/RBAC; Tailscale-local) | good |

Hard dependency is the killer: today nothing breaks if Neon is down. As primary,
every sprint create/update/run becomes a network call that can 500 — for a tool
whose whole value is "works on my phone over Tailscale, offline-ish."

## Recommendation

**Keep Neon disabled / export-only. Finish making SQLite the single metrics SoT.**

Concretely:
1. **Lazy-ingest everywhere, stop reading disk at render** — outcome + finish-card
   already lazy-ingest (this week). Do the same for History and any remaining
   reader, then delete the disk-read branches. One read path: DB. (Disk stays as
   the write-once audit trail, never read in a response.)
2. **One canonical count helper** — already started (`_settled_done_from_columns`).
   Audit the remaining panes (donut intentionally = "completed"; everything else
   = settled) so no pane re-derives counts inline.
3. **Optional: materialized `sprint_summary`** — compute it on run-finish in
   `db.ingest_sprint_run_artifact()`; outcome/history read that row, O(1), stable
   shape, decoupled from raw `issues_json`.
4. **Keep `export_to_neon.py` for BI** — if you ever want dashboards in a BI tool,
   push a snapshot per finish. Analytics tolerates 1-sprint staleness; the live
   dashboard never depends on it.

Revisit Neon **only** if the product changes shape: multi-user, multi-machine
write concurrency, or you want hosted BI on live data. None of those are true
today (no auth, single user, local-first).

## When Neon *would* make sense (tripwires)

- You add **auth + multiple users** writing concurrently.
- You run the dashboard from **2+ machines** that must see each other's live
  writes (not just a nightly export).
- You want **always-fresh hosted analytics** (not retrospective).

Until one of those is real, Postgres is cost + a new failure mode with no payoff.

---

## Actionable follow-ups (S/M, only if you take the recommendation)

### SOT-1 — Lazy-ingest the History read path (M)
Mirror the outcome/finish-card lazy-ingest: when a History row lacks
`run_ingested_at` but the sprints row exists, ingest from disk so the next read
is DB-only. Removes the last disk-vs-DB fork at render.

### SOT-2 — Delete disk-read branches once all readers ingest (M)
After SOT-1, remove the disk-fallback code in outcome/history/finish so there is
exactly one read path (DB). Keep disk as write-once audit only.

### SOT-3 — Reconcile counts, not just state (M)
Background reconcile currently fixes lifecycle state only. Have it re-derive
`issues_json`/counts from `agent_runs` for terminal sprints and wire the unused
`db.update_sprint_reconciliation()`. (This was rec 2c's deeper half — the
session shipped the outcome-side union; this closes the stored-row side.)

### SOT-4 — Optional: materialized `sprint_summary` on finish (M)
Compute and store a denormalized summary row in `ingest_sprint_run_artifact()`;
point outcome/history reads at it. O(1) reads, one fixed shape, no inline
re-derivation anywhere.

### SOT-5 — Document the SoT contract in one place (S)
Update `docs/architecture/1_state-and-source-of-truth.md`: GitHub=state,
SQLite=metrics (single, DB-only at render), disk=write-once audit, Neon=optional
export. State the one canonical count definition. So the next person doesn't
re-introduce a third formula.

**Net:** these 5 (4×M + 1×S) finish the single-source-of-truth work **without**
a new database, a network dependency, or a migration — and actually kill the
drift class at its root (one read path, one formula, reconciled counts).
