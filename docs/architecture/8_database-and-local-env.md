# 8. Database, connection & local env/files

[← Contents](0_content.md) · [← Prev: Git / branch strategy](7_git-branch-strategy.md) · [Next: Multiple projects →](9_multiple-projects.md)

## 8.1 What lives where today

Full store inventory + authority table: [section 1.1](1_state-and-source-of-truth.md). This section covers the storage/connection side.

- **SQLite** — one `commander.db` per clone, path from `DB_PATH` (`apps/dashboard/db.py`). All tables created in `db.init_db()` except `settings_kv` (lazy). **Two writer processes** share the file: the uvicorn server and the sprint-manager subprocess (which imports `apps/dashboard/db.py` directly). `get_conn()` sets **no WAL mode and no busy timeout** (only `settings_repo._kv_conn` sets `timeout=5`); write failures in the manager are best-effort and swallowed. *(open question: enable WAL + busy_timeout, and/or surface swallowed lifecycle-write failures.)*
- **`.commander/` dir** — discovered by walking up from CWD preferring the dir owning `sprint.yaml` (`commander_paths.discover_commander_dir()`). Per-sprint files: `{label}-plan.json` (plan + dual-written lifecycle state + `signoff`), `{label}-state.json` (live run state, atomic rewrite via `SprintState.save()`), `{label}-status.json` (persisted status POSTs, rehydrated at startup), `{label}-pid`, `{label}-summary-<date>.md`. Shared: `bulk-jobs/`, `estimates/`, `logs/`, `alerts/`, `mis-sizing-flags-*.json`, `calibration_cache.json`, `settings_store.json`, `runtime/sprint-progress.json`.
- **`apps/dashboard/projects.json`** — primary project registry.
- Clones (prd/uat/coder/tester) each have their **own SQLite + mirror + ETags** but share GitHub and, when clones share a project root, the same `.commander/sprints` files.

## 8.2 Neon migration plan

**Superseded.** The runtime Neon layer was removed — SQLite `sprints` took the lifecycle-render role the plan (issues #244–248) targeted. Neon remains export-only via `scripts/migrate_sprints_to_neon.py` / `scripts/export_to_neon.py`; settings/todo KV can use it when enabled. See [section 1.4](1_state-and-source-of-truth.md). Pending decision: delete `sprint_repo.py`/`models.py` or mark them export-only.

## 8.3 Config vs state vs secrets

What belongs where (secrets detail in [section 12](12_security-and-secrets.md)).

_TODO_

## 8.4 Backup/restore

The gist-based approach.

_TODO_
