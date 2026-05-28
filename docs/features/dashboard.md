# Dashboard

The Commander dashboard is a FastAPI-served single-page app at `localhost:8000`
(PRD) and `localhost:8001` (UAT). It gives you a live view of all Claude Code
agents running across your projects, a sprint board, and a UAT sign-off UI.

---

## UI Overview

The dashboard is organised into two navigation levels:

### Global tabs (top nav)

| Tab | What it shows |
|---|---|
| **Home** | Summary stat cards, per-project active/idle cards, and last 5 agent events |
| **Sprint Mgmt** | Sprint planning, sprint runner, and per-sprint progress for the selected project |
| **Agents** | Live agent event feed — tool calls, token usage, finish events |

The root route `/` and `/home` both load the Home tab (previously called "Overview").

### Project drill-in

Clicking an active project card from the Home tab opens the project detail view in
`/projects/<owner>/<repo>`. A persistent sidebar lists all tracked projects; a
sub-tab bar lets you switch between Sprint Mgmt, Agents, and Tickets without a
full page reload. The project page shows:

- **Active tickets** grouped by status: In Progress, SIT, UAT, Backlog
- Sprint label per ticket (the `sprint-N` GitHub label)
- Batch "Approve all UAT" button per group
- Individual Approve / Reject buttons on each UAT ticket

---

## PRD and UAT Environments

Commander runs two isolated instances:

| | PRD | UAT |
|---|---|---|
| Port | 8000 | 8001 |
| Branch | `master` | `develop` |
| Database | `apps/dashboard/commander.db` | `uat/apps/dashboard/commander-uat.db` |

Agent hooks post events to the URL in their `HOOK_POST_TARGET` env var
(default `http://localhost:8000/api/agent-event`). UAT agents override this to
`http://localhost:8001/api/agent-event` via their `.claude/settings.json`.

---

## Home Tab

The Home tab (`/`) shows:

- **Stat cards** — counts for active agents, open tickets, running sprints, and UAT-pending tickets
- **Projects section** — active project cards (sprint progress, ticket counts) and an idle deck for projects with no open issues
- **Activity feed** — last 5 agent events across all projects (role, event type, timestamp)

---

## Sprint Mgmt Tab

The Sprint Mgmt tab has a toolbar at the top and a list of sprint blocks below.

### Toolbar

| Button | Action |
|---|---|
| Trigger Refresh | Re-fetches sprint data from GitHub without a full page reload |
| New Ticket | Opens the BA draft-ticket modal |
| New Sprint | Opens the New Sprint modal to create a sprint label |

### Sprint blocks

Each sprint is shown as a block with four visual states:

| State | Appearance |
|---|---|
| Idle | Grey — sprint has tickets but is not running |
| Running | Blue with live progress counter and per-ticket status indicators |
| Completed | Green — all tickets merged or skipped |
| Failed | Red — one or more tickets failed the tester gate |

A running sprint block shows a **live log panel** below it. The panel tails the
sprint dispatch log, refreshes on demand, and includes a Cancel button to kill
the sprint.

A **sticky Backlog block** always appears at the bottom of the sprint list,
showing all issues not yet assigned to a sprint. It cannot be reordered past
other sprint blocks via drag-and-drop.

### Time tracking

Each ticket row in a sprint block shows elapsed wall-clock time (updated live
while the sprint runs). The sprint block header shows the total estimated and
elapsed time across all tickets.

---

## Sprint Progress Bar

Each active project card on the Home tab shows a sprint progress bar when a
sprint is active. The bar shows:

- Ticket count by status (done / UAT / in-progress / pending)
- Sprint label (e.g. "Sprint 7")
- A "Kill sprint" shortcut if the sprint manager is running

---

## UAT Sign-off

When a ticket's GitHub label is `UAT`, it appears in the UAT group on the
project drill-in. From here you can:

- **Approve** — moves the label to `UAT-approved` and closes the issue
- **Reject** — moves the label to `needs-rework` and adds a rejection comment
- **Approve all** — bulk-approves every ticket in the UAT group

After approving, merge `develop` to `master` manually to promote to PRD.

---

## Agent Event Feed

The Agents tab streams events from the SQLite database in real time via
Server-Sent Events (`GET /events`). Each row shows:

- Agent role (coder, tester, ba)
- Event type (tool_used, agent_finished, post_tool_used)
- Tool name or finish reason
- Token usage
- Timestamp

---

## Draft Ticket Modal

The **+ Draft Ticket** button (top-right on Overview) opens a modal where you
can describe a feature, optionally attach screenshots or files, and have the
BA agent generate a structured ticket with acceptance criteria. The draft is
shown for review before being filed as a GitHub issue.

---

## Init Project Modal

The **+ Add Project** button opens the Init Project modal. Enter a GitHub repo
(e.g. `owner/repo`) and a UAT port, and Commander will:

1. Clone the repo into the nested layout (`~/dev/<project>/main`, `uat/`, `coder/`, `tester/`)
2. Create `.commander/sprint.yaml` with the correct paths
3. Add the project to the tracked repos list

---

## Shell Shortcuts

Install once with `bash scripts/install_shell_shortcuts.sh`, then add
`source ~/.commander.zsh` to your `~/.zshrc`.

| Shortcut | Action |
|---|---|
| `start-prd` | Start PRD server on port 8000 |
| `start-uat` | Start UAT server on port 8001 |
| `stop-prd` / `stop-uat` / `stop-all` | Stop servers |
| `restart-prd` / `restart-uat` | Restart a server |
| `cmdr-status` | Show PID, port, branch, and running state |
