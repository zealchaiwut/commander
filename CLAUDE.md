# Commander Platform — Agent Instructions

You are working on Commander, a personal AI agent platform for solo 
development with Claude Code. This file contains project-wide instructions 
that apply to all agents (BA, Coder, Tester, and any direct Claude Code 
sessions).

## Confirmation Policy — STRICT (All Agents)

Agents may ONLY pause for confirmation for: (1) ambiguous requirements where a wrong guess wastes significant work; (2) destructive actions without clear precedent (`git push --force`, `git reset --hard`, touching `master`, deleting issues); (3) role-specific gate — BA shows the ticket body once before creating it on GitHub, Coder never pauses, Tester never pauses. Everything else — branch creation, commits, pushes, label updates, running tests, calling workflow scripts — executes immediately with a one-line status update. Default is **execute**, not ask.

## Bug Reports — Investigate and Plan First (Direct Sessions)

When the operator reports a bug or asks why something is broken, ALWAYS investigate the root cause and present a plan for discussion BEFORE making any fix. Read the relevant code, logs, and state; explain what is actually happening and why; lay out the fix options with a recommendation; then wait for the operator to choose. Do not jump straight to editing. This applies to direct Claude Code sessions with the operator — it does not change the autonomous Coder/Tester agents, which never pause. Once the operator approves the plan, the default-execute policy above governs the fix.

## Project Overview

Commander is:
- A FastAPI web dashboard that tracks Claude Code agents in real time
- A GitHub Issues-based sprint board (BA → Coder → Tester → UAT flow)
- Mobile-accessible via Tailscale — launchd is the authoritative unattended runner (tmux is attended local dev only); device must be enrolled (`tailscale up`) and logged in; macOS Application Firewall blocks direct incoming connections for Python/uvicorn on port 8000; Tailscale operates below the Application Firewall layer so Tailscale-routed traffic reaches the service regardless of that block
- The repo is github.com/zealchaiwut/commander

## Tech Stack

- Python 3.12, FastAPI, Uvicorn
- SQLite (`DB_PATH`, e.g. `dashboard.db`) for agent event history — tables `agents`, `events`, `token_usage`
- Optional Neon/Postgres layer (`DATABASE_URL`, via `services/sprint_manager/sprint_repo.py`) that mirrors sprint metadata. It is **secondary** — the dashboard runs fully without it. Disable per-machine with `COMMANDER_DISABLE_NEON=1` (see below).
- Plain HTML + vanilla JS with an **esbuild bundling step** (ES modules → `static/dist/bundle.js`; no React/Vue/Svelte framework yet — see `docs/architecture/2_app-dashboard-architecture.md` §2.3)
- Server-Sent Events for live updates
- GitHub CLI (`gh`) for issue management
- Pytest + httpx for tests

## Dashboard deploy model — frontend vs backend

The dashboard has two deploy layers with different refresh rules:

- **Frontend markup** (`apps/dashboard/static/*.html`, inline JS/CSS not yet extracted) is served from disk per request. Edits take effect on the **next page refresh** — no server restart needed.
- **Frontend bundle** (`apps/dashboard/static/src/` → `npm run build` → `static/dist/bundle.js`) requires running **`npm run build`** (or `npm run watch`) after editing ES modules under `static/src/`. Commit the rebuilt `bundle.js` or run build in each clone (prd, coder, tester, uat).
- **Backend** (`apps/dashboard/server.py`, `services/sprint_manager/*.py`, any Python) requires a **uvicorn restart / redeploy** to take effect.

When sequencing work for a running instance you can't restart (e.g. a remote launchd dashboard), prefer HTML-only frontend tickets — they go live without a redeploy. Bundle changes need a build step in that clone.

## Neon/Postgres kill switch

The Neon layer is optional and currently **disabled on the local authoring machine** (its schema is unmigrated, so writes error). Setting `COMMANDER_DISABLE_NEON=1` (in `apps/dashboard/.env`, read at import time) makes the dashboard run purely off GitHub + SQLite + local JSON: no Neon reads/writes and no startup `projects` sync. Symptoms when it's *not* disabled but the schema is missing: a 500 on sprint create (`Neon write failed`) and a startup warning `relation "projects" does not exist`. Re-enable (and migrate the schema) when that work is picked up.

## Sprint reconcile & GitHub quota

GitHub is the source of truth for sprint state; the DB is a replica that can drift (e.g. testing the same sprint on UAT and PRD with separate DBs). Reconciliation re-checks GitHub and corrects the DB lifecycle + local `state.json` — it **never** modifies GitHub.

