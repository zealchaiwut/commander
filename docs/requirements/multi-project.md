# REQ-MULTI — Multi-project

## Capabilities

| ID | Requirement | Source |
|----|-------------|--------|
| REQ-MULTI-01 | Canonical `project=` / `repo` is `owner/repo`; bare slug accepted via central resolver | #2064, [architecture §9](../architecture/9_multiple-projects.md) |
| REQ-MULTI-02 | Unrecognised project → 404; never fall back to Commander’s own data | #2064 |
| REQ-MULTI-03 | Sprint running checks and locks are scoped per project | CLAUDE session notes / #cross-project lock fix |
| REQ-MULTI-04 | Nested and flat project layouts both discover `.commander/sprint.yaml` | CLAUDE.md Standard Project Layout |

## Design Refs

- DESIGN.md → **Composite sprint key**, **Project query parameter**
