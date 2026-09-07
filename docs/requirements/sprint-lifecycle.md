# REQ-SPRINT — Sprint board & lifecycle

## Capabilities

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-SPRINT-01 | Sprint identity is `(label, project)`; no cross-project collisions | [architecture §1](../architecture/1_state-and-source-of-truth.md), [#1465](../architecture/), DESIGN.md Invariants |
| REQ-SPRINT-02 | Issues are tracked via GitHub labels (`sprint-N`, backlog/SIT/UAT/…) and the dashboard Board | [PRODUCT.md](../../PRODUCT.md), [sprint-lifecycle.md](../architecture/sprint-lifecycle.md) |
| REQ-SPRINT-03 | Board exposes Board / Running / History; Run Sprint starts API dispatch (not CLI-only) | [#2356](../agent-guide.md), DESIGN.md Sprint board |
| REQ-SPRINT-04 | Child sprint labels `sprint-N.1` are banned for new work; sprint *branches* are allowed | Shrink / #2311, CLAUDE.md |
| REQ-SPRINT-05 | Lifecycle states and merge model follow shipped lifecycle redesign | [sprint-lifecycle.md](../architecture/sprint-lifecycle.md), [milestone](../milestones/sprint-lifecycle-redesign.md) |
| REQ-SPRINT-06 | Deprecated `planned` / plan.json signoff gate stay disabled unless re-enabled by ADR | [ADR 2026-07-02-1](../decisions/2026-07-02-1-delete-planned-state-and-signoff.md) |

## Non-requirements

- Auto-merge `develop` → `master` (human only).
