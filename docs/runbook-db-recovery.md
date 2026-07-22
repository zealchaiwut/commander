# Runbook: commander.db Recovery

## Incident Pattern

The live `commander.db` was found truncated (e.g. 4 KB from ~30 MB). Every SQLite operation returns `disk I/O error (10)`. All DB-backed endpoints return 500.

**Root-cause analysis (issue #1901):**

- **Primary suspect — WAL checkpoint crash during unattended write.** SQLite's WAL mode writes new pages to a `-wal` file; a checkpoint moves them back to the main DB file. A crash mid-checkpoint can leave the main DB partially written and the `-wal` at 0 bytes, producing a truncated main file.
- **Contributing factor — fd exhaustion (#1749).** Before the #1749 fix, `get_conn()` opened a connection per call but never closed it. Hours of unattended parallel sprints accumulated open file handles, degrading OS-level write reliability. Fixed by converting `get_conn()` to a context manager in sprint-117.
- **Not hardware.** Confirmed by checking `diskutil verifyVolume` and `log show` — no OS-level I/O errors.

**Ruled out:**
- Concurrent writer collision — WAL mode inherently allows one writer + N readers; this alone does not corrupt.
- Disk full — disk was at 7% capacity.

## Detection

Starting from issue #1901, `init_db()` runs `PRAGMA integrity_check` at server startup. A corrupt DB causes an immediate abort with a CRITICAL log message and the location of the most recent local backup.

Manual check:

```bash
sqlite3 apps/dashboard/commander.db "PRAGMA integrity_check"
# healthy → "ok"
# corrupt → one or more error lines
```

## Recovery

### Step 1 — Identify the most recent local backup

```bash
ls -lt apps/dashboard/.commander/db-backups/*.bak 2>/dev/null | head -5
```

Local rolling backups are written hourly and kept for 5 generations (~5 hours of coverage). If no local backups are present, check the authority-DB repo backup (see Step 4 alternative).

### Step 2 — Stop the server

```bash
kill -9 $(cat apps/dashboard/prd.pid) 2>/dev/null || true
rm -f apps/dashboard/prd.pid
```

If port 8000 is still held:

```bash
lsof -i :8000 -sTCP:LISTEN   # find the orphan PID
kill -9 <pid>
```

### Step 3 — Move the corrupt DB aside

```bash
mv apps/dashboard/commander.db     apps/dashboard/commander.db.corrupt
mv apps/dashboard/commander.db-wal apps/dashboard/commander.db-wal.corrupt 2>/dev/null || true
mv apps/dashboard/commander.db-shm apps/dashboard/commander.db-shm.corrupt 2>/dev/null || true
```

### Step 4 — Restore from local backup

```bash
# Replace YYYYMMDD_HHMMSS_ffffff with the latest backup filename
cp apps/dashboard/.commander/db-backups/commander.db.YYYYMMDD_HHMMSS_ffffff.bak \
   apps/dashboard/commander.db
```

**Alternative — restore from authority-DB repo backup** (if local backups are absent):

```bash
python3 services/sprint_manager/backup.py restore-db \
    --from <COMMANDER_BACKUP_REPO_URL_or_local_path> \
    --target apps/dashboard/commander.db \
    --force
```

### Step 5 — Verify the restored DB

```bash
sqlite3 apps/dashboard/commander.db "PRAGMA integrity_check"
# must print "ok"
```

### Step 6 — Restart the server

```bash
bash scripts/start_prd.sh
```

The server's `init_db()` runs `PRAGMA integrity_check` at startup and aborts cleanly if the restore was incomplete.

### Step 7 — Reconcile against GitHub

Open the dashboard → History tab for each project and click **Reconcile** on any sprint showing an inconsistent state. Reconcile re-reads GitHub labels and corrects the local DB without modifying GitHub.

For a full sweep: load the History tab for each project — auto-reconcile runs in the background (throttled, mirror-backed).

## Preventive Measures (issue #1901)

| Measure | Detail |
|---------|--------|
| Startup integrity check | `init_db()` runs `PRAGMA integrity_check` and aborts with clear message if corrupt |
| Hourly local rolling backups | `backup_db_local()` writes `.bak` to `.commander/db-backups/`, 5 deep |
| WAL mode + busy_timeout | Set on every connection via `get_conn()` |
| Connection closing | `get_conn()` is a context manager — connections close on exit (#1749) |
| WAL passive checkpoint | `run_wal_checkpoint()` available for operator or periodic maintenance |
| Authority-DB repo backup | 6-hour SQL-dump push to `COMMANDER_BACKUP_REPO` (if configured) |

## Useful Commands

```bash
# Check WAL file size (large WAL = checkpoint hasn't run in a while)
ls -lh apps/dashboard/commander.db-wal

# Manual WAL checkpoint (safe, non-blocking)
python3 -c "
import sys; sys.path.insert(0, 'apps/dashboard')
import os; os.environ.setdefault('DB_PATH', 'apps/dashboard/commander.db')
import db; print(db.run_wal_checkpoint())
"

# List local backups newest-first
python3 -c "
import sys; sys.path.insert(0, 'services/sprint_manager')
import backup
for p in backup.list_local_backups(): print(p)
"

# Run a manual local backup right now
python3 -c "
import sys; sys.path.insert(0, 'services/sprint_manager')
import os, backup
from pathlib import Path
db_path = Path(os.environ.get('DB_PATH', 'apps/dashboard/commander.db'))
print(backup.backup_db_local(db_path))
"
```
