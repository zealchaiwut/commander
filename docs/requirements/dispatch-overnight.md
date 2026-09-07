# REQ-DISPATCH / REQ-OVERNIGHT — Dispatch & overnight HTTP

## Capabilities — dispatch

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-DISPATCH-01 | `POST /api/sprints/{label}/dispatch` runs coder→tester per ticket in given order; stops on first failure | #2314/#2315, shrink milestone |
| REQ-DISPATCH-02 | Empty `tickets` or `"all": true` resolves open issues for that exact sprint label (child labels excluded) | #2353 |
| REQ-DISPATCH-03 | Optional `order: "dag"` reorders via existing DAG preview; default ascending issue number | #2353 |
| REQ-DISPATCH-04 | Zero open tickets → 400; no silent empty run | #2353 |
| REQ-DISPATCH-05 | Wrap-up runs reviewer then documentor before opening sprint→develop PR (skippable via env) | #2343 |
| REQ-DISPATCH-06 | Sprint branch `sprint/sprint-N` is ensured from develop when dispatch starts | #2329 |
| REQ-DISPATCH-07 | Privileged `claude -p` spawn happens inside Commander; assistants call HTTP only | PRODUCT.md, CLAUDE.md |

## Capabilities — overnight

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-OVERNIGHT-01 | `POST …/overnight` resolves tickets, dispatches, on failure resets failed ticket and re-dispatches remaining up to `max_retries` (default 2) | #2354 |
| REQ-OVERNIGHT-02 | Status via `GET …/overnight/{id}` with phase `dispatching`/`retrying`/`done`/`exhausted`/`stopped` | #2354 |
| REQ-OVERNIGHT-03 | Stop at next dispatch/retry boundary via `POST …/overnight/{id}/stop` | #2354 |
| REQ-OVERNIGHT-04 | Does not mint child sprint labels or write lifecycle beyond dispatch/rerun | #2354 / #2311 |
| REQ-OVERNIGHT-05 | Claude Code overnight recipe is HTTP-only (dispatch → overnight → complete-after-dispatch) | [agent-guide](../agent-guide.md), #2358 |

## API contract

OpenAPI (SDD): [`docs/api/overnight.yaml`](../api/overnight.yaml).

## Source milestones / ADRs

- [commander-shrink-2026-08.md](../milestones/commander-shrink-2026-08.md) — deleted orchestrator; restore dispatch as endpoints
- Deleted `POST /api/sprints/run` must not appear as a live requirement
