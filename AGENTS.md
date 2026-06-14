<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.

## Cursor Cloud specific instructions

Dependencies (Python venv at `venv/`, `node_modules/`) are refreshed automatically by the startup update script; you do not need to reinstall them. System package `python3.12-venv` is required for venv creation and is already present in the VM image.

### Running the dashboard (the one must-run service)

- The dashboard needs `apps/dashboard/.env` (gitignored, so not in the repo). It must define `DB_PATH` — the server **exits immediately** if `DB_PATH` is unset/blank. A working local `.env` is: `DB_PATH=./commander.db`, `ENVIRONMENT=prd`, `PORT=8000`, `COMMANDER_DISABLE_NEON=1`. Set `COMMANDER_DISABLE_NEON=1` so the app runs purely off SQLite + local JSON (Neon/Postgres is an optional export target with an unmigrated schema; without this flag sprint creation can 500).
- Start it with `bash scripts/start_prd.sh` (backgrounds uvicorn on port 8000, writes `apps/dashboard/prd.pid` and `apps/dashboard/prd.log`). It auto-syncs pip and ensures `ENVIRONMENT=prd`. Stop with `bash scripts/stop_all.sh`.
- Health check: `curl http://localhost:8000/api/health`. It reports `degraded` (not `healthy`) when the GitHub API is unreachable — see below.
- The `claude CLI not found` warning at startup is expected here; it only blocks the agent-dispatch workflow, not the dashboard itself.

### GitHub API is the main external limitation

The cloud agent's `gh`/GitHub token does not have `repo` scope for `zealchaiwut/commander`, so GitHub-backed features return 403: the sprint board shows "Failed to load sprints", a yellow "missing repo scope" banner appears, and `/api/health` is `degraded`. This is expected without a scoped token and does not indicate a broken setup. Core local features (agent/event tracking via SQLite, the Logs/Activity feed, SSE) work without GitHub.

### Lint / test / build

- Python tests: run with `DB_PATH` set as the safe default — `DB_PATH=./apps/dashboard/commander.db ./venv/bin/python -m pytest tests/<file> -q`. Some suites (e.g. DB/endpoint tests like `test_631`) need it; others set up their own temp DB. Exclude browser tests with `-m "not selenium"` (selenium/webdriver-manager tests need a browser + network). Note: a few tests on `master` are pre-existing failures (stale references / live-DB assumptions), and at least one test hangs, so do not expect a fully green full-suite run — scope to the files relevant to your change.
- Python lint: `./venv/bin/ruff check apps/dashboard` (the repo currently has pre-existing ruff findings; treat new findings only as actionable).
- Frontend build: `npm run build` (esbuild → `apps/dashboard/static/dist/bundle.js`). Frontend lint: `npm run lint` (eslint; currently warnings only). Frontend tests: `npm test`.
- Frontend ES-module edits under `static/src/` require `npm run build` (or `npm run watch`) to take effect; plain `static/*.html` edits apply on page refresh; Python changes need a uvicorn restart.

### code-review-graph (MCP tools the rules require)

The `.cursorrules`/`AGENTS.md` mandate the `code-review-graph` MCP tools, registered in `.cursor/mcp.json` as `./venv/bin/code-review-graph serve`. The graph DB lives at `.code-review-graph/graph.db` (gitignored, so not in the repo). Build/refresh it with `./venv/bin/code-review-graph build` (a fast local parse — no network) before relying on `query_graph`/`detect_changes`/`get_impact_radius`; `semantic_search_nodes` works off the FTS index after a build (vector embeddings via `crg embed` need an API key and are optional). The `.claude/settings.json` auto-update hook targets a hardcoded macOS path and no-ops on this Linux VM, so refresh manually with `code-review-graph update` after large edits.
