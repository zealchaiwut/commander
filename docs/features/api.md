# API Reference

All endpoints are served by the FastAPI app at `http://localhost:8000` (PRD)
or `http://localhost:8001` (UAT). No authentication — single-user, local only.

---

## Health & Environment

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check — returns `{"status": "ok"}` |
| `GET` | `/api/environment` | Returns current environment (`prd` or `uat`) and version |

---

## Agent Events & Token Usage

These endpoints are called by the Claude Code hooks in `hooks/`.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/agent-event` | Receive an agent event (tool_used, agent_finished, etc.) |
| `GET` | `/api/agents` | List active agents with last-seen timestamp |
| `GET` | `/api/events` | List recent agent events (paginated) |
| `DELETE` | `/api/events/test` | Delete all events flagged as test data |
| `GET` | `/events` | SSE stream — pushed to the Agents tab in real time |
| `POST` | `/api/token-usage` | Record token usage for a completed agent run |
| `GET` | `/api/debug/token-usage` | Raw token usage rows |
| `GET` | `/api/debug/token-usage/by-agent-model` | Token usage grouped by agent role and model |

---

## Projects

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/projects` | List all tracked projects |
| `POST` | `/api/projects` | Add a project by repo name |
| `DELETE` | `/api/projects/{owner}/{repo_name}` | Remove a tracked project |
| `GET` | `/api/project-details` | Full project detail: tickets grouped by status, sprint label, assignee |
| `POST` | `/api/projects/{owner}/{repo_name}/approve-batch` | Batch-approve all UAT tickets for a project |
| `POST` | `/api/projects/init` | Init a new project (clone repos, create sprint.yaml) |
| `GET` | `/api/repo/config` | Repository configuration (labels, sprint prefix) |

---

## Issues

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/issues` | List issues with optional label/state filters |
| `GET` | `/api/open-issues` | List open issues for sprint planning |
| `POST` | `/api/issues/{issue_id}/approve` | Approve a UAT ticket (sets `UAT-approved`, closes issue) |
| `POST` | `/api/issues/{issue_id}/reject` | Reject a UAT ticket (sets `needs-rework`) |
| `GET` | `/api/issues/{issue_id}/test-report` | Fetch the test report comment for an issue |
| `POST` | `/api/issues/{issue_id}/sprint-label` | Add or update the `sprint-N` label on an issue |

---

## Sprints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sprints` | List all sprint labels in the repo |
| `POST` | `/api/sprints/create` | Create a new sprint label |
| `POST` | `/api/sprints/delete-empty` | Delete sprint labels that have no open issues |
| `GET` | `/api/sprints/goal` | Get the current sprint goal |
| `POST` | `/api/sprints/goal` | Set the sprint goal |
| `GET` | `/api/sprints/order` | Get the sprint ordering list |
| `POST` | `/api/sprints/order` | Update the sprint ordering list |

---

