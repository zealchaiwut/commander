<!-- Last reviewed: 2026-05-28 -->

# Commander Travel Playbook

Reference for remote operation over iPad + Tailscale. Readable in 5 minutes,
actionable for 90% of issues that come up at 3am in a foreign timezone.

---

## Pre-Travel Checklist

Work through this list on the Mac mini the day before departure.

### Mac mini stability

- [ ] Sleep disabled:
  ```bash
  sudo pmset -a disablesleep 1
  ```
  Verify: `pmset -g | grep disablesleep` should show `disablesleep 1`

- [ ] Auto-login on boot enabled:
  System Settings > General > Login Items & Extensions > enable auto-login for your user account.

- [ ] launchd service installed and running:
  ```bash
  bash scripts/install_launchd.sh
  launchctl list | grep commander
  ```
  Expected: a line with `com.commander.dashboard` and exit code `0`.

### Network

- [ ] Tailscale up on Mac mini:
  ```bash
  tailscale status
  ```
  Expected: your Mac mini appears in the peer list with a `100.x.x.x` address.

- [ ] MagicDNS enabled in Tailscale admin console (`https://login.tailscale.com/admin/dns`).

- [ ] Tailscale installed and logged in on iPad.

- [ ] **Critical:** tested dashboard access from iPad on **cellular** (disable WiFi before testing):
  Open `http://<mac-mini>.tail-xxxx.ts.net:8000` in browser. Dashboard must load.

### SSH

- [ ] SSH enabled on Mac mini:
  System Settings > General > Sharing > Remote Login: On

- [ ] SSH key from iPad terminal app (Blink or Termius) added to `~/.ssh/authorized_keys` on Mac mini:
  ```bash
  cat ~/.ssh/authorized_keys   # should contain your iPad's public key
  ```

- [ ] Verified SSH login from iPad over Tailscale (on cellular, not local WiFi):
  ```
  ssh <your-user>@<mac-mini>.tail-xxxx.ts.net
  ```

### Auth tokens

- [ ] GitHub auth is valid:
  ```bash
  gh auth status
  ```
  Expected: `Logged in to github.com as zealchaiwut`

- [ ] Claude Code auth is fresh:
  ```bash
  claude --version
  claude auth status
  ```

### Dashboard health

- [ ] `/api/health` returns 200:
  ```bash
  curl -s http://localhost:8000/api/health | python3 -m json.tool
  ```
  Expected: `"status": "ok"`

- [ ] Backup gist exists and is recent:
  ```bash
  curl -s http://localhost:8000/api/backup/status
  ```
  Note the gist URL — save it in 1Password or a note on iPad.

### Sprint state

- [ ] All in-flight sprints completed or cancelled. Do not leave a sprint mid-run.
  ```bash
  cat .commander/sprint.yaml
  ```

---

## URLs You Will Need

Save these in 1Password or iPad Notes before leaving.

| Resource | URL |
|---|---|
| Dashboard | `http://<mac-mini>.tail-xxxx.ts.net:8000` |
| Diagnostics | `http://<mac-mini>.tail-xxxx.ts.net:8000/diagnostics` |
| Health JSON | `http://<mac-mini>.tail-xxxx.ts.net:8000/api/health` |
| Backup status | `http://<mac-mini>.tail-xxxx.ts.net:8000/api/backup/status` |
| Backup gist | `https://gist.github.com/<gist-id>` (get from `/api/backup/status`) |
| GitHub repo | `https://github.com/zealchaiwut/commander` |
| Tailscale admin | `https://login.tailscale.com/admin` |

---

## Common Failures and Recovery

---

### Dashboard URL does not respond

**Symptom:** Browser shows "connection refused" or times out on `http://<mac-mini>:8000`.

**Likely cause:** Dashboard process crashed and launchd has not restarted it yet, or launchd service is not installed.

**Recovery from iPad:**
1. Wait 15 seconds — launchd restarts automatically with a 5-second throttle.
2. If still down, open the Tailscale app and confirm the Mac mini is online (green dot).
3. If Mac mini is online, proceed to SSH recovery.

**Recovery via SSH:**
```bash
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

# Check if the process is running:
launchctl list | grep commander

# Force restart the service:
launchctl kickstart -k gui/$(id -u)/com.commander.dashboard

# Confirm it came up:
curl -s http://localhost:8000/api/health
```