- **Auto-reconcile** runs as a background sweep on every History-tab load (throttled per project). Its reads are **mirror-backed** (the local `issues` table, refreshed every 60s via zero-quota 304 ETag polls) plus one cached `gh pr list` per repo for merge state — so a History load no longer fans out `gh issue list`/`gh pr view` per sprint. Set `COMMANDER_DISABLE_AUTO_RECONCILE=1` (in `apps/dashboard/.env`) on a non-primary clone (e.g. UAT) so only one dashboard self-heals against the shared token.
- **Per-sprint reconcile button** (Board + History cards → "Reconcile"): `GET /api/sprints/{label}/reconcile-preview?project=` is a dry-run (GitHub-vs-DB diff + post-sprint checks, mirror-sourced, no writes); `POST /api/sprints/{label}/reconcile` applies it (DB + local only). Use it to clear a zombie sprint without a full sweep.
- **Backfill scripts make zero GitHub calls.** `scripts/backfill_agent_runs_project.py` and `scripts/backfill_sprint_project.py` read only the local `issues` mirror + disk JSON — they never consume GH quota. The rate-limit risk is from interactive `gh` CLI (PR/issue GraphQL) and running multiple dashboards on one token, not these scripts.

## Branching Workflow

This project uses a three-tier branching model:

- `master` — production, signed-off code only. Only I (the human) merge here.
- `develop` — integration branch. SIT-passed code lives here. Tester merges 
  feature branches into this after tests pass.
  - `feature/<issue-N>-<slug>` — short-lived branches for individual features.
    Coders create these off `develop`. Naming: kebab-case, includes issue number.

DO NOT commit directly to `master`. DO NOT merge feature branches to master.

## Roles

- **BA** writes acceptance criteria + UAT steps, creates GitHub issues using 
  `scripts/create_ticket.py`. Uses `.github/ISSUE_TEMPLATE/feature.md`.
  Pass `--attachment <path>` (repeatable) to attach supporting files; each file
  is copied to `references/issue-<N>/`, committed, and linked in the issue body.
- **Coder** creates a feature branch off develop, implements the feature, 
  pushes the branch, updates label to SIT. Does NOT merge.
- **Tester** checks out feature branch, writes pytest tests for each 
  acceptance criterion, runs them, posts structured test report, merges 
  to develop if tests pass and moves label to UAT.
- **The human** signs off on UAT from the dashboard, then merges develop 
  to master manually.

**UAT is the "done" state for progress/completion UI.** There is no separately
tracked "done" stage before the human sign-off, so any "X/Y done" count or
completion percentage should use `done + uat` as the numerator (surface UAT
separately as the awaiting-sign-off count). Applied in the sprint nav pill.

## Code Conventions

- Test files in `tests/` named `test_<feature>__<criterion>.py`
- Helper scripts in `scripts/` are pure Python, args via argparse
- Hook scripts in `hooks/` POST to localhost:8000, fail silently if server down
- No new Python dependencies without adding to requirements.txt
- Frontend: ES modules under `apps/dashboard/static/src/`, bundled via esbuild (`npm run build`). No React/Vue/Svelte — keep vanilla JS. Node/npm is a required dev dependency in every clone.

### Keep lint/export refactors in their own ticket (issue #1588)

