# Commander Agent Operate Guide

Canonical workflow recipes for external agents. The full API schema is at
`GET /openapi.json` (~60 routers). This guide documents the call chains an
external agent needs to operate the Commander platform end-to-end.

Served by `GET /api/agent-guide` as `{content, version}`.

---

## Recipe 1: Bulk Create Tickets

Create one or more tickets as a batch job, review the drafts, then post the
approved ones. The estimator runs automatically per ticket after posting.

### Step 1 — Submit the bulk job (202 Accepted)

```
POST /api/tickets/bulk
Content-Type: application/json

{
  "project": "owner/repo",
  "prompt": "Create tickets for <feature description>"
}
```

Response body:
```json
{"job_id": "<uuid>", "status": "running"}
```

### Step 2 — Poll the job status

```
GET /api/tickets/bulk/{job_id}
```

Key response fields:
- `status` — `"running"` | `"done"` | `"failed"`
- `tickets` — array of draft ticket objects with `title`, `body`, `size`

Or stream events via SSE:

```
GET /api/tickets/bulk/{job_id}/stream
```

### Step 3 — Post the approved drafts

```
POST /api/tickets/bulk/{job_id}/post-selected
Content-Type: application/json

{"selected_indices": [0, 1, 2]}
```

The estimator fires automatically for each posted ticket.

---

## Recipe 2: Create and Run a Sprint

### Step 1 — Create the sprint

```
POST /api/sprints/create
Content-Type: application/json

{"label": "sprint-N", "project": "owner/repo"}
```

Label format: `sprint-<number>` (e.g. `sprint-115`).

### Step 2 — Assign tickets to the sprint

```
POST /api/sprints/batch-labels
Content-Type: application/json

{
  "label": "sprint-N",
  "issue_numbers": [123, 456],
  "project": "owner/repo"
}
```

### Step 3 — Dispatch the sprint (API — preferred)

`POST /api/sprints/run` was **deleted** in the 2026-08 shrink. Use the restored
dispatch API (#2315 / #2353):

```
POST /api/sprints/{sprint_label}/dispatch
Content-Type: application/json

{"all": true, "repo": "owner/repo"}
```

Empty `tickets` / `"all": true` resolves open issues for that sprint label.
Explicit `"tickets": [N, …]` still works (order preserved).

Returns immediately with `run_id`. Poll:

```
GET /api/sprints/dispatch/{run_id}
```

Or stop at the next step boundary:

```
POST /api/sprints/dispatch/{run_id}/stop
```

### Step 3b — Overnight babysitter (optional, #2354)

For unattended retry-until-done (Claude Code overnight should only call HTTP):

```
POST /api/sprints/{sprint_label}/overnight
Content-Type: application/json

{"all": true, "repo": "owner/repo", "max_retries": 2}
```

Poll `GET /api/sprints/overnight/{overnight_id}`; stop with
`POST /api/sprints/overnight/{overnight_id}/stop`.

### Step 4 — Poll live / Running view

```
GET /api/running?project=owner/repo
GET /api/sprints/{sprint_label}/live?project=owner/repo
```

API dispatch progress comes from `.commander/runtime/dispatch-*.json` (#2355).
Tick-level clients may poll `GET /api/sprints/dispatch/{run_id}`. SSE
(`/live/stream`) also emits `dispatch` events when that JSON changes.

Alternatively stream:

```
GET /api/sprints/{sprint_label}/live/stream?project=owner/repo
```

### Step 5 — Complete after a green dispatch (#2357)

```
POST /api/sprints/{sprint_label}/complete-after-dispatch
Content-Type: application/json

{"project": "owner/repo", "preview": true}
```

Then without preview (merge PR; set `"uat_signoff": true` to also run Finish):

```
{"project": "owner/repo", "uat_signoff": true}
```

### Step 6 — History / outcome

```
GET /api/sprints/history?project=owner/repo
```

Returns an array of sprint objects ordered newest-first. Inspect `status` and
`summary` on the matching sprint entry.

---

## Recipe 3: Estimate an Issue

```
POST /api/issues/{issue_id}/estimate?repo=owner/repo
```

Key response fields:
- `size` — `"S"` | `"M"` | `"L"` | `"XL"`
- `confidence` — `"high"` | `"medium"` | `"low"`
- `estimated_hours` — float
- `files_likely_affected` — list of repo-relative paths
- `flags` — list of risk strings (e.g. `"touches-db-schema"`)

Results are cached in `.commander/estimates/issue-<N>.json`. Append
`?force=true` to re-estimate.

---

## Recipe 4: Query Pending Sign-Off and Read Docs

### Pending sign-off

Sprint tickets move to `uat` after Tester passes. Await human approval via the
dashboard. Query current state with:

```
GET /api/sprints/history?project=owner/repo
```

Filter the result for entries where `status == "uat"` — these await sign-off.

### List all docs for a project

```
GET /api/projects/{slug}/docs
```

`slug` is the lowercase repo name (e.g. `commander`). Returns:
```json
[{"path": "docs/architecture.md", "size": 4096, "mtime": 1720000000.0}, ...]
```

Allowed paths: everything under `docs/**`, plus `README.md` and `CHANGELOG.md`.

### Fetch a specific doc

```
GET /api/projects/{slug}/docs/{path}
```

Example paths: `docs/architecture.md`, `README.md`, `CHANGELOG.md`

Returns:
```json
{"path": "docs/architecture.md", "content": "# Architecture\n..."}
```

404 if the file does not exist; 400 if the path is not in the allowed set.

---

## Recipe 5: Error Conventions

| Code | Condition | Resolution |
|------|-----------|------------|
| 409  | `sprint-already-running` — a sprint is active for this project | Cancel or wait for the running sprint to finish |
| 422  | DAG cycle detected among ticket dependencies | Remove a dependency link to break the cycle |
| 422  | Mis-sizing flags — incompatible size/effort combination | Check the `flags` field in the response body |
| 422  | Missing required fields (label format, project) | `label` must match `sprint-\d+`; `project` must be `owner/repo` |
| 404  | Unknown project slug or sprint label | Verify the slug/label against `GET /api/sprints/history` |

### Sprint label format

Labels must match `sprint-\d+` (e.g. `sprint-115`). Numeric suffixes only;
no letters or spaces.

### 409 sprint-already-running

Only one sprint may run per project at a time. Check the running sprint with
`GET /api/sprints/history` and cancel it via `DELETE /api/sprints/run/{sprint_label}`
before starting a new one.
