# 14. Roadmap & sequencing

*Meta — updated as sections are drafted.*

[← Contents](0_content.md) · [← Prev: Observability & cost](13_observability-and-cost.md)

## 14.1 What's drafted/filed already

| Area | Issues / docs |
|------|---------------|
| Neon migration | #244–248; `sprint_repo.py` (#246) as repo-layer template |
| Backend router split | #761; [boundaries.md](boundaries.md) (#793) |
| Structured logging Phase 1 | `services/logging.py` |
| Frontend build pipeline | #796 (esbuild, ES modules, no framework yet) |
| Coder fix-loop + rework | #618 (bounded retry), #787 (hang redispatch), #788 (worktree hygiene), #789 (model routing) |
| Sprint lifecycle redesign | [sprint-lifecycle.md](sprint-lifecycle.md), `docs/milestones/sprint-lifecycle-redesign.md` |
| UI parity, bulk-create-tab | Filed — **blocked until refactor lands** ([2.5](2_app-dashboard-architecture.md#25-sequencing--refactor-before-any-more-features)) |

## 14.2 Refactor vs feature ordering

**Decision:** finish in-flight UI sprint, then **no new feature work** until refactor lands.

Refactor sprint sequence ([2.5](2_app-dashboard-architecture.md#25-sequencing--refactor-before-any-more-features)):

1. Characterization tests
2. Backend router/service/repo split (continue #761)
3. Structured logging Phase 1 completion (collapse four surfaces)
4. Frontend esbuild + modules + partials split (2.3a, 2.3b)
5. Legacy route deletion at parity (2.4)

**Open:** one sprint vs split (frontend/backend).

## 14.3 Dependency map

```
1 State model ──► 3 Sprint flow ──► 4 Agents ──► 5 Concurrency
       │                │                │
       └────────────────┴────────────────┴──► 6 Failure/recovery
       │
       └──► 8 Database/Neon ──► 13 Observability Phase 2

2 App architecture (refactor) ──blocks──► UI parity, bulk-create-tab, Neon features
2.2 Backend split ──► 13.3 log consolidation
2.3 Frontend split ──► 2.3b live-log stream decision
4.6 Coder reliability ──► 6.1 ticket retry, 7.3 worktrees
```

## 14.4 What to run next

**Resume here (pending from section 2 review):**

- [x] **2.3a** — frontend module boundaries ([2.3a-frontend-module-boundaries.md](2.3a-frontend-module-boundaries.md))
- [ ] **2.3b** — complete sitemap + settle file-tail vs live-stream
- [ ] **4c–4f** — nudge-before-kill, worktree debt, doctor preflight, AGENTS.md targeting
- [ ] Refactor sprint scoping (one vs split)
- [ ] Characterization-test coverage depth