Lint-only or testability-only refactors — adding/removing `export` keywords,
editing eslint `/* global */` comments, renaming for clarity, reordering imports
— must be filed as a **separate ticket** from feature work, never bundled into a
feature ticket's diff. The per-ticket diff must match that ticket's acceptance
criteria so reviewers can verify scope at a glance. If you notice a worthwhile
lint/export cleanup while implementing a feature, open a follow-up ticket for it
rather than widening the current diff (this is exactly what #1154 → #1588 did).

## MCP Servers (available in all sessions)

Three MCP servers are installed at user scope — prefer them over shell fallbacks:

| Server | Tools prefix | Use for |
|--------|-------------|---------|
| **codedb** | `mcp__codedb__*` | Code navigation, symbol search, file reads (faster than Bash+Read) |
| **github** | `mcp__github__*` | List/view/create/edit issues, create PRs, check workflow runs. Prefer over shelling out to `gh`. |
| **sqlite** | `mcp__sqlite__*` | Query `dashboard.db` (tables: `agents`, `events`, `token_usage`). Use for debugging DB state instead of `sqlite3` via Bash. |

**Tool preference rules:**
- To read/search code → `mcp__codedb__*` over `Read`/`Bash grep`
- To work with GitHub issues/PRs → `mcp__github__*` over `gh` CLI in Bash (existing scripts like `create_ticket.py` / `update_ticket.py` may still use `gh` internally — do not refactor them)
- To inspect live DB state → `mcp__sqlite__*` over `sqlite3` in Bash

## When Working on This Project

- Always run agents with CLAUDE_AGENT_ROLE env var set (ba, coder, tester)
- The dashboard runs at localhost:8000 — assume it's already running
- Use codedb MCP tools for code navigation when available (faster than Read)
- Read the issue body carefully before implementing — acceptance criteria 
  is the contract

## Standard Docs Structure

Every Commander project (including Commander itself) shares the same docs
layout so the documentor and agents always know where things live:

```
README.md            hub linking to everything below
CHANGELOG.md         change-log
docs/
  quickstart.md      install + first run
  tutorial.md        full walkthrough
  workflow.md        Bulk Create -> Run Sprint -> Finish/Rerun
  architecture.md    system map (documentor-owned AUTO region)
  todo.md            sprint history (documentor-owned AUTO region) + hand TODO
  milestones/        active milestone tracking (one .md per initiative)
  features/          one .md per subsystem
  bulk-create/       saved bulk-create prompts (YYYY-MM-DD-N-<topic>.md; N =
                     sequence within the day so batches file in order; BKK
                     dates; never edit a file whose batch already ran)
  changelog/         dated per-sprint entries (uat/ and prd/)
```

`architecture.md` and `todo.md` (formerly `milestones.md`) each contain an
`<!-- AUTO:... -->` region owned by the documentor — do not hand-edit inside
the markers. `docs/milestones/` holds hand-maintained milestone tracking files
(e.g. `sprint-lifecycle-redesign.md`, whose design contract is
`docs/architecture/sprint-lifecycle.md`).

**Enforce it with the scaffold script:**
- New projects get this structure stamped into the initial commit by
  `init_project.py`.
- For an existing project: `python3 scripts/scaffold_project.py --project <path>`
  creates any missing standard files from template and never overwrites
  existing content, so it is always safe to re-run. Add `--check` to report
  drift (exit 1 if anything is missing) without writing. Stray top-level docs
  are reported for manual review, never deleted.

## Standard Project Layout

Two layouts are supported. Use `--nested` with `init_project.py` for new projects.

**Nested layout** (`--nested`, recommended for new projects):
```
~/dev/<project>/
  main/              # primary working clone (master branch)
  coder/             # coder agent clone (develop branch)
  tester/            # tester agent clone (develop branch)
  uat/               # UAT clone (develop branch) — optional
  .commander/        # sprint config at project root, outside any clone
    sprint.yaml
    logs/
    sprints/
    alerts/
```

**Flat layout** (default, backward compatible):
```
~/dev/<project>/          # main clone — master branch
~/dev/<project>/uat/      # UAT clone — develop branch
~/dev/<project>-coder/    # coder agent clone — develop branch
~/dev/<project>-tester/   # tester agent clone — develop branch
~/dev/<project>/.commander/sprint.yaml   # inside main clone
```

The sprint manager auto-discovers `.commander/sprint.yaml` by walking UP
from the current working directory, so it works from inside any clone in
both layouts.

To migrate an existing flat project to nested:
`scripts/migrate_project_layout.py <project-name>`

## Useful Scripts

- `scripts/create_ticket.py` — file a new issue with template
- `scripts/update_ticket.py` — change labels (in-progress, sit, uat, blocked)
- `scripts/comment_ticket.py` — add comment to issue
- `scripts/post_test_report.py` — tester uses this for structured reports
- `scripts/start_feature.py` — coder uses this to create feature branch
- `scripts/finish_feature.py` — tester uses this to merge to develop
- `scripts/init_project.py` — onboard a new project (`--nested` for nested layout)
- `scripts/migrate_project_layout.py` — migrate flat project to nested layout
- `scripts/migrate_add_uat.py` — add UAT clone to an existing project

## Issue Estimator

The Issue Estimator agent reads a ticket after it is created and produces structured sizing metadata: size estimate, confidence, files likely affected, dependency graph, and risk flags.

**When to run it:**
- After BA creates a ticket and you want sizing data before sprint planning
- From the CLI: `python3 services/sprint_manager/estimate_issue.py --issue <N> [--repo owner/repo] [--save-comment] [--save-label] [--force]`
- As a slash command: `/estimate <issue-url>`

**Output saved to:** `<project>/.commander/estimates/issue-<N>.json`

**Size scale:** S=1–5min, M=~15min, L=~30min, XL=>30min

**Caching:** estimates are cached — re-running without `--force` returns the cached result instantly.

**Sprint manager integration:** when `sprint_manager.py` dispatches tickets, it reads cached estimates (if present) and:
- Logs size, estimated hours, and confidence for each ticket
- Warns on serious risk flags (`touches-db-schema`, `security-sensitive`, `breaks-tests`)
- Warns when two pending tickets share files in `files_likely_affected`

**Model:** Haiku 4.5 (cheaper; the task is structured and well-defined — no Sonnet needed).

**Agent definition:** `apps/dashboard/.claude/agents/estimator.md`

## Out of Scope

- DO NOT add Discord, Slack, or other notification systems (separate sprint)
- DO NOT add auth (single-user, local only for now)
- DO NOT add caching layers beyond the existing 30s GitHub cache

## When in Doubt

Default is **execute**, not ask. Only stop for genuine ambiguity or destructive actions. See "Confirmation Policy — STRICT" at the top of this file.

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

## Session Notes — 2026-06-08

### Bugs fixed (PRs #647, #652, #653, #654)

**Bulk create attachments not appearing in issues**
- `_build_body_with_images` used `![img]()` for all files; non-image types (HTML, PDF) render as broken icons on GitHub. Fixed: only use inline image syntax for known image extensions (`.png .jpg .jpeg .gif .webp .svg`); all others use plain `[link]()`.
- Added idempotent guard (`if "## Attachments" in body: return body`) to prevent double-injection on retry.
- Added retry pre-commit inside `bulk_post_selected` when `image_url_map` is empty after server restart.

**Sprint nav pill showing wrong sprint (S49 instead of S50)**
- `get_sprint_nav_status` was picking the first sprint with active-column issues without checking if it was already finished. Fixed: skip sprints found in `_finished_sprint_summaries` before selecting the current sprint.

**Dispatch level separators missing on page load**
- Separators appeared only after live data updated. Fixed: on first live fetch, if cache was empty and data arrived, trigger a full `_smgmtRender` instead of a patch.

**Nav pill/panel showing stale GitHub label counts (e.g. 2 UAT, 5 Backlog)**
- Pill and panel were using GitHub label counts for running sprints. Fixed: added `_snavColsFromLive(live)` helper that derives counts from the live snapshot; pill uses this for running sprints; panel donut uses it too. Single source of truth.

**Bulk create stuck on "Posting..." after server restart**
- `_bulk_jobs` is in-memory; restarts cleared it. Added `_get_bulk_job(job_id)` helper that lazy-loads from `uat/.commander/bulk-jobs/{id}.json` on miss. Replaced all 30 `_bulk_jobs.get(job_id)` call sites. **Gotcha:** using `replace_all: true` on that substitution also hit the line inside the helper itself, causing infinite recursion (`RecursionError` on every request). Always use targeted edits for the helper's own dict lookup.

**BA run button — one blink, no progress (RecursionError)**
- Root cause: the `replace_all` fix above. The helper called itself instead of the underlying dict. Fixed with a targeted single-line edit restoring `_bulk_jobs.get(job_id)` inside the helper.

**Events API returning `[]` despite data in DB**
- `/api/projects/{slug}/events` was querying `WHERE project = <slug>` but the `events` table stores `project = 'owner/repo'` (full path). Fixed: `project_key = matched["repo"]` (full path, not `matched["repo"].split("/")[-1]`).

**Activity tab always showing "No events found" or "Loading activity..."**
- Two causes: (1) `/api/home` omitted `repo` field from project payloads — `_projectData.repo` was `undefined` in JS, so `evlFetch` built URL `/api/projects//events` (double slash → 404). Fixed: added `"repo": repo` to both the normal result and the `_idle()` sentinel in `_home_project_data`. (2) `evlFetch()` bails early if `_projectData` is null at call time — added retry in the `_projectData`-arrived handler.

**Cross-project sprint lock blocking unrelated projects**
- `_any_sprint_running()` scanned ALL projects. A running sprint on `commander` blocked starting any sprint on `perf-coach`. Fixed: added `project=` param; both call sites in `/api/sprints/run` and `/api/sprints/{label}/rerun` now pass the target project.

**Orphaned uvicorn worker holding port 8000 after server crash**
- After the main uvicorn process dies, a multiprocessing worker can survive as an orphan (PPID=1) and hold the port. `start_prd.sh` detects stale PID files but not orphaned workers. Symptom: `start_prd.sh` starts a new process that immediately fails to bind. Fix: `lsof -i :8000 -sTCP:LISTEN` to find the orphan, then `kill -9 <pid>` before restarting.

**perf-coach sprint 46 — all tickets fail with `design_docs_missing`**
- `_design_docs_guard` in `sprint_manager.py` requires `PRODUCT.md` and `DESIGN.md` in the coder worktree. This guard applies to ALL projects, not just commander. perf-coach never had these files. Fixed: created both files in `perf-coach/coder` develop branch (pushed directly — no PR). Any new project onboarded via commander must have these files on its develop branch before running sprints.

### Logs tab changes

- Activity view is now the **default** view when opening the Logs tab (was Runs).
- Source filter chips (All / Agent & Sprint / Dashboard / GitHub) are visible by default.
- `evlFetch()` is called on `logsInit()` and also retried when `_projectData` arrives late.

### Server restart procedure (uat)

```bash
kill -9 $(cat apps/dashboard/prd.pid)
rm -f apps/dashboard/prd.pid
bash scripts/start_prd.sh
```

If port 8000 is still held after kill: `lsof -i :8000 -sTCP:LISTEN` → kill the orphan PID → then restart.

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
