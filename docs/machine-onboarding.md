# Machine Onboarding Runbook

The single authoritative procedure for bringing up a **new Commander machine
from scratch** on macOS. Follow it top to bottom on a clean host and you will
reach a passing `doctor` run and a working first sprint without consulting any
other document.

> Audience: any engineer or operator provisioning a developer/operator machine.
> Scope: macOS only. CI/CD provisioning and bootstrap automation are out of
> scope — see [quickstart.md](quickstart.md) for the five-minute local install.

## Prerequisites

- macOS with `python3.12` available (e.g. `~/.local/bin/python3.12` or Homebrew)
- Node.js / `npm` (for installing the Claude Code CLI)
- A GitHub account with access to `zealchaiwut/commander`
- A GitHub personal access token with `repo` scope (for headless `gh` auth)
- A Claude Code OAuth token (generated below with `claude setup-token`)

Work through the sections **in order** (or use [Quick bootstrap](#quick-bootstrap-setup_machinesh) on a fresh Mac):

1. [Quick bootstrap](#quick-bootstrap-setup_machinesh) *(one command — recommended)*
2. [Clone layout](#1-clone-layout)
3. [Venv per clone](#2-venv-per-clone)
4. [Claude install + setup-token](#3-claude-install--setup-token)
5. [gh auth + token](#4-gh-auth--token)
6. [install_launchd.sh](#5-install_launchdsh)
7. [Doctor run](#6-doctor-run)
8. [First sprint smoke test](#7-first-sprint-smoke-test)
9. [Failure Signatures](#8-failure-signatures)

---

## Quick bootstrap (setup_machine.sh)

One command from a fresh clone: venv + requirements (includes `code-review-graph`),
`.env`, prd/uat layout, **caveman + code-review-graph** on every clone under
`~/dev/commander/`, then doctor.

```bash
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/prd
cd ~/dev/commander/prd
bash scripts/setup_machine.sh
```

**Second Mac mini** (clones and venv already exist): reinstall agent skills and
rebuild CRG graphs without repeating the full bootstrap:

```bash
cd ~/dev/commander/uat   # or prd — any clone with a venv
bash scripts/setup_machine.sh --resetup-machine
```

Or target one clone:

```bash
bash scripts/install_agent_skills.sh --force --clone uat
```

Restart Claude Code / Cursor after either command (`/mcp` should list
`code-review-graph`). See [features/agent-skills.md](features/agent-skills.md).

Before an **overnight sprint**, refresh agent worktree graphs (headless dispatches
do not run CRG hooks reliably):

```bash
bash scripts/update_crg_graphs.sh
```

Sprint manager also runs a best-effort `update` before each coder/tester ticket.

---

## 1. Clone layout

Commander runs each agent role in its own clone so that the Coder, Tester, and
UAT working trees never collide. Use the **nested layout** for a new machine —
all clones live under one project folder, and `.commander/` (sprint config)
sits *outside* every git working tree so it can never be committed by accident.

Create this directory structure:

```
~/dev/commander/
  prd/               # primary working clone — master branch
  coder/             # coder agent clone — develop branch
  tester/            # tester agent clone — develop branch
  uat/               # UAT clone — develop branch (optional)
  .commander/        # sprint config — at project root, outside any git clone
    sprint.yaml
    logs/
    sprints/
    alerts/
```

Clone the repo once per role:

```bash
mkdir -p ~/dev/commander
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/prd
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/coder
git clone https://github.com/zealchaiwut/commander.git ~/dev/commander/tester

# The agent clones track develop; prd stays on master.
git -C ~/dev/commander/coder  checkout develop
git -C ~/dev/commander/tester checkout develop
```

> The optional `uat/` clone (also on `develop`) is only needed if you sign off
> UAT on this machine. Add it the same way and `git checkout develop`.

---

## 2. Venv per clone

**Each clone needs its own virtualenv.** A venv hardcodes absolute paths in its
shim binaries, so a venv may never be copied between clones or machines —
copying one produces `ModuleNotFoundError: No module named 'encodings'`. Always
create a fresh venv inside each clone.

Run these exact commands **once per clone** (`prd`, `coder`, `tester`, and
`uat` if present):

```bash
cd ~/dev/commander/prd          # repeat for coder, tester, uat
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Verify the venv is the active interpreter inside the clone:

```bash
which python          # → ~/dev/commander/prd/venv/bin/python
```

> If you ever copied a venv and hit `No module named 'encodings'`, delete and
> recreate it: `rm -rf venv && python3.12 -m venv venv && source
> venv/bin/activate && pip install -r requirements.txt`.

---

## 3. Claude install + setup-token

The Coder and Tester agents shell out to the `claude` CLI, so it must be
installed and authenticated on the host.

**Install** the Claude Code CLI globally with npm:

```bash
npm install -g @anthropic-ai/claude-code
claude --version          # confirm the binary is reachable
```

**Generate a long-lived OAuth token** for headless (unattended) use. The
launchd service is detached from your login session and cannot open a browser,
so it authenticates with a token rather than an interactive login:

```bash
claude setup-token
```

`claude setup-token` prints a `CLAUDE_CODE_OAUTH_TOKEN` value. Copy it — you
will pass it to the launchd service in [section 5](#5-install_launchdsh) so the
unattended runner can authenticate without a browser. (For attended local dev
you can instead run `claude` once and complete `/login` interactively.)

---

## 4. gh auth + token

The dashboard and the sprint manager use the GitHub CLI (`gh`) for all issue and
branch operations. There are two authentication paths — set up **both**: the
interactive path for your own terminal use, and the headless token path for the
launchd service.

**Interactive (attended terminal):**

```bash
gh auth login          # follow the browser/device prompts
gh auth status         # → "Logged in to github.com as <user>"
```

**Headless (token-based, for the launchd service):**

A process launched by launchd is detached from the login session and **cannot
read the macOS keychain** where `gh auth login` stores its credentials — so at
startup the repo check fails with *"does not exist or is inaccessible"*. `gh`
prefers the `GH_TOKEN` environment variable over the keychain, so the
unattended service authenticates via a token instead.

Create a GitHub personal access token with `repo` scope, then either export it
for the install step or pass it directly to `install_launchd.sh` (next
section). To verify a token works headlessly without touching the keychain:

```bash
GH_TOKEN=ghp_xxxxxxxxxxxxxxxx gh auth status
```

This must report authenticated with **no browser interaction**. The token is
injected into the launchd plist `EnvironmentVariables` (and the agent `.env`) in
the next section — it is never hard-coded in the repo.

---

## 5. install_launchd.sh

launchd is the authoritative unattended runner. Install the dashboard as a
macOS LaunchAgent, **passing both headless tokens** so the detached service can
reach GitHub and Claude without a login session.

`install_launchd.sh` resolves the on-disk directories of `claude` and `gh`
dynamically and writes them into the plist `PATH`, so the service finds the
tools even when they live outside the standard locations (e.g. `~/.local/bin`).

Run the exact invocation, providing the gh token as an argument and the claude
OAuth token via the environment:

```bash
cd ~/dev/commander/prd
CLAUDE_CODE_OAUTH_TOKEN=<token-from-claude-setup-token> \
  bash scripts/install_launchd.sh --gh-token ghp_xxxxxxxxxxxxxxxx
```

- `--gh-token` injects `GH_TOKEN` into the plist `EnvironmentVariables` block
  and the agent `.env` (headless `gh` auth). If omitted, the script reads
  `$GH_TOKEN` / `$GITHUB_TOKEN` from the environment.
- `CLAUDE_CODE_OAUTH_TOKEN` is carried into the service environment so the
  unattended `claude` runs are authenticated headlessly.

Verify the service loaded:

```bash
launchctl list | grep commander          # → com.commander.dashboard
```

The dashboard is now serving on `http://localhost:8000` and will restart on
crash (`KeepAlive` → restart only on non-zero exit).

---

## 6. Doctor run

Before running any sprint, validate the host with the install-time doctor. It
checks every prerequisite — tools on PATH, auth, git identity, the venv, a
writable `DB_PATH`, and the launchd plist PATH/token environment — and prints a
named `[PASS]`/`[FAIL]` line plus the exact fix for each failure.

Run it from the clone:

```bash
cd ~/dev/commander/prd
source venv/bin/activate
python scripts/doctor.py
```

A **passing** run looks like this — every check is `[PASS]` and the exit code
is `0`:

```
Commander host doctor — pre-sprint validation (issue #828)
==========================================================
[PASS] claude CLI reachable
       /usr/local/bin/claude (1.2.3 (Claude Code))
[PASS] gh authenticated
       /usr/local/bin/gh
[PASS] git identity configured
       Your Name <you@example.com>
[PASS] venv and packages importable
       /Users/you/dev/commander/prd/venv — 6 core packages
[PASS] DB_PATH writable
       ./commander.db
[PASS] launchd plist PATH includes tool dirs
       /Users/you/Library/LaunchAgents/com.commander.dashboard.plist
[PASS] headless auth tokens in plist
       /Users/you/Library/LaunchAgents/com.commander.dashboard.plist
==========================================================
All 7 checks passed — host is ready to run a sprint.
```

If any check is `[FAIL]`, the doctor prints an indented `↳ fix:` line with the
exact remediation and exits non-zero. Apply the fix and re-run until every line
is `[PASS]`. For a machine-readable report, add `--json`.

---

## 7. First sprint smoke test

With the doctor green, confirm the machine can actually drive a sprint. The
minimum check is to run the sprint manager against a sprint label — it discovers
the sprint config, runs the pre-dispatch auth probe, and works the backlog
tickets through the Coder → Tester loop.

```bash
cd ~/dev/commander/prd
source venv/bin/activate
python3 services/sprint_manager/sprint_manager.py sprint-1
```

**Expected:** the sprint manager starts without error, reports the tickets it
found under the `sprint-1` label, and dispatches the Coder for the first ticket.
Seeing the dispatch begin (and a feature branch created) confirms the machine is
operational end to end — auth, tools, clones, and venv are all working.

> Use a sprint label that actually has a backlog ticket on this repo. To watch
> the run live, open the dashboard at `http://localhost:8000`.

---

## 8. Failure Signatures

Common startup failures and their concrete fixes. Each signature maps to one
root cause — apply the fix and re-run `python scripts/doctor.py` to confirm.

| Signature | Root cause | Fix |
|---|---|---|
| `claude CLI not found` | The service PATH does not include the directory containing `claude`. A launchd process is detached from your login shell, so it does not inherit your interactive PATH. | Ensure the service PATH is set correctly: reinstall the service with `bash scripts/install_launchd.sh` — it resolves `claude`'s on-disk directory dynamically and writes it into the plist `PATH`. Confirm with `python scripts/doctor.py`. |
| `Not logged in` | The Claude OAuth token is missing or was not passed to the service. The unattended runner cannot open a browser to log in interactively. | Provide the OAuth token to the service: run `claude setup-token`, then reinstall passing it — `CLAUDE_CODE_OAUTH_TOKEN=<token> bash scripts/install_launchd.sh --gh-token <gh-token>` — so the token reaches the plist `EnvironmentVariables`. |
| `repo inaccessible` at startup | `gh` has no headless auth configured. The launchd service cannot read the macOS keychain where `gh auth login` stores credentials, so the startup repo check fails. | Configure headless `gh` auth with a `repo`-scoped token: reinstall with `bash scripts/install_launchd.sh --gh-token ghp_xxxx`, which injects `GH_TOKEN` into the plist and the agent `.env`. Verify with `GH_TOKEN=ghp_xxxx gh auth status`. |

---

## Next steps

- [quickstart.md](quickstart.md) — the five-minute local install
- [tutorial.md](tutorial.md) — multi-clone setup and agent roles
- [runbook.md](runbook.md) — day-two operations (log rotation, secrets hygiene)
- [workflow.md](workflow.md) — the Bulk Create → Run → Finish/Rerun flow
