# REQ-RUNNING — Running & live visibility

## Capabilities

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-RUNNING-01 | `GET /api/running?project=` surfaces active API dispatch runs (`queued`/`running` in `dispatch-*.json`) without requiring a PID file | #2355 |
| REQ-RUNNING-02 | Done/failed/stopped dispatch runs do not appear as active Running rows | #2355 |
| REQ-RUNNING-03 | `GET …/live` includes dispatch `run_id`, `current_issue`, `current_step`, recent outcomes when active | #2355 |
| REQ-RUNNING-04 | Live SSE may emit `dispatch` events; tick-level clients may poll `GET …/dispatch/{run_id}` | #2355, DESIGN.md Running view |
| REQ-RUNNING-05 | Running hot path makes zero live GitHub calls (mirror / local files / SQLite only) | #2355 / #1645 |
| REQ-RUNNING-06 | Global hooks provision so agent_runs work from managed-project worktrees | #2342 |

## Design Refs

- DESIGN.md → **Running view**, **Live snapshot and SSE**
