# Commander Architecture — Table of Contents

This is the index for the architecture documentation set. Sections are ordered by dependency: state model first (the foundation), then the user's priorities (app, sprint flow, agents), then cross-cutting concerns, ending with the roadmap.

| # | Section | Notes |
|---|---------|-------|
| 1 | [State & source-of-truth model](1_state-and-source-of-truth.md) | The foundation — read first |
| 2 | [App / Dashboard architecture](2_app-dashboard-architecture.md) | Priority · **decision record partial** (2.3a/2.3b pending) |
| 3 | [Sprint flow (work flow)](3_sprint-flow.md) | Priority — includes sprint & ticket lifecycle |
| 4 | [Agents](4_agents.md) | Priority · **decision record partial** (4a/4b landed; 4d/4e partial; 4c/4f open) |
| 5 | [Concurrency & locking](5_concurrency-and-locking.md) | Tightly tied to 3 & 4 |
| 6 | [Failure & recovery semantics](6_failure-and-recovery.md) | The unhappy-path side of the lifecycle |
| 7 | [Git / branch strategy](7_git-branch-strategy.md) | |
| 8 | [Database, connection & local env/files](8_database-and-local-env.md) | Includes Neon migration plan |
| 9 | [Multiple projects — sync & creation](9_multiple-projects.md) | |
| 10 | [DevOps process](10_devops.md) | Commander itself + controlled projects |
| 11 | [Remote work](11_remote-work.md) | |
| 12 | [Security & secrets](12_security-and-secrets.md) | |
| 13 | [Observability & cost](13_observability-and-cost.md) | |
| 14 | [Roadmap & sequencing](14_roadmap-and-sequencing.md) | Meta — do last |

## Where does "lifecycle" live?

The sprint/ticket lifecycle is split deliberately:

- **Section 3 (Sprint flow)** owns the lifecycle definitions — states, transitions, the happy path (3.2 sprint lifecycle, 3.3 ticket lifecycle).
- **Section 6 (Failure & recovery)** owns the unhappy paths — needs-rework, cancel, process death, resume.
- **Section 1 (State model)** owns where lifecycle state is *stored* and who is authoritative for it.

The lifecycle redesign **landed** (P0–P4, shipped through sprints 73.x — see the now-closed tracker `docs/milestones/sprint-lifecycle-redesign.md`). [sprint-lifecycle.md](sprint-lifecycle.md) documents the shipped behavior and is the source of truth. Sections 1, 3, and 6 still carry `_TODO_` stubs to be folded against that doc — a separate prose pass.

## Related existing docs

- [Sprint lifecycle redesign](sprint-lifecycle.md) — agreed design for source-of-truth, states, merge model
- [Boundaries](boundaries.md) — module/layer boundaries
- [Frontend map](frontend-map.md) — frontend file inventory
- [architecture.md](../architecture.md) — legacy top-level architecture overview (to be folded into this set)
