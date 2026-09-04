# REQ-DOR — Definition of Ready & Design Refs

## Capabilities

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-DOR-01 | Tickets include Acceptance Criteria; Design Refs when DESIGN applies; UAT/test plan; size estimate | [bulk-create DoR brief](../bulk-create/2026-06-21-2-planning-definition-of-ready.md), ticket_spec.py |
| REQ-DOR-02 | Design Refs cite only headings that exist in root `DESIGN.md` | ba.md, DESIGN.md |
| REQ-DOR-03 | DoR mode `block` / `warn` / `off` gates Run Sprint via settings / `COMMANDER_DOR_MODE` | #1487 |
| REQ-DOR-04 | AC tests must exercise behavior, not source-regex | #1746, CLAUDE.md |
| REQ-DOR-05 | Frontend UI ACs reference concrete DESIGN / mock contracts (#713 paths) | ba.md |

## Related

- Product: [`PRODUCT.md`](../../PRODUCT.md)
- Design: [`DESIGN.md`](../../DESIGN.md)