---

### Dashboard responds but /api/health shows down

**Symptom:** Dashboard UI loads but is blank or shows errors. `GET /api/health` returns `status: "down"` or a non-200 response.

**Likely cause:** Database locked, GitHub token expired, or a background task is stuck.

**Recovery from iPad:**
1. Open `http://<mac-mini>.tail-xxxx.ts.net:8000/diagnostics` for the diagnostics page.
2. Check the error shown there — it usually names the failing component.

**Recovery via SSH:**
```bash
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

# Read recent logs:
tail -f ~/Library/Logs/commander-dashboard.out.log

# Check GitHub token:
gh auth status

# Restart the dashboard:
launchctl kickstart -k gui/$(id -u)/com.commander.dashboard
```

---

### GitHub auth expired

**Symptom:** `/api/health` shows GitHub as `down`. `gh auth status` shows `not logged in` or token expired.

**Likely cause:** GitHub token was revoked or expired.

**Recovery from iPad:**
SSH into Mac mini is required for this recovery.

**Recovery via SSH:**
```bash
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

gh auth login
# Choose: GitHub.com > HTTPS > Login with a web browser
# Follow the one-time code flow — open GitHub in iPad browser

# Verify:
gh auth status
```

---

### Claude Code auth expired

**Symptom:** Coder or Tester agent fails immediately with auth error. `claude auth status` shows expired.

**Likely cause:** Claude Code session expired (typically after 30 days).

**Recovery from iPad:**
SSH into Mac mini is required for this recovery.

**Recovery via SSH:**
```bash
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

claude auth login
# Follow the browser-based auth flow on your iPad

# Verify:
claude auth status
```

---

### Sprint stuck (running but no log activity)

**Symptom:** Sprint shows as running in the dashboard but no new log lines appear for more than 5 minutes.

**Likely cause:** A Coder or Tester agent subprocess hung, or the sprint manager process died without cleaning up the PID file.

**Recovery from iPad:**
1. Check the dashboard — if the spinner is running but last log is old, it is stuck.

**Recovery via SSH:**
```bash
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

# Find and kill the stuck sprint manager:
ps aux | grep sprint_manager
kill <pid>

# Remove the stale PID file if present:
rm -f .commander/sprints/sprint-*.pid

# Restart the sprint from the dashboard or CLI:
python3 services/sprint_manager/sprint_manager.py --sprint <sprint-label>
```

---

### projects.json or sprint.yaml lost — restore from gist

**Symptom:** Dashboard shows no projects. Sprint manager errors on missing config.

**Likely cause:** File was accidentally deleted, or DB/filesystem corruption.

**Recovery from iPad:**
Open the backup gist URL and copy the file contents manually.

**Recovery via SSH:**
```bash
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

# Restore from the backup gist (replace <gist-id> with the ID from /api/backup/status):
python3 -m services.sprint_manager.backup restore --gist-id <gist-id>

# Verify:
cat apps/dashboard/projects.json
cat .commander/sprint.yaml
```

---

### Tester rejected a ticket repeatedly

**Symptom:** A ticket has been rejected 3+ times and is bouncing between Coder and Tester.

**Likely cause:** Ambiguous acceptance criteria, environment-specific test failure, or a flaky test.

**Escalation path:**
1. Read the test report comments on the GitHub issue.
2. SSH in and run the failing test manually:
   ```bash
   ssh <your-user>@<mac-mini>.tail-xxxx.ts.net
   cd ~/dev/commander/tester
   python3 -m pytest apps/dashboard/tests/test_<feature>__<N>.py -v
   ```
3. If the test failure is environment-specific (not a code bug), add a skip marker with a comment and re-run the Tester.
4. If the AC is ambiguous, update the GitHub issue body with a clarification and restart the sprint.

---

### Mac mini totally unreachable (Tailscale shows offline)

**Symptom:** Mac mini does not appear in Tailscale peer list, or shows as offline.

**Likely cause:** Power outage, kernel panic, or Tailscale daemon crashed on the Mac.

