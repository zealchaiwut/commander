# Requirements index (Spec-Driven Development)

Stable **capability IDs** for Commander. This tree is an **index over existing
discussion records** (ADRs, milestones, features, agent-guide) — not a second
copy of those docs.

| Prefix | Domain | File |
|--------|--------|------|
| `REQ-SPRINT-*` | Sprint board & lifecycle | [sprint-lifecycle.md](sprint-lifecycle.md) |
| `REQ-DISPATCH-*` | API dispatch queue | [dispatch-overnight.md](dispatch-overnight.md) |
| `REQ-OVERNIGHT-*` | Unattended babysitter / Claude Code HTTP | [dispatch-overnight.md](dispatch-overnight.md) |
| `REQ-RUNNING-*` | Running / live visibility | [running-live.md](running-live.md) |
| `REQ-FINISH-*` | Finish, UAT sign-off, complete-after-dispatch | [finish-signoff.md](finish-signoff.md) |
| `REQ-MULTI-*` | Multi-project & `project=` | [multi-project.md](multi-project.md) |
| `REQ-SEC-*` | Auth / secrets | [security-auth.md](security-auth.md) |
| `REQ-DOR-*` | Ticket DoR / Design Refs | [definition-of-ready.md](definition-of-ready.md) |

## How BA / SDD tickets use this

1. Pick capability ID(s) in **What & Why** or Design Refs.
2. Follow the **Source** links for deliberated decisions (ADR / milestone).
3. Acceptance criteria must be behavioral; map UI work to [`DESIGN.md`](../../DESIGN.md) headings.
4. HTTP contracts for overnight: [`docs/api/overnight.yaml`](../api/overnight.yaml) + [`docs/agent-guide.md`](../agent-guide.md).

## Related roots

- Product strategy: [`PRODUCT.md`](../../PRODUCT.md)
- Design / Design Refs: [`DESIGN.md`](../../DESIGN.md)
- Technical design chapters: [`docs/architecture/`](../architecture/)
- ADRs: [`docs/decisions/`](../decisions/)
- Initiatives: [`docs/milestones/`](../milestones/)
