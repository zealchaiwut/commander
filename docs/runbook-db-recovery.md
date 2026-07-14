# Runbook: commander.db Recovery

## Incident Pattern

The live `commander.db` was found truncated (e.g. 4 KB from ~30 MB). Every SQLite operation returns `disk I/O error (10)`. All DB-backed endpoints return 500.

**Root-cause analysis (issue #1901):**

- **Primary suspect — WAL checkpoint crash during unattended write**: SQLite's WAL mode writes new pages to a `-wal` file; a checkpoint moves them back to the main DB. A crash mid-checkpoint can leave the main DB partially written and the `-wal` file at 0 bytes, producing a truncated main file.
- **Contributing factor — fd exhaustion (#1749)**: `get_conn()` opens a connection per call. Hours of unattended parallel sprints can accumulate open file handles if any code path skips the context manager, degrading OS-level write reliability.
- **Not hardware**: confirmed by `diskutil verifyVolume` / `log show` showing no OS-level I/O errors.

**Ruled out:**
- Concurrent writer collision — WAL mode inherently allows one writer + N readers; this alone does not corrupt.
- Disk full — disk was at 7% capacity.

## Detection

Starting with issue #1901, `init_db()` runs `PRAGMA integrity_check` at server startup. If the DB is corrupt the server will **not start**, logging a CRITICAL message and the location of the most recent local backup.

You can also check manually:

```bash
sqlite3 apps/dashboard/commander.db "PRAGMA integrity_check"
# healthy → "ok"
# corrupt → one or more error lines
```

## Recovery

### Step 1 — Identify the most recent local backup

```bash
ls -lt apps/dashboard/.commander/db-backups/*.bak | head -5
```

Local rolling backups are written hourly to `.commander/db-backups/` and kept for 5 generations (~5 hours of coverage).

### Step 2 — Stop the server

```bash
kill -9 $(cat apps/dashboard/prd.pid)
rm -f apps/dashboard/prd.pid
```

If port 8000 is still held after kill:

```bash
lsof -i :8000 -sTCP:LISTEN   # find orphan PID
kill -9 <pid>
```

### Step 3 — Move the corrupt DB aside

```bash
mv apps/dashboard/commander.db apps/dashboard/commander.db.corrupt
mv apps/dashboard/commander.db-wal apps/dashboard/commander.db-wal.corrupt 2>/dev/null || true
mv apps/dashboard/commander.db-shm apps/dashboard/commander.db-shm.corrupt 2>/dev/null || true
```

### Step 4 — Restore from local backup

```bash
cp apps/dashboard/.commander/db-backups/commander.db.YYYYMMDD_HHMMSS_ffffff.bak \
   apps/dashboard/commander.db
```

Or restore from the authority-DB repo backup (if the local backups are also missing):

```bash
python3 services/sprint_manager/backup.py restore-db \
    --from <COMMANDER_BACKUP_REPO_URL> \
    --target apps/dashboard/commander.db \
    --force
```

### Step 5 — Verify the restored DB

```bash
sqlite3 apps/dashboard/commander.db "PRAGMA integrity_check"
# must return "ok"
```

### Step 6 — Restart the server

```bash
bash scripts/start_prd.sh
```

The server startup will run `PRAGMA integrity_check` and abort cleanly if the restore was incomplete.

### Step 7 — Reconcile against GitHub

After restart, open the dashboard → History tab for each project and click **Reconcile** on any sprint that shows an inconsistent state. The reconciler re-reads GitHub labels and corrects the local DB lifecycle fields without modifying GitHub.

For a full sweep across all projects, load the History tab for each project — auto-reconcile runs in the background (throttled, mirror-backed).

## Preventive Measures (issue #1901)

| Measure | Detail |
|---------|--------|
| Startup integrity check | `init_db()` runs `PRAGMA integrity_check`; aborts with clear message if corrupt |
| Hourly local rolling backups | `backup_db_local()` writes `.bak` to `.commander/db-backups/`, 5 deep |
| WAL mode | Enabled on every connection via `PRAGMA journal_mode=WAL` |
| Busy timeout | `PRAGMA busy_timeout=5000` on every connection prevents lock-wait crashes |
| WAL passive checkpoint | `run_wal_checkpoint()` available for operator or periodic maintenance |
| Authority-DB repo backup | Existing 6-hour SQL-dump push to `COMMANDER_BACKUP_REPO` (if configured) |

## Useful Commands

```bash
# Check WAL file size (large WAL = checkpoint not running)
ls -lh apps/dashboard/commander.db-wal

# Manual WAL checkpoint (safe, non-blocking)
python3 -c "
import sys; sys.path.insert(0, 'apps/dashboard')
import os; os.environ['DB_PATH'] = 'apps/dashboard/commander.db'
import db; print(db.run_wal_checkpoint())
"

# List local backups
python3 -c "
import sys; sys.path.insert(0, 'apps/dashboard')
import backup
for p in backup.list_local_backups(p := __import__('pathlib').Path('apps/dashboard/.commander/db-backups')): print(p)
"

# Verify DB integrity
python3 -c "
import sys; sys.path.insert(0, 'apps/dashboard')
import os; os.environ['DB_PATH'] = 'apps/dashboard/commander.db'
import db; print(db.check_db_integrity(db.DB_PATH))
"
```
