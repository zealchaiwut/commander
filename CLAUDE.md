# Commander Platform — Agent Instructions

You are working on Commander, a personal AI agent platform for solo 
development with Claude Code. This file contains project-wide instructions 
that apply to all agents (BA, Coder, Tester, and any direct Claude Code 
sessions).

## Confirmation Policy — STRICT (All Agents)

Agents may ONLY pause for confirmation for: (1) ambiguous requirements where a wrong guess wastes significant work; (2) destructive actions without clear precedent (`git push --force`, `git reset --hard`, touching `master`, deleting issues); (3) role-specific gate — BA shows the ticket body once before creating it on GitHub, Coder never pauses, Tester never pauses. Everything else — branch creation, commits, pushes, label updates, running tests, calling workflow scripts — executes immediately with a one-line status update. Default is **execute**, not ask.

## Project Overview

Commander is:
- A FastAPI web dashboard that tracks Claude Code agents in real time
- - A GitHub Issues-based sprint board (BA → Coder → Tester → UAT flow)
- - Mobile-accessible via Tailscale
- - The repo is github.com/zealchaiwut/commander
-
- ## Tech Stack
-
- - Python 3.12, FastAPI, Uvicorn
- - SQLite for agent event history
- - Plain HTML + vanilla JS (no React, no build step)
- - Server-Sent Events for live updates
- - GitHub CLI (`gh`) for issue management
- - Pytest + httpx for tests
-
- ## Branching Workflow
-
- This project uses a three-tier branching model:
-
- - `master` — production, signed-off code only. Only I (the human) merge here.
- - `develop` — integration branch. SIT-passed code lives here. Tester merges 
-   feature branches into this after tests pass.
-   - `feature/<issue-N>-<slug>` — short-lived branches for individual features.
-     Coders create these off `develop`. Naming: kebab-case, includes issue number.
-
-     DO NOT commit directly to `master`. DO NOT merge feature branches to master.
-
-     ## Roles
-
-     - **BA** writes acceptance criteria + UAT steps, creates GitHub issues using 
-       `scripts/create_ticket.py`. Uses `.github/ISSUE_TEMPLATE/feature.md`.
-       Pass `--attachment <path>` (repeatable) to attach supporting files; each file
-       is copied to `references/issue-<N>/`, committed, and linked in the issue body.
-
-       - **Coder** creates a feature branch off develop, implements the feature, 
-         pushes the branch, updates label to SIT. Does NOT merge.
-
-         - **Tester** checks out feature branch, writes pytest tests for each 
-           acceptance criterion, runs them, posts structured test report, merges 
-             to develop if tests pass and moves label to UAT.
-
-             - **The human** signs off on UAT from the dashboard, then merges develop 
-               to master manually.
-
-               ## Code Conventions
-
-               - Test files in `tests/` named `test_<feature>__<criterion>.py`
-               - Helper scripts in `scripts/` are pure Python, args via argparse
-               - Hook scripts in `hooks/` POST to localhost:8000, fail silently if server down
-               - No new Python dependencies without adding to requirements.txt
-               - No new frontend frameworks — keep static HTML + JS minimal
-
-               ## MCP Servers (available in all sessions)
-
-               Three MCP servers are installed at user scope — prefer them over shell fallbacks:
-
-               | Server | Tools prefix | Use for |
-               |--------|-------------|---------|
-               | **codedb** | `mcp__codedb__*` | Code navigation, symbol search, file reads (faster than Bash+Read) |
-               | **github** | `mcp__github__*` | List/view/create/edit issues, create PRs, check workflow runs. Prefer over shelling out to `gh`. |
-               | **sqlite** | `mcp__sqlite__*` | Query `dashboard.db` (tables: `agents`, `events`, `token_usage`). Use for debugging DB state instead of `sqlite3` via Bash. |
-
-               **Tool preference rules:**
-               - To read/search code → `mcp__codedb__*` over `Read`/`Bash grep`
-               - To work with GitHub issues/PRs → `mcp__github__*` over `gh` CLI in Bash (existing scripts like `create_ticket.py` / `update_ticket.py` may still use `gh` internally — do not refactor them)
-               - To inspect live DB state → `mcp__sqlite__*` over `sqlite3` in Bash
-
-               ## When Working on This Project
-
-               - Always run agents with CLAUDE_AGENT_ROLE env var set (ba, coder, tester)
-               - The dashboard runs at localhost:8000 — assume it's already running
-               - Use codedb MCP tools for code navigation when available (faster than Read)
-               - Read the issue body carefully before implementing — acceptance criteria 
-                 is the contract
-
-                 ## Standard Project Layout
-
-                 Two layouts are supported. Use `--nested` with `init_project.py` for new projects.
-
-                 **Nested layout** (`--nested`, recommended for new projects):
-                 ```
-                 ~/dev/<project>/
-                   main/              # primary working clone (master branch)
-                   coder/             # coder agent clone (develop branch)
-                   tester/            # tester agent clone (develop branch)
-                   uat/               # UAT clone (develop branch) — optional
-                   .commander/        # sprint config at project root, outside any clone
-                     sprint.yaml
-                     logs/
-                     sprints/
-                     alerts/
-                 ```
-
-                 **Flat layout** (default, backward compatible):
-                 ```
-                 ~/dev/<project>/          # main clone — master branch
-                 ~/dev/<project>/uat/      # UAT clone — develop branch
-                 ~/dev/<project>-coder/    # coder agent clone — develop branch
-                 ~/dev/<project>-tester/   # tester agent clone — develop branch
-                 ~/dev/<project>/.commander/sprint.yaml   # inside main clone
-                 ```
-
-                 The sprint manager auto-discovers `.commander/sprint.yaml` by walking UP
-                 from the current working directory, so it works from inside any clone in
-                 both layouts.
-
-                 To migrate an existing flat project to nested:
-                 `scripts/migrate_project_layout.py <project-name>`
-
-                 ## Useful Scripts
-
-                 - `scripts/create_ticket.py` — file a new issue with template
-                 - `scripts/update_ticket.py` — change labels (in-progress, sit, uat, blocked)
-                 - `scripts/comment_ticket.py` — add comment to issue
-                 - `scripts/post_test_report.py` — tester uses this for structured reports
-                 - `scripts/start_feature.py` — coder uses this to create feature branch
-                 - `scripts/finish_feature.py` — tester uses this to merge to develop
-                 - `scripts/init_project.py` — onboard a new project (`--nested` for nested layout)
-                 - `scripts/migrate_project_layout.py` — migrate flat project to nested layout
-                 - `scripts/migrate_add_uat.py` — add UAT clone to an existing project
-
-                 ## Issue Estimator

