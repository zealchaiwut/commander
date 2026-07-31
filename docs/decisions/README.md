# Decisions

Architecture Decision Records (ADRs) for Commander. One file per decision;
naming `YYYY-MM-DD-N-<slug>.md` (BKK dates, N = sequence within the day so
entries file in order — mirrors `docs/bulk-create/`).

Use `scripts/log_decision.py` or `/decide` to append a new entry in one call.
The template is in `TEMPLATE.md`.

---

## 2026-07-02

- [2026-07-02-1-delete-planned-state-and-signoff](2026-07-02-1-delete-planned-state-and-signoff.md) — Q1: deprecate `planned` lifecycle state and plan.json signoff gate
- [2026-07-02-2-merge-sprint-rework-soft-guard](2026-07-02-2-merge-sprint-rework-soft-guard.md) — Q2: soft confirmation guard when closing rework tickets at Merge Sprint
- [2026-07-02-3-sweep-auto-settle-confirmed-orphans](2026-07-02-3-sweep-auto-settle-confirmed-orphans.md) — Q3: auto-reconcile sweep settles confirmed orphaned running sprints
- [2026-07-02-4-consolidate-lineage-into-db](2026-07-02-4-consolidate-lineage-into-db.md) — Q4: add `immediate_parent` DB column to consolidate sprint lineage fields
- [2026-07-02-5-delete-duplicate-lifecycle-accessor](2026-07-02-5-delete-duplicate-lifecycle-accessor.md) — Q5: delete `routers/sprint_state.py` and migrate callers to canonical accessor
- [2026-07-02-6-draft-db-row-at-create](2026-07-02-6-draft-db-row-at-create.md) — Q6: write a DB row at sprint creation and rerun-queue time
- [2026-07-02-7-sqlite-wal-busy-timeout](2026-07-02-7-sqlite-wal-busy-timeout.md) — Q7: enable WAL + busy_timeout in get_conn(), surface swallowed write failures
- [2026-07-02-8-unify-run-lock-sentinel](2026-07-02-8-unify-run-lock-sentinel.md) — Q8: unify run-lock sentinel on truthy-check
- [2026-07-02-9-bless-disk-fallbacks](2026-07-02-9-bless-disk-fallbacks.md) — Q9: bless sanctioned disk fallbacks; rewrite §1.7 as "DB first, disk fallback"
- [2026-07-02-10-close-without-uat-is-waive](2026-07-02-10-close-without-uat-is-waive.md) — Q10: closing a ticket without UAT is the sanctioned waive mechanism
- [2026-07-02-11-reconcile-ttl-and-cursor](2026-07-02-11-reconcile-ttl-and-cursor.md) — Q11: reconcile TTL-on-success and cursor the 40-row sweep window
- [2026-07-02-12-delete-lineage-fully-in-develop](2026-07-02-12-delete-lineage-fully-in-develop.md) — Q12: delete `_lineage_fully_in_develop` (no production caller)
- [2026-07-02-13-neon-export-only-docstrings](2026-07-02-13-neon-export-only-docstrings.md) — Q13: bless Neon sprint_repo as export-only; add import-guard test
