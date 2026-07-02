# Open Flow Questions — 2026-07-02 Lifecycle/Reconcile/Data Review

Source: full flow review that produced commit `4cc886a4` (docs updated to
as-built). Each question is a design decision for the operator. Answer inline
(edit this file) or walk through interactively. Recommendations marked ★.

---

## Q1 — `planned` state: wire or delete?

`planned` exists in the enum and `_LEGAL_SPRINT_EDGES` but nothing ever writes
it. The preflight gate shipped as the plan.json `signoff` field with its own
approve/reject endpoints.

- **A ★ Delete `planned`; canonize `signoff`.** Remove from enum/edges;
  document plan.json `signoff` as the official gate. Least churn; matches
  reality.
- **B Wire it.** Sign-off approval writes `planned` to the DB; signoff becomes
  a detail behind the enum. Completes "DB is sole lifecycle source" but touches
  the run/sign-off flow.
- **C Leave as documented wart.**

**Decision (2026-07-02):** **Park/deprecate BOTH** — `planned` state AND the
plan.json `signoff` gate are deprecated for now (too hard to stabilize).
Also deprecate the **advisor** and **brief** features in the same pass.
Remove/disable rather than wire up; revisit when the platform is stable.

## Q2 — Merge Sprint has no rework guard

Plain finish closes ALL sprint issues including `needs-rework` ones;
bulk-complete and complete-step refuse on rework. Five paths reach
`completed` with inconsistent guards.

- **A ★ Add a soft guard to finish:** confirmation modal warns "N rework
  tickets will be closed" — human can still override (human click = sign-off).
- **B Hard guard:** finish refuses like bulk-complete; operator must re-run or
  manually clear rework first.
- **C Intended:** human sign-off overrides everything; document only (done).

**Decision (2026-07-02):** **A — soft guard.** Finish confirmation modal warns
"N rework tickets will be closed"; human can override. Never close failed
work silently.

## Q3 — Orphan settling is button-only

`_github_reconcile_row` can settle an orphaned `running` sprint (PID file
present, process dead), but the auto sweep skips `running` rows — only the
per-sprint Reconcile button reaches that branch.

