# API contracts (Spec-Driven Development)

| File | Scope |
|------|--------|
| [`overnight.yaml`](overnight.yaml) | Dispatch, overnight, running/live, finish / UAT / complete-after-dispatch, agent-guide |

## Rules

1. **Runtime full schema:** `GET /openapi.json` (every router).
2. **Reviewed overnight subset:** `overnight.yaml` — what Claude Code / Hermes and the Run Sprint / Running paths must agree on.
3. **Deleted (not in YAML as live):** `POST /api/sprints/run`, scheduler routes.
4. Requirements map: [`docs/requirements/`](../requirements/).
5. Human recipes: [`docs/agent-guide.md`](../agent-guide.md).

## Validation (optional)

```bash
# If you have a YAML/OpenAPI linter locally:
npx --yes @redocly/cli lint docs/api/overnight.yaml
```

Do not treat lint failures as a merge blocker until CI is wired; keep the YAML
aligned with `apps/dashboard/routers/sprints.py`, `running.py`, `sprint_live.py`,
`sprint_finish.py`, and `signoff.py`.
