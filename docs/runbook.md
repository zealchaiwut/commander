# Commander Operations Runbook

Operational procedures for running Commander unattended (launchd) and keeping the
repo and host clean. See also [quickstart.md](quickstart.md) and
[workflow.md](workflow.md).

## Log rotation

`prd.log` and the `.commander/logs` agent logs are size-rotated so an unattended
server can never exhaust disk (issue #762).

- **prd.log** — a `RotatingFileHandler` is installed on the root logger at every
  server entry point (`apps/dashboard/run_server.py` and the `server:app` import
  in `apps/dashboard/server.py`). Defaults: `maxBytes=10_000_000` (10 MB),
  `backupCount=5` → `prd.log`, `prd.log.1` … `prd.log.5`, oldest pruned
  automatically. Hard upper bound ≈ 60 MB.
- **.commander/logs** — `_CommanderFileHandler` size-rotates each daily file
  (`commander-YYYY-MM-DD.log`) with the same bounds, so the agent log directory
  has a verified upper bound rather than unbounded growth.

### Tuning rotation

Override the bounds via env (read at handler setup / emit time):

| Env var | Default | Meaning |
|---|---|---|
| `COMMANDER_LOG_MAX_BYTES` | `10000000` | Rotate when the file would exceed this size |
| `COMMANDER_LOG_BACKUP_COUNT` | `5` | Number of rotated backups kept |
| `COMMANDER_PRD_LOG` | `apps/dashboard/prd.log` | Override the prd.log path |

To exercise rotation in dev: set `COMMANDER_LOG_MAX_BYTES=1000`, write log lines
past the threshold, and confirm `prd.log.1` appears and the backup count never
exceeds `COMMANDER_LOG_BACKUP_COUNT`. Restore to `10000000` and restart for prod.

## Secrets hygiene

The repo tree and host must never carry live secrets or runtime artifacts.

### Tracked-file audit

`.gitignore` carries explicit patterns for `.env`, `*.db`, `*.log`, `*.pid`,
`*.bak`, `:memory:`, `STATUS.md`, and the `.commander/estimates/` /
`.commander/reports/` runtime dirs. Verify nothing leaked into the index:

```bash
git ls-files | grep -E '(\.env$|\.(db|log|pid)$|tmp.*\.md)'
```

Expected: **no output** (the only tracked `.env*` file is the intentional
`.env.example` template).

### ANTHROPIC_API_KEY rotation (manual — must be done at the Anthropic console)

An `ANTHROPIC_API_KEY` was previously exposed. Revoking it is a **manual** step
that cannot be automated from this repo. Complete and tick every box:

- [ ] Log in to **console.anthropic.com** → **API keys**.
- [ ] Locate the old/exposed key and **revoke (delete)** it.
- [ ] Create a fresh key; store it only in `apps/dashboard/.env` (gitignored).
- [ ] Confirm the old key no longer appears in any `.env`, shell history, or log.
- [ ] Restart the dashboard and confirm it authenticates with the new key.

## Stray files outside the repo (meta root)

The meta root (`~/dev/commander/`, the directory *above* this clone) can collect
stray scratch files that are **not** part of any clone and are not covered by the
repo `.gitignore`. Known strays to remove or leave intentionally:

- `tmp*.md` (e.g. `tmp0xeupjk3.md`) — empty scratch files from tooling; safe to
  delete (`rm ~/dev/commander/tmp*.md`).
- `NOTES.md`, `STATUS.md`, `CHANGELOG.md` at the meta root — author scratch
  notes; review before deleting, they are outside every clone.

These live outside the repo, so they cannot be gitignored from here — periodically
sweep the meta root manually as part of hygiene checks.