## Sprint Runner

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/sprints/run` | Start the sprint manager for a given sprint label |
| `GET` | `/api/sprints/running` | Check whether a sprint is currently running |
| `DELETE` | `/api/sprints/run/{sprint_label}` | Kill a running sprint |
| `POST` | `/api/sprints/{sprint_label}/rerun` | Reset ticket labels and rerun a sprint |
| `GET` | `/api/sprints/{sprint_label}/live` | Live snapshot of a running sprint — counts, current ticket, active agent, last 50 log lines, and locked `issues[]` array (see below) |
| `GET` | `/api/sprints/{sprint_label}/live/stream` | SSE stream — pushed live sprint events |
| `GET` | `/api/sprints/{sprint_label}/finish-card` | Summary card data for a sprint — always returns HTTP 200 (see response shape below) |
| `GET` | `/api/sprint-status` | Get current sprint run status (per-ticket states) |
| `POST` | `/api/sprint-status` | Update sprint run status (called by sprint manager) |
| `GET` | `/api/sprint-summary` | Get the sprint summary for the active or last sprint |
| `GET` | `/api/sprint-history` | List completed sprint summaries |
| `GET` | `/api/sprint-history-content` | Get the raw Markdown content of a sprint summary |

### `/api/sprints/{sprint_label}/live` response shape

```json
{
  "time_spent_sec": 342,
  "started_at": "2026-05-29T00:00:00+07:00",
  "current_ticket": { "number": 307, "title": "Lock running sprint card…" },
  "active_agent": { "name": "coder", "model": "claude-sonnet-4-6", "pid": 12345 },
  "recent_log_lines": [
    { "timestamp": "00:05:12", "type": "info", "message": "Starting coder…" }
  ],
  "issues": [
    {
      "number": 305,
      "title": "Lock sprint-N label…",
      "status": "done",
      "agent_status": null,
      "agent": null,
      "elapsed_secs": 180,
      "size": "M"
    }
  ]
}
```

`issues[]` is sourced from the locked launch snapshot (not live GitHub queries), so
tickets whose `sprint-N` label was stripped mid-run still appear. `status` is one of
`pending`, `in-progress`, `done`, `skipped`. `agent_status` is `running`, `failed`,
or `null`.

### `/api/sprints/{sprint_label}/finish-card` response shape

Always returns **HTTP 200**. The `state` field determines which other fields are present.

> **Breaking change (sprint 52, issue #672):** this endpoint previously returned
> HTTP 404 when a sprint had never been run. It now returns HTTP 200 with
> `state: "no_data"`. Clients must check the `state` field in the response body
> rather than relying on HTTP status codes to determine whether a sprint has run.

**`state: "no_data"`** — sprint has never been run (no state file on disk):

```json
{
  "sprint_label":  "sprint-99",
  "sprint_number": 99,
  "state":         "no_data"
}
```

**`state: "running"`** — sprint is currently executing:

```json
{
  "sprint_label":    "sprint-10",
  "sprint_number":   10,
  "state":           "running",
  "in_flight_count": 1,
  "pending_count":   3,
  "done_count":      2,
  "wall_clock_secs": 342.0,
  "started_at":      "2026-05-29T00:00:00Z"
}
```

**`state: "completed"` / `"has_rework"` / `"cancelled"`** — sprint finished:

```json
{
  "sprint_label":      "sprint-10",
  "sprint_number":     10,
  "state":             "completed",
  "done_count":        5,
  "failed_count":      0,
  "skipped_count":     0,
  "rework_count":      0,
  "wall_clock_secs":   1800.0,
  "ended_at":          "2026-05-29T00:30:00Z",
  "summary_issue_url": "https://github.com/owner/repo/issues/123",
  "summary_issue_num": 123
}
```

---

## Sprint Planning & Management

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sprint-planning/issues` | Get issues available for sprint assignment |
| `POST` | `/api/sprint-planning/assign` | Assign selected issues to a sprint label |
| `GET` | `/api/sprint-management/issues` | Get the Sprint Mgmt panel view (issues grouped by sprint) |
| `GET` | `/api/plan-usage` | Claude API plan usage (token budget remaining) |

---

## Tickets (Draft & Create)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/tickets/draft` | Generate a draft ticket via the BA agent (accepts file attachments) |
| `POST` | `/api/tickets/create` | Create a GitHub issue from a drafted ticket body |

---

## Alerts

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/alerts` | Create an alert (shown on the dashboard banner) |
| `GET` | `/api/alerts` | List active alerts |
| `DELETE` | `/api/alerts/{idx}` | Dismiss an alert by index |

---

## Settings

Read effective settings (global defaults merged with project overrides) and write override values. Secret fields are never returned as raw values — they appear as boolean presence flags (e.g. `"github_token_set": true`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/settings` | Read effective global settings. Non-secret fields returned with defaults applied; secrets shown as `<field>_set` booleans. |
| `PUT` | `/api/settings` | Write a global override. Only supplied keys are written. Returns `400` for unknown keys, `422` for raw secret values. |
| `GET` | `/api/projects/{slug}/settings` | Read effective project settings with project overrides merged over global. Returns `404` if the project slug does not exist. |
| `PUT` | `/api/projects/{slug}/settings` | Write a project-level override. Global settings are not affected. Returns `400` for unknown keys, `422` for raw secret values, `404` if project not found. |

---

## Home

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/home` | Aggregated Home page payload: summary stats, per-project cards, and last 5 activity events. Per-project data is cached 30 s. Always returns HTTP 200 — failing projects degrade gracefully. |

---

## Sprint Logs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/sprints/{sprint_label}/dispatch-log` | Return the tail of the most recent sprint run log file (`sprint-run-<label>-*.log`). Accepts `?tail_lines=N` (max 2000). |
| `GET` | `/api/sprints/{sprint_label}/issue/{issue_num}/log` | Return the tail of the most recent per-issue log (`sprint-issue-<N>.log`). Accepts `?tail_lines=N` (max 2000). |

---

## Pages

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Home page (serves `static/index.html`) |
| `GET` | `/home` | Alias for `/` |
| `GET` | `/home-preview` | Static preview of the Home stat card layout |
| `GET` | `/projects/{path}` | Project detail page (SPA deep-link handler, serves `static/project.html`) |
