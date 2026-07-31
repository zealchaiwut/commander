# 2026-07-02-13-neon-export-only-docstrings

> Status: decided | provisional

## Context

`sprint_repo.py` / `models.py` are reachable only from
`scripts/migrate_sprints_to_neon.py` / `scripts/export_to_neon.py`. Keeping
them wired invites someone to reconnect runtime code against stale docs. The
correct policy (runtime vs export-only) is undocumented.

## Options

- **A ★ Bless export-only:** move both under `scripts/` (or add a module
  docstring "export-only, no runtime imports") + a lint/test guard that fails
  if dashboard/server code imports them.
- **B Delete entirely** along with the export scripts (Neon abandoned).
- **C Leave as-is** (docs now say export-only).

## Decision

**A — bless export-only** (provisional — auto-adopted ★ recommendation after
interactive timeouts; operator may veto): docstring + import-guard test
preventing dashboard/server imports of `sprint_repo.py`/`models.py`.

## Consequences

- Investigation corrected the premise: `models.py` and `neon_db.py` are NOT
  export-only — both are genuine runtime dependencies of
  `apps/dashboard/{settings_repo,todo_repo}.py`'s settings/todo KV fallback
  when Neon is enabled. Only `sprint_repo.py` and `sync_projects_to_neon.py`
  have zero runtime callers.
- `sprint_repo.py`, `sync_projects_to_neon.py`: EXPORT-ONLY docstrings added;
  also fixed `sync_projects_to_neon.py`'s stale claim of being "called at
  dashboard startup and by POST /api/projects/sync-to-db" — neither exists.
- `models.py`, `neon_db.py`: docstrings clarify the split (which ORM
  classes/paths are runtime-shared vs export-only).
- New `tests/test_neon_export_only.py`: AST-based static scan failing if any
  `apps/dashboard/**/*.py` file or the sprint-manager entrypoint imports
  `sprint_repo` or `sync_projects_to_neon`.

## Implemented-by (#N)

#1695 (`fix/1686-1698-flow-decisions`)