- **A ★ Let the sweep settle confirmed orphans** (PID-file-present AND
  process-dead only; PID-file-absent still left alone per issue #1095).
- **B Keep button-only** — conservative; a false-positive orphan settle during
  a live run would be bad, and the button exists.

**Decision (2026-07-02):** **A — auto-settle confirmed orphans** in the sweep
(PID-file-present AND process-dead only; PID-file-absent untouched per #1095).

## Q4 — Three lineage fields

plan.json `parent` = immediate parent (drives merge topology); DB
`sprints.parent_label` = base label (drives History grouping); state.json
`rerun_into` = forward pointer. Three stores, three notions.

- **A ★ Consolidate into DB:** add `immediate_parent` column alongside
  `parent_label` (base); write both at rerun; plan.json/state.json copies
  become dual-writes to retire later.
- **B Leave as-is,** now documented.

**Decision:** _______

## Q5 — Two canonical lifecycle read accessors

`apps/dashboard/sprint_state.py` returns `"unknown"` for a missing row;
`apps/dashboard/routers/sprint_state.py` returns `None`. Contract says one
sanctioned reader.

- **A ★ Delete the routers copy;** migrate its callers to the top-level
  accessor. Mechanical.
- **B Keep both,** document the difference.

**Decision:** _______

## Q6 — Queued rerun children invisible to the DB

`auto_run=false` rerun children exist only as plan.json
(`needs_rework`/`queued`), no DB row — a sub-lifecycle `sprint_state.current()`
cannot see. Related: sprint creation writes no DB row at all (missing row =
implicit `draft`).

- **A ★ Write a DB row at creation/queue time** (`draft` at create, or a
  queued marker at rerun-queue), making the DB genuinely complete. Pairs
  naturally with Q1 option B or A.
- **B Accept plan.json as the pre-run store;** DB authority starts at first
  dispatch. Document only (done).

**Decision:** _______

## Q7 — SQLite dual-writer robustness

Server and sprint-manager subprocess both write `commander.db`. `get_conn()`
sets no WAL, no busy timeout; manager lifecycle writes are best-effort with
swallowed exceptions — a lost write is silent and plan.json ends up newer than
the "authoritative" DB.

- **A ★ Enable WAL + `busy_timeout` in `get_conn()`** and log (not swallow)
  failed lifecycle writes. Small, low-risk hardening.
- **B Also add a drift alarm:** reconcile flags plan.json-newer-than-DB.
- **C Accept as-is** — reconcile sweep eventually heals.

**Decision:** _______

## Q8 — Run-lock sentinel mismatch

Orchestrator sets `COMMANDER_SPRINT_RUNNING=<label>`; `assert_run_mutable`
checks `== "1"` (inert in manager subprocesses); `update_ticket.py` treats any
truthy value as locked. Two guard layers, different semantics.

- **A ★ Unify on truthy-check** in `assert_run_mutable` (match
  `update_ticket.py`); the label value is useful context — keep setting it.
- **B Unify on `"1"`** everywhere and pass the label separately.
- **C Leave documented.**

**Decision:** _______

## Q9 — Disk-at-render fallbacks: migrate or bless?

§1.7 lists five render paths still reading disk: history label discovery,
live-snapshot state.json fallback, nav-pill fallback, home summary-md glob,
run roster fallback.

- **A ★ Bless the fallbacks explicitly** (they exist for real failure modes,
  e.g. port-coupled status POSTs) and rewrite §1.7 as "DB first, sanctioned
  disk fallback, never disk-only" — then migrate opportunistically.
- **B Hard migration effort:** tickets to remove each fallback; strict §1.7.
- **C Status quo** (documented as deviations).

**Decision:** _______

## Q10 — Closed-without-UAT tickets read as resolved

`_has_rework_tickets` only scans open issues. A failed ticket someone closes
manually vanishes from the rework signal → sprint promotes to
`ready_to_merge`.

- **A ★ Intended:** closing a ticket is an explicit human "drop it" — document
  as the sanctioned way to waive a failed ticket.
- **B Not intended:** reconcile should also check closed tickets that never
  got `UAT` and keep the sprint `needs_rework` (or flag it).

**Decision:** _______

## Q11 — Reconcile tightening (flap / TTL / starvation)

Three small mechanics: (a) `ready_to_merge↔needs_rework` can flap during
mirror lag — the natural-run guard covers only the demote direction; (b) the
60 s throttle timestamp is recorded before the pass, so a failed pass blocks
retries; (c) the 40-row sweep cap can starve the tail on projects with many
non-final sprints.

- **A ★ Fix (b) and (c)** (record TTL on success; rotate/cursor the sweep
  window) — cheap. Leave (a): it self-heals and a symmetric guard risks
  masking real rework.
- **B Fix all three** including a promote-direction lag guard.
- **C None** — all self-heal eventually.

**Decision:** _______

## Q12 — `_lineage_fully_in_develop` has tests but no production caller

Defined in `sprint_reconcile_service.py` for B2 auto-complete of superseded
ancestors; tests assert its behavior; nothing in production calls it (the
comment says needs_rework→completed is never reconciler-driven, yet
`_sprint_db_mark_merged_completed` does its own verification).

- **A ★ Delete it + its tests,** or fold its check into
  `_sprint_db_mark_merged_completed` if that path's verification is weaker.
  Decide after comparing the two checks.
- **B Wire it:** sweep auto-completes superseded ancestors once lineage is
  verified merged.

**Decision:** _______

## Q13 — Neon leftovers: delete or bless export-only

`sprint_repo.py` / `models.py` are reachable only from
`scripts/migrate_sprints_to_neon.py` / `scripts/export_to_neon.py`. Keeping
them wired invites someone to reconnect runtime code against stale docs.

- **A ★ Bless export-only:** move both under `scripts/` (or add a module
  docstring "export-only, no runtime imports") + a lint/test guard that fails
  if dashboard/server code imports them.
- **B Delete entirely** along with the export scripts (Neon abandoned).
- **C Leave as-is** (docs now say export-only).

**Decision:** _______
