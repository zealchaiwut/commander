# Sprint Manager

The sprint manager automates the full BA → Coder → Tester → UAT loop for every
ticket in a sprint. It dispatches Claude Code agents as subprocesses, polls for
results, runs quality gates, and generates a sprint summary.

![image](./flow.png)

---

## How It Works

A sprint is a GitHub label (`sprint-N`) applied to one or more issues. When you
run a sprint, the manager works through each ticket in order:

1. **Coder** — creates a feature branch, implements the ticket, pushes (label
   move to `SIT` is done by sprint_manager). Today dispatched via Claude Code
   CLI; a Cline-only coder backend is [designed but not implemented](coder-backends.md).
2. **Tester** — checks out the branch, writes pytest tests against each AC item,
   runs them, posts a test report, merges to `develop` if tests pass, moves label
   to `UAT`
3. **Gate check** — if the tester rejects the ticket, the sprint manager labels
   it `needs-rework` (with an `end_reason`) and moves to the next ticket
4. When all tickets are done, a sprint summary issue is filed on GitHub and the
   sprint state JSON is written to `.commander/sprints/` (per-label
   `{label}-state.json` / `{label}-summary-*.md`)

---

## Planning a Sprint

### From the dashboard

Open the **Sprint Mgmt** tab, select a project, and use the backlog section to
multi-select issues. Click **Plan sprint** to assign the `sprint-N` label to the
selected issues and set a sprint goal.

### From the CLI

```bash
python3 scripts/sprint_planner.py --repo owner/repo --sprint 8 \
  --issues 121 122 123
```

---

## Running a Sprint

### From the dashboard

Click **Run sprint** on the Sprint Mgmt tab. The manager starts in the
background and the dashboard polls `/api/sprint-status` every 5 seconds to
show live progress.

### From the CLI

```bash
# label is positional; --repo is optional when sprint.yaml sets repo_name
python3 services/sprint_manager/sprint_manager.py sprint-8 \
  --repo zealchaiwut/commander
```

The sprint manager reads worktree paths from `.commander/sprint.yaml` (see
[Sprint Manager Config](../../.commander/README.md)). It auto-discovers the
config by walking up from the current directory, so it runs from inside any
clone.

---

## Rerunning a Sprint

A finished sprint label is **terminal** — it is never re-dispatched under the
same label (`POST /api/sprints/run` rejects terminal labels with 409). To
re-attempt failed tickets, click **Re-run** on the sprint card: this creates a
**child sprint** (`sprint-N.1`, `sprint-N.2`, …) branched off the sprint base
branch (`sprint/sprint-N`), moves the unsettled tickets into it, and runs only
those. Original labels are kept; nothing is reset to `backlog`. When the last
child completes, the parent flips from `partial_finished` to `completed`. See
[`sprint-lifecycle.md`](../architecture/sprint-lifecycle.md).

---

## Killing a Sprint

Click **Kill sprint** (or the inline kill button on a sprint card) to terminate
the running subprocess and mark the sprint stopped. The sprint summary records
the partial results.

---

## Sprint States

The lifecycle redesign unified the state enum (`db.LIFECYCLE_STATES` +
`canonical_lifecycle()` display mapping). Legacy `cancelled`/`failed` now map to
`needs_rework`; `finished` → `completed`; `planning` → `draft`.

| State | What it means |
|---|---|
| `draft` | Sprint created, not yet preflighted/dispatched |
| `planned` | Preflight confirmed, not yet running |
| `running` | Manager subprocess active |
| `completed` | All tickets reached UAT/done |
| `needs_rework` | A ticket failed, the run was stopped, or the process was lost (carries `end_reason`) — re-run into a child sprint |
| `partial_finished` | **Derived, never stored** — this sprint's tickets moved to a child sprint not yet completed |

---

## Sprint Summary

When a sprint finishes (or is stopped), the manager generates a Markdown summary
and files it as a GitHub issue with the `sprint-summary` label. The summary
includes:

- What shipped (ticket, time taken, outcome)
- What didn't ship (failure category and reason)
- Stats: total tokens, avg ticket time, quality-gate pass rate, cost estimate
- Suggested follow-up actions

Sprint state files are also written to `.commander/sprints/sprint-N-state.json`
and `.commander/sprints/sprint-N-summary-YYYY-MM-DD.md`.

---

## Quality Gates

After the tester exits 0, the sprint manager runs seven gates in this order
(cheap/deterministic first so bad tickets fail before expensive gates):