The Issue Estimator agent reads a ticket after it is created and produces structured sizing metadata: size estimate, confidence, files likely affected, dependency graph, and risk flags.

**When to run it:**
- After BA creates a ticket and you want sizing data before sprint planning
- From the CLI: `python3 services/sprint_manager/estimate_issue.py --issue <N> [--repo owner/repo] [--save-comment] [--save-label] [--force]`
- As a slash command: `/estimate <issue-url>`

**Output saved to:** `<project>/.commander/estimates/issue-<N>.json`

**Size scale:** S=<1hr, M=1–3hr, L=3–8hr, XL=>8hr

**Caching:** estimates are cached — re-running without `--force` returns the cached result instantly.

**Sprint manager integration:** when `sprint_manager.py` dispatches tickets, it reads cached estimates (if present) and:
- Logs size, estimated hours, and confidence for each ticket
- Warns on serious risk flags (`touches-db-schema`, `security-sensitive`, `breaks-tests`)
- Warns when two pending tickets share files in `files_likely_affected`

**Model:** Haiku 4.5 (cheaper; the task is structured and well-defined — no Sonnet needed).

**Agent definition:** `apps/dashboard/.claude/agents/estimator.md`

## Out of Scope
-
-                 - DO NOT add Discord, Slack, or other notification systems (separate sprint)
-                 - DO NOT add auth (single-user, local only for now)
-                 - DO NOT add caching layers beyond the existing 30s GitHub cache
-
-                 ## When in Doubt
-
-                 Default is **execute**, not ask. Only stop for genuine ambiguity or destructive actions. See "Confirmation Policy — STRICT" at the top of this file.

