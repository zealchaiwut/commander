# REQ-SEC — Security & auth

## Capabilities

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-SEC-01 | Single-user; no accounts, sessions, OAuth, or per-role permissions | PRODUCT.md, [architecture §12](../architecture/12_security-and-secrets.md) |
| REQ-SEC-02 | Optional `COMMANDER_API_TOKEN`: writes require `Authorization: Bearer`; GET/SSE open; 127.0.0.1 exempt | #1864 |
| REQ-SEC-03 | Token never rendered into served HTML | #1895 |
| REQ-SEC-04 | Browser stores token in `localStorage` via `commanderSetApiToken`; Hermes sends Bearer directly | #1864/#1895 |
| REQ-SEC-05 | No Discord/Slack notification stack in-core (separate initiative) | PRODUCT.md non-goals / Out of Scope in CLAUDE.md |

## Design Refs

- DESIGN.md → **Settings and API token**