| # | Gate | Tool | Skip flag / env var |
|---|------|------|-------------|
| 1 | **typecheck** | mypy (Python), tsc --noEmit (TypeScript) | `--no-gate-typecheck` / `COMMANDER_GATE_TYPECHECK=0` |
| 2 | **lint** | ruff (Python), eslint/biome + prettier (JS/TS) | `--no-gate-lint` |
| 3 | **frontend-lint** | eslint/biome + prettier on JS/TS only | `--no-gate-frontend-lint` / `COMMANDER_GATE_FRONTEND_LINT=0` |
| 4 | **design** | `npx impeccable detect` — UI anti-pattern detector, no LLM | `COMMANDER_GATE_DESIGN=0` |
| 5 | **pytest** | pytest -x on changed test files | `--no-gate-pytest` |
| 6 | **monolith** | blocks new routes added to `server.py` (`COMMANDER_GATE_MONOLITH`) | `--no-gate-monolith` |
| 7 | **merge-preview** | `git merge --no-commit --no-ff` dry run | `--no-gate-merge-preview` |

Gate scope (`--gate-scope changed\|full`) controls whether gates run on changed
files only or the full tree.

Gates stop on first failure — the issue is reverted to SIT and a structured
comment is posted. All gates are individually skippable via CLI flags or env
vars. Pass `--skip-gates` to bypass all gates and force-merge (use with caution).

Gates that don't find the required tool (mypy, tsc, eslint, impeccable) skip
gracefully with a warning rather than failing the build.

---

## Coder TDD Workflow

The coder prompt instructs the agent to follow TDD:

1. Read the issue's `## Acceptance Criteria` section
2. Write pytest tests that encode each criterion **before** any implementation
3. Implement until all tests pass
4. Tests must not be deleted, skipped, or weakened to achieve a passing run

If the project has `PRODUCT.md` and `DESIGN.md` at the repo root, the coder
reads them before touching frontend/UI files. Use these to codify design
conventions and anti-patterns.

---

## Configuration

Sprint manager reads `.commander/sprint.yaml`. After cloning, run:

```bash
./.commander/setup.sh
```

Key fields:

```yaml
repo_name: owner/repo

worktrees:
  coder: /path/to/coder-clone
  tester: /path/to/tester-clone

paths:
  scripts_dir: /path/to/scripts
  logs_dir: /path/to/.commander/logs
  sprints_dir: /path/to/.commander/sprints

dashboard:
  api_url: http://localhost:8000
```

---

## Agent Models

| Agent | Default model | Notes |
|---|---|---|
| Coder | `claude-sonnet-4-6` | Via Claude Code CLI (subscription-funded) |
| Tester | `claude-haiku-4-5` | Via Claude Code CLI (subscription-funded) |
| Reviewer | `claude-haiku-4-5` | Via Claude Code CLI (subscription-funded) |
| Estimator | `claude-sonnet-4-6` | Via Claude Code CLI (subscription-funded) |
| Documentor | `claude-sonnet-4-6` | Via Claude Code CLI (subscription-funded) |
| Sprint preflight | `claude-haiku-4-5-20251001` | Raw API (charged per token) |

Override per-agent in `sprint.yaml` under `agent_config`:

```yaml
agent_config:
  default_model: claude-sonnet-4-6   # fallback for any agent without a specific override
  coder_model: claude-sonnet-4-6
  tester_model: claude-haiku-4-5
  reviewer_model: claude-haiku-4-5
  estimator_model: claude-sonnet-4-6
  documentor_model: claude-sonnet-4-6
```

`default_model` applies to any agent that doesn't have its own key set. Per-agent keys take precedence over `default_model`.

---

## Useful Scripts

| Script | What it does |
|---|---|
| `scripts/sprint_planner.py` | Assign issues to a sprint label |
| `scripts/sprint_review.py` | Run the BA preflight review before a sprint starts |
| `scripts/sprint_init.py` | Initialise a sprint state file |
| `scripts/start_feature.py` | Coder: create and push a feature branch |
| `scripts/finish_feature.py` | Tester: merge feature branch to develop |
| `scripts/post_test_report.py` | Tester: post structured test report as issue comment |
| `scripts/create_ticket.py` | BA: file a new GitHub issue from the feature template |
| `scripts/update_ticket.py` | Move a ticket label (in-progress, SIT, UAT, blocked) |
| `scripts/comment_ticket.py` | Add a comment to an issue |
| `scripts/approve_ticket.py` | Approve a UAT ticket (sets UAT-approved, closes issue) |
| `scripts/reject_ticket.py` | Reject a UAT ticket (sets needs-rework) |