## API Cost and Model Selection

There are two pricing surfaces. Always prefer the cheaper option.

### Pricing surfaces

| Surface | Funded by | When used |
|---|---|---|
| **Claude Code CLI** (`claude` subprocess) | Claude.ai subscription (free up to limits) | Coder, tester, preflight, and sprint_estimator agents dispatched by sprint_manager.py |

### Default models per agent

| Agent | Default model | Rationale |
|---|---|---|
| BA | `claude-sonnet-4-6` | Ticket writing benefits from quality reasoning; Opus is overkill |
| Coder | `claude-sonnet-4-6` (via Claude Code) | Solid coding quality; subscription-funded |
| Tester | `claude-haiku-4-5` (via Claude Code) | Mostly mechanical verification; cheapest capable model |
| Estimator (per-issue) | `claude-haiku-4-5-20251001` (via Claude Code) | Structured JSON output; no Sonnet needed |
| Sprint estimator | `claude-sonnet-4-6` (via Claude Code) | Scans codebase for all backlog tickets in one pass; subscription-funded |
| sprint_review.py | `claude-haiku-4-5` (via Claude Code) | Single agent call for all issues; subscription-funded |

### How to choose a model

- Use Sonnet 4.6 as the default for BA and Coder work.
- Use Haiku 4.5 for mechanical/verification work (Tester, preflight).
- Use Opus only when a task genuinely requires it (complex architecture decisions, ambiguous multi-constraint problems). Override per-invocation, not by changing the default.
- Never use Opus as the default for any agent role — the cost is disproportionate.

### Cost visibility

- Token usage is tracked in the `token_usage` table with `agent_role` and `model_name` columns.
- Sprint summaries include a `cost_estimate` row in the Stats table (shows $0.00 — all agents are subscription-funded).
- Audit per-agent/model spend: `GET /api/debug/token-usage/by-agent-model`

## String literal conventions

For any displayed text in markdown reports, error messages, or user-facing 
output:

- Section headings: Title Case (## Sprint Review, ## What Shipped)
- Table column headers: Sentence case (| Total tokens |, | Avg ticket time |)
- Inline labels: Sentence case (timeout, gate failed)

Be consistent. If you see "Total Tokens" somewhere and "Total tokens" 
elsewhere, that's a bug — flag it.

## Don't copy Python venvs

A Python venv hardcodes absolute paths in its scripts and shim binaries.
Copying `venv/` from one location (or machine) to another will produce
`ModuleNotFoundError: No module named 'encodings'` and similar errors
when Python can't find its standard library at the original path.

**Always recreate venvs fresh:**

```bash
# In each clone (prd, uat, coder, tester):
rm -rf venv
~/.local/bin/python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Or use the `.commander/setup.sh` helper if it includes venv setup.

## How to read issue attachments

When an issue body contains an `## Attachments` section, files are stored on the
dedicated long-lived `attachments` branch (never merged into develop or master).

**List files for an issue:**

```bash
gh api repos/zealchaiwut/commander/git/trees/attachments --recursive \
  | jq -r '.tree[] | select(.path | startswith("references/issue-<N>/")) | .path'
```

**Download a specific file:**

```bash
# Via gh api (outputs raw bytes):
gh api repos/zealchaiwut/commander/contents/references/issue-<N>/<filename> \
  --header "Accept: application/vnd.github.raw" \
  --jq '.' --output <local-filename>

# Or via git show from the bare-clone cache (fastest if cache exists):
git -C apps/dashboard/runtime/attachments-cache/zealchaiwut-commander \
  show attachments:references/issue-<N>/<filename> > <local-filename>
```

**Direct raw URL (for supported file types that render on GitHub):**

```
https://raw.githubusercontent.com/zealchaiwut/commander/attachments/references/issue-<N>/<filename>
```

If the issue body has an `## Attachments` section, download the relevant files
before starting implementation. The links in the issue body already point to the
raw URL above.