**Recovery from iPad:**
1. Check Tailscale admin console (`https://login.tailscale.com/admin/machines`) — confirm the last-seen time.
2. If offline for less than 5 minutes: wait, the Mac may be rebooting.
3. If offline for longer: the Mac mini needs physical access or a remote power cycle.
   - If you have a smart plug on the Mac mini's power outlet, cycle the power.
   - If not: there is no remote recovery — contact someone with physical access.

**Recovery via SSH:**
Not available when Mac is unreachable. Physical access is required.

---

### iPad battery dies mid-sprint

**Symptom:** iPad runs out of battery while a sprint is in progress.

**Likely cause:** Normal battery depletion.

**Impact:** The sprint continues running on the Mac mini — it does not depend on the iPad being connected.

**Recovery:**
1. Charge the iPad.
2. Re-open the dashboard URL in the browser.
3. The sprint state is persisted in `.commander/sprint.yaml` — no data is lost.

---

## SSH Commands You Will Forget

```bash
# Connect to Mac mini over Tailscale:
ssh <your-user>@<mac-mini>.tail-xxxx.ts.net

# Attach to the live tmux session:
tmux attach -t commander

# Follow the dashboard stdout log:
tail -f ~/Library/Logs/commander-dashboard.out.log

# Follow the dashboard stderr log:
tail -f ~/Library/Logs/commander-dashboard.err.log

# Check if the launchd service is registered:
launchctl list | grep commander

# Force-restart the dashboard service:
launchctl kickstart -k gui/$(id -u)/com.commander.dashboard

# Stop the dashboard service:
launchctl stop gui/$(id -u)/com.commander.dashboard

# Restore config files from backup gist:
python3 -m services.sprint_manager.backup restore --gist-id <gist-id>

# Run a test suite manually:
cd ~/dev/commander/tester
python3 -m pytest apps/dashboard/tests/ -v

# Check GitHub auth:
gh auth status

# Re-login to GitHub (opens browser flow):
gh auth login

# Check Claude Code auth:
claude auth status

# Check all running Python processes:
ps aux | grep python

# Check what is listening on port 8000:
lsof -i :8000
```

---

## Fallback Paths

### If iPad fails

Use your phone with Tailscale installed and Termius (iOS) or JuiceSSH (Android).
The SSH key is stored in 1Password — export it to the terminal app.

### If both iPad and phone fail

Use any laptop or borrowed device:
1. Install Tailscale.
2. Log in with your Tailscale account (credentials in 1Password).
3. SSH into the Mac mini using the key from 1Password.
4. Alternatively, use the GitHub web UI to read sprint state and close/reopen issues.

### If Mac mini truly dies

What is recoverable from the backup gist:
- `projects.json` — all tracked repositories and sprint config
- `sprint.yaml` — current sprint state

What is **not** recoverable without the Mac mini:
- `commander.db` — agent event history and token usage logs
- In-progress Coder/Tester work (uncommitted code)
- Any in-memory sprint state that was not flushed to `sprint.yaml`

Steps after hardware replacement:
1. Clone the repo on the new machine.
2. Run `python3 scripts/init_project.py --nested` to set up the project layout.
3. Restore config from the backup gist (see "projects.json or sprint.yaml lost" above).
4. Install and start the launchd service: `bash scripts/install_launchd.sh`
5. Re-auth GitHub: `gh auth login`
6. Re-auth Claude Code: `claude auth login`
7. Run `curl http://localhost:8000/api/health` to confirm the dashboard is up.

---

## What to Verify on Return

Run through this list after getting home and reconnecting to local network.

- [ ] `launchctl list | grep commander` — service is still running.
- [ ] `curl http://localhost:8000/api/health` — dashboard is healthy.
- [ ] `gh auth status` — GitHub token is still valid.
- [ ] `claude auth status` — Claude Code session is still valid.
- [ ] Check the dashboard sprint board for any tickets that got stuck during travel.
- [ ] Check GitHub Issues for any open `needs-rework` or `blocked` tickets.
- [ ] Review `~/Library/Logs/commander-dashboard.err.log` for any errors that accumulated.
- [ ] Check your GitHub Actions or API billing page for unexpected usage spikes.
- [ ] Re-enable normal sleep if desired: `sudo pmset -a disablesleep 0`
- [ ] Run a fresh backup: trigger via `POST http://localhost:8000/api/backup` or from the dashboard.
