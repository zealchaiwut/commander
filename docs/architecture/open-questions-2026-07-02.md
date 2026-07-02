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

**IMPLEMENTED (#1686, branch `fix/1686-1698-flow-decisions`):** `planned`
removed from `_SPRINT_STATES`/`LIFECYCLE_STATES`/`_LEGAL_SPRINT_EDGES` in
`apps/dashboard/db.py`, kept only as a legacy-read value canonicalizing to
`draft`. Sign-off was already default-disabled via
`config.sprint_signoff_disabled()` (confirmed, not re-implemented — the
approve/reject endpoints already 404 and the run-guard already no-ops by
default). Docs updated in sprint-lifecycle.md and 3_sprint-flow.md.

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

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — consolidate into DB**: add `immediate_parent` column beside `parent_label`; rerun writes both; file copies become dual-writes to retire later.

**IMPLEMENTED (#1691, branch `fix/1686-1698-flow-decisions`):** added
`immediate_parent` to the `sprints` table (additive column, same migration
idiom as the other run-artifact columns). `db.set_sprint_immediate_parent()`
writes it from the rerun endpoint (`routers/sprint_run.py`) alongside the
existing plan.json `parent` write, creating a placeholder `draft` row for
queued children if none exists yet. The value survives the later `running`
transition unchanged. Merge-topology resolvers (`_sprint_merge_parent_label`,
`_merge_steps_for_sprint_chain` in `startup.py`) still read plan.json first —
switching them to prefer the DB column is follow-up work, not done here, so
this ticket only adds the column and its writer.

## Q5 — Two canonical lifecycle read accessors

`apps/dashboard/sprint_state.py` returns `"unknown"` for a missing row;
`apps/dashboard/routers/sprint_state.py` returns `None`. Contract says one
sanctioned reader.

- **A ★ Delete the routers copy;** migrate its callers to the top-level
  accessor. Mechanical.
- **B Keep both,** document the difference.

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — delete the routers accessor**; migrate callers to `apps/dashboard/sprint_state.py`.

**IMPLEMENTED (#1692, branch `fix/1686-1698-flow-decisions`):** deleted
`apps/dashboard/routers/sprint_state.py` (it was never a mounted FastAPI
router despite the location — a plain module). Its one caller
(`routers/sprint_history_service.py`) migrated to the top-level
`apps/dashboard/sprint_state.py`, adjusting its `or _normalize_state(...)`
fallback (which relied on `None` being falsy) to an explicit
`is None or == "unknown"` check since the canonical accessor returns the
string `"unknown"`, not `None`, for a missing row.

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

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — write a DB row at creation/queue time** so the DB is complete pre-run. (Adjusted for Q1: with `planned`/signoff deprecated, use `draft` at create and at rerun-queue.)

## Q7 — SQLite dual-writer robustness

Server and sprint-manager subprocess both write `commander.db`. `get_conn()`
sets no WAL, no busy timeout; manager lifecycle writes are best-effort with
swallowed exceptions — a lost write is silent and plan.json ends up newer than
the "authoritative" DB.

- **A ★ Enable WAL + `busy_timeout` in `get_conn()`** and log (not swallow)
  failed lifecycle writes. Small, low-risk hardening.
- **B Also add a drift alarm:** reconcile flags plan.json-newer-than-DB.
- **C Accept as-is** — reconcile sweep eventually heals.

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — enable WAL + busy_timeout in `get_conn()`** and log (not swallow) failed lifecycle writes.

## Q8 — Run-lock sentinel mismatch

Orchestrator sets `COMMANDER_SPRINT_RUNNING=<label>`; `assert_run_mutable`
checks `== "1"` (inert in manager subprocesses); `update_ticket.py` treats any
truthy value as locked. Two guard layers, different semantics.

- **A ★ Unify on truthy-check** in `assert_run_mutable` (match
  `update_ticket.py`); the label value is useful context — keep setting it.
- **B Unify on `"1"`** everywhere and pass the label separately.
- **C Leave documented.**

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — unify on truthy-check** in `assert_run_mutable`; keep setting the label value for context.

**IMPLEMENTED (#1689, branch `fix/1686-1698-flow-decisions`):**
`state_machine.run_lock_active()` and `github_client._refuse_if_sprint_running`
both now treat any non-empty `COMMANDER_SPRINT_RUNNING` value as locked,
matching `update_ticket.py`. Two pre-existing tests asserting the old exact-`"1"`
semantics were updated (`test_754__run_mutable_labels.py`) — production never
sets the var to `"0"`, so treating it as truthy there costs nothing.

## Q9 — Disk-at-render fallbacks: migrate or bless?

§1.7 lists five render paths still reading disk: history label discovery,
live-snapshot state.json fallback, nav-pill fallback, home summary-md glob,
run roster fallback.

- **A ★ Bless the fallbacks explicitly** (they exist for real failure modes,
  e.g. port-coupled status POSTs) and rewrite §1.7 as "DB first, sanctioned
  disk fallback, never disk-only" — then migrate opportunistically.
- **B Hard migration effort:** tickets to remove each fallback; strict §1.7.
- **C Status quo** (documented as deviations).

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — bless the fallbacks explicitly**; rewrite §1.7 as "DB first, sanctioned disk fallback, never disk-only"; migrate opportunistically.

## Q10 — Closed-without-UAT tickets read as resolved

`_has_rework_tickets` only scans open issues. A failed ticket someone closes
manually vanishes from the rework signal → sprint promotes to
`ready_to_merge`.

- **A ★ Intended:** closing a ticket is an explicit human "drop it" — document
  as the sanctioned way to waive a failed ticket.
- **B Not intended:** reconcile should also check closed tickets that never
  got `UAT` and keep the sprint `needs_rework` (or flag it).

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — intended**: closing a ticket is the sanctioned human "drop it"; document as the waive mechanism.

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

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — fix (b) TTL-on-success and (c) sweep cursor**; leave (a) flap as self-healing.

**IMPLEMENTED (#1690, branch `fix/1686-1698-flow-decisions`):** (b) and (c)
both fixed in `routers/sprint_reconcile_service.py` — TTL stamped only after
a successful pass; per-project rotating cursor over eligible rows so a
project with >40 eligible terminal sprints gets full coverage across sweeps
instead of only ever re-checking the first 40. (a) left as-is per the
recommendation.

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

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — delete or fold** `_lineage_fully_in_develop` after comparing with `_sprint_db_mark_merged_completed`'s check; no wiring into the sweep.

## Q13 — Neon leftovers: delete or bless export-only

`sprint_repo.py` / `models.py` are reachable only from
`scripts/migrate_sprints_to_neon.py` / `scripts/export_to_neon.py`. Keeping
them wired invites someone to reconnect runtime code against stale docs.

- **A ★ Bless export-only:** move both under `scripts/` (or add a module
  docstring "export-only, no runtime imports") + a lint/test guard that fails
  if dashboard/server code imports them.
- **B Delete entirely** along with the export scripts (Neon abandoned).
- **C Leave as-is** (docs now say export-only).

**Decision (2026-07-02, PROVISIONAL — auto-adopted ★ recommendation after interactive timeouts; operator may veto):** **A — bless export-only**: docstring + import-guard test preventing dashboard/server imports of `sprint_repo.py`/`models.py`.

---

## Ticket Map (filed 2026-07-02)

| Ticket | Decision(s) | Title |
|--------|-------------|-------|
| #1686 | Q1 | Deprecate planned lifecycle state and plan.json signoff gate |
| #1687 | Q1 | Deprecate advisor and brief features (park until platform stable) |
| #1688 | Q7 | SQLite hardening: WAL + busy_timeout, surface swallowed lifecycle write failures |
| #1689 | Q8 | Unify run-lock sentinel (truthy check) |
| #1690 | Q11 | Reconcile sweep: TTL on success only; cursor the 40-row window |
| #1691 | Q4 | Consolidate lineage: immediate_parent DB column |
| #1692 | Q5 | Remove duplicate lifecycle read accessor |
| #1693 | Q6 | Draft DB row at sprint create and rerun-queue |
| #1694 | Q12 | Resolve _lineage_fully_in_develop (delete or fold) |
| #1695 | Q13 | Neon export-only: docstrings + import-guard test |
| #1696 | Q2 | Merge Sprint soft rework guard |
| #1697 | Q3 | Sweep auto-settles confirmed orphaned running sprints |
| #1698 | Q9+Q10 | Docs: sanctioned disk fallbacks + waive-by-close rule |

Suggested sequencing: #1686/#1687 (deprecations) → #1688/#1689/#1690 (hardening)
→ #1691→#1693 (lineage/DB model; #1693 benefits from #1691 and #1686) →
#1694/#1695 (cleanups) → #1696/#1697 (guards; #1697 pairs with #1690's sweep
changes) → #1698 (docs, anytime).
