# Workflow

The end-to-end flow from raw idea to signed-off code. The core pipeline is three
stages — **Bulk Create**, **Run Sprint**, **Finish / Rerun Sprint** — but there
are now several ways to *drive* and *augment* that pipeline (overnight, on a
schedule, grouped under a milestone, advised, signed off). The [Operating
modes](#operating-modes) section maps them; the stages below describe the core
pipeline in detail.

> This document describes current behavior. When a sprint changes the pipeline,
> the documentor updates this file.

---

## Operating modes

Pick an entry point. They all reduce to the same Stage 1 → 3 pipeline — they
differ in **who triggers it, when, and what surrounds it**.

| Mode | What it is | How to start | Best for |
|------|------------|--------------|----------|
| **Manual per-ticket** | One issue at a time, you in the loop: `/ba` → `/coder` → `/tester` interactively | Run each agent in its pane (see [tutorial §4](tutorial.md#4-the-workflow--end-to-end)) | A single feature you want to watch closely |
| **Bulk Create → Run Sprint** | Draft many tickets, label them `sprint-N`, run the whole sprint unattended | Bulk Create tab → Run Sprint (Stages 1–2 below) | A batch of related work |
| **Overnight / unattended sprint** | Same Run Sprint, but the dashboard runs under **launchd** (authoritative unattended runner) so it survives logout/reboot and you monitor from your phone over Tailscale | Start a sprint, leave it; check the board in the morning | Long sprints, away-from-desk |
| **Scheduled sprint queue** | Queue one or more sprints to fire at a set `scheduled_run_time` instead of now (#863) | Set a run time in the scheduler UI; `/api/scheduler/config` + `/api/scheduler/sprints` | "Run sprint-12 at 2am", batching off-peak |
| **Milestone-grouped** | Group issues/sprints under a GitHub **milestone**; the Roadmap view shows milestone cards + progress (#877–880) | Assign a milestone on the ticket / Roadmap tab; `/api/roadmap/milestones` | Tracking a multi-sprint initiative |
| **Advisor-driven** *(new, partial)* | A daily agent surfaces "what to build next" suggestions on the morning brief as advisory decision items | Read the home brief; act on a suggested action | Deciding the next sprint when the backlog is fuzzy |
| **Sign-off gate** | Human Approve/Reject — both for finished tickets at `UAT` and as a pending-sign-off gate on *planned* sprints before they run (#862) | Dashboard Approve/Reject; `/api/sprints/pending-signoff`, `/api/sprints/{label}/approve` | Keeping a human checkpoint in an otherwise autonomous flow |

> **Advisor caveat:** the advisor (#881) is **half-shipped** — pending a
> complete-or-revert decision (#1015). Treat its suggestions as emerging, not
> load-bearing. See [`milestones/post-lifecycle-backlog.md`](milestones/post-lifecycle-backlog.md) §5.

---

## Stage 1 — Bulk Create

Turn raw ideas into GitHub issues that are ready to run.

- **Open the Bulk Create tab** and paste one or more prompts (separated by `---`),
  each describing a feature or fix.
- **BA agent drafts each ticket** — one BA subprocess per prompt, run in parallel.
  It writes a title, body, acceptance criteria, and UAT steps in the standard
  ticket format. *Agent: BA.*
- **Estimator sizes each draft** — after the BA draft is ready, the estimator
  runs per ticket to assign a size (S/M/L/XL) and effort estimate. *Agent:
  Estimator.*
- **Review and edit** — each drafted ticket renders as an editable card. Fix the
  wording or tighten the AC before posting; edits persist into the posted issue.
- **Post selected** — the chosen tickets are created as real GitHub issues with
  the sprint label and any default labels applied. The estimate is materialised
  onto each issue (size label + estimate file).

Records of past batches live in [bulk-create/](bulk-create/).

---

## Stage 2 — Run Sprint

For each ticket in the sprint, the Coder writes the code and the Tester
validates it. Driven by `services/sprint_manager/sprint_manager.py`.

- **Run Sprint** is triggered from the dashboard for a `sprint-N` label. The
  sprint manager reads `.commander/sprint.yaml` for the repo and worktree paths.
- **Estimator pass** — the sprint estimator scans the backlog and writes effort
  data for the whole sprint before any coder runs. *Agent: Sprint Estimator.*
- **Dispatch loop** — tickets are grouped into dependency levels and processed
  in order. For each ticket:
  - **Coder** creates a `feature/<issue>-<slug>` branch off develop, implements
    the change, and pushes. The issue moves `in-progress` → `SIT`. *Agent: Coder.*
  - **Tester** checks out the feature branch, writes and runs tests for each
    acceptance criterion, and posts a structured report. *Agent: Tester.*
  - **Fix loop** — if the tester or a gate fails, the coder is re-dispatched with
    the failure context, up to `COMMANDER_MAX_FIX_ROUNDS` (default 3) attempts.
    After that the ticket is tagged `needs-rework` and the merge is blocked.
  - **Quality gates** — typecheck, lint, frontend-lint, design, pytest,
    monolith, and merge-preview run after the tester (cheap/deterministic
    first). All must pass to merge. See
    [features/sprint-manager.md](features/sprint-manager.md#quality-gates).
  - **Documentor** updates `CHANGELOG.md` (and README where relevant) for the
    ticket. *Agent: Documentor.*
  - **Merge** — `finish_feature.py` merges the feature branch into the sprint
    branch and the issue moves to `UAT`.
- **Reviewer** runs once after the sprint PR is created. It reviews the full diff,
  posts findings on the sprint summary issue, and opens follow-up tickets for
  suggestions and nits. *Agent: Reviewer.*

---

## Stage 3 — Finish / Rerun Sprint

The human reviews what reached UAT and wraps the sprint up — or reruns the
tickets that need more work.

### Finish

- **Review UAT issues** — tickets at the `UAT` label are the deliverables. Test
  them in the running app, then **Approve** the good ones (closes the issue) or
  **Reject** (sets `needs-rework`). UAT is the "done" state — there is no
  separate done stage before human sign-off.
- **Pending sign-off gate** *(optional)* — a *planned* sprint can be held in a
  pending-sign-off state for Approve/Reject before it runs, not just after
  (#862; `/api/sprints/pending-signoff`). Use it to keep a human checkpoint in
  an otherwise autonomous flow.
- **Milestone progress** — if the sprint's issues carry a GitHub milestone, the
  Roadmap view rolls their closure into milestone progress (#877–880).
- **Sprint summary** — a summary report is generated for the sprint and posted as
  a GitHub issue (labeled `sprint-N` + `sprint-summary`). Posting this summary is
  what marks the sprint as finished; the card then drops off the Sprint
  Management board.

### Rerun (sub-sprints)

When some tickets need rework, a rerun is run as an **independent sub-sprint**
(`sprint-N.1`, `sprint-N.2`, …) so the parent sprint's label, branch, PR, and
summary stay untouched:

- Tickets carrying `needs-rework` on the parent sprint are re-labeled to the
  sub-sprint (`sprint-N.1`) and the parent label is stripped from them.
- A fresh branch, PR, and summary are created for the sub-sprint.
- The sub-sprint then runs the full Stage 2 pipeline under its own label.

---

## Agents at a glance

| Stage | Agent | Role |
|-------|-------|------|
| Bulk Create | BA | Draft ticket title, body, AC, UAT steps |
| Bulk Create | Estimator | Size each draft (S/M/L/XL) |
| Run Sprint | Sprint Estimator | Effort scan for the whole sprint |
| Run Sprint | Coder | Implement on a feature branch |
| Run Sprint | Tester | Write/run tests, post report |
| Run Sprint | Documentor | Update changelog and docs |
| Run Sprint | Reviewer | Review diff, open follow-up tickets |
| Daily (out-of-band) | Advisor | Suggest what to build next on the morning brief *(new, partial — #881/#1015)* |

Default models per agent are documented in [../CLAUDE.md](../CLAUDE.md) under
"API Cost and Model Selection".
