# AGENTS.md — scripts

## Purpose

CLI helper scripts for the Commander workflow — ticket creation, branch
management, label updates, sprint lifecycle operations, project onboarding,
and maintenance. Called by BA, Coder, and Tester agents as well as the human
directly. Scripts are pure Python or shell and use only stdlib + `gh` CLI.

## Mutation Markers

Scripts marked with `[GH]` mutate GitHub state (issues, PRs, labels, comments)
and should not be called in dry-run contexts. Scripts marked with `[Neon]` write
to the optional Neon Postgres database. Scripts marked with `[DB]` mutate the
local SQLite database. Scripts with **no marker** are read-only or write only
to local files — safe to probe without side-effects.

## Scope

Every `.py` and `.sh` file directly under `scripts/` is indexed below.
`scripts/archive/` is explicitly excluded from this requirement — it holds
superseded one-shot scripts that no longer run. `scripts/test_fixtures/` is also
excluded (it contains only data files, not executable scripts).

## Enforcement

`tests/test_2057__scripts_agents_md_coverage.py` fails if any `.py` or `.sh`
file in `scripts/` is absent from this index. This is the drift gate; it runs
on every push. Add new scripts to the appropriate group below before committing.

---

## Ticket Lifecycle

Scripts that operate on individual tickets: create, update, comment, approve,
reject, lint, report.

- `create_ticket.py` [GH] — file new GitHub issue from feature template; `--title TITLE [--body BODY] [--sprint SPRINT] [--attachment PATH]`
- `update_ticket.py` [GH] — change issue label/status via transition(); `--issue N --status {blocked,in-progress,sit,uat,...} [--force]`
- `comment_ticket.py` [GH] — post a comment on a GitHub issue; `--issue N --body TEXT`
- `approve_ticket.py` [GH] — mark a UAT issue as approved; `--issue N [--comment TEXT]`
- `reject_ticket.py` [GH] — reject a UAT issue with a required reason; `--issue N --reason TEXT`
- `lint_ticket_spec.py` — validate issue body against the canonical template; `--issue N [--repo owner/repo]`
- `post_test_report.py` [GH] — post structured test report as issue comment; `--issue N --report-file PATH [--gate GATE] [--gate-output PATH]` (Tester-only)
- `log_decision.py` — append a dated ADR entry to docs/decisions/; `--slug SLUG [--context TEXT] [--decision TEXT] [--consequences TEXT] [--implemented-by STR]`

## Sprint Lifecycle

Scripts that drive the sprint flow: branch creation, merging, estimation,
review, health gating, and promotion.

- `start_feature.py` [GH] — create feature branch off develop/sprint and push; `--issue N [--base-branch BRANCH]` (Coder-only)
- `finish_feature.py` [GH] — merge feature branch to target branch after tests pass; `--issue N [--repo owner/repo] [--target-branch BRANCH]` (Tester-only)
- `sprint_estimator.py` — estimate effort/risk for all backlog tickets in a sprint (Sonnet); `<sprint-label> [--repo owner/repo] [--config PATH]`
- `sprint_review.py` — generate sprint review summary (Haiku); `--sprint-label LABEL [--repo owner/repo]`
- `sprint_planner.py` [GH] — conversational CLI to plan the next sprint; (interactive, auto-detects sprint number)
- `sprint_init.py` — bootstrap a project for sprint_manager (.commander/sprint.yaml); `--repo owner/repo --repo-root PATH --coder-worktree PATH --tester-worktree PATH`
- `run_suite_health_gate.py` — run full pytest suite health gate for a sprint; `--sprint-label LABEL [--sprints-dir PATH] [--timeout SECONDS]`
- `promote_to_master.py` [GH] — open draft PR develop → master with sprint summary; `[--dry-run] [--title TITLE] [--draft|--ready]`
- `release.py` [GH] — merge develop into main and tag the release; `--issue N`

## Reporting

Scripts that generate reports, snapshots, and digests from project state.

- `generate_status.py` [GH] — generate STATUS.md snapshot from GitHub issues/PRs; `[--repo owner/repo] [--out PATH]`
- `sync_status_md.py` — regenerate STATUS.md and commit only when content changed; `[--repo owner/repo] [--out PATH] [--note TEXT]`
- `generate_context_digest.py` [GH] — build context digest for new Claude Code sessions; `[--repo owner/repo] [--out PATH] [--decisions PATH]`
- `generate_code_state.py` — generate docs/architecture/code-state.md for a sprint; `--sprint-label LABEL [--repo-root PATH] [--base-sha SHA] [--head-sha SHA]`
- `export_hermes_report.py` — export nightly dev-report JSON contract for Hermes; `[--dry-run] [--output PATH] [--db-path PATH]`

## Project Setup

Scripts for onboarding new projects and provisioning machines and services.

- `init_project.py` [GH] — onboard a new project (repo + clones + launchd); `[repo_name] [--nested] [--owner OWNER] [--prd-port PORT] [--uat-port PORT]`
- `scaffold_project.py` — stamp missing standard docs into an existing project; `--project PATH [--name NAME] [--check]`
- `migrate_project_layout.py` — convert flat project layout to nested; `project_name [--projects-dir DIR] [--dry-run]`
- `migrate_add_uat.py` — add a UAT clone to an existing project; `repo_name [--owner OWNER] [--uat-port PORT]`
- `find_port.py` — find a free port for a project app server; `--prefer PORT [--strategy {prefer_default,always_random}]`
- `sprint_init.py` — (see Sprint Lifecycle)
- `setup_uat_env.sh` — set up UAT clone: venv, .env, hooks, DB init
- `setup_machine.sh` — one-time machine provisioning (deps, paths, dotfiles) — do not call from agent code
- `setup_cline.sh` — install Cline CLI and verify coder worktree for sprint dispatch
- `install_launchd.sh` — install Commander dashboard as macOS LaunchAgent on port 8000
- `install_shell_shortcuts.sh` — append Commander shell functions to ~/.commander.zsh
- `install_agent_skills.sh` — install caveman + code-review-graph skills into all clones
- `uninstall_launchd.sh` — unload and remove the Commander dashboard LaunchAgent
- `run_pytest_smoke.sh` — fast smoke test subset for CI/pre-push; (no args)
- `run_tester.sh` — convenience wrapper to invoke the tester agent; `<issue-number>`
- `start_prd.sh` — start PRD dashboard on port 8000 in background (PID file + log)
- `start_uat.sh` — start UAT dashboard on port 8001 in background
- `stop_all.sh` — terminate PRD (8000) and/or UAT (8001) server processes
- `status.sh` — print which Commander server is running on each port with PID/branch
- `gh_reauth.sh` — re-authenticate gh and propagate token to launchd-detached processes

## Maintenance

Scripts for repair, backfill, calibration, migration, cleanup, and diagnostics.

- `doctor.py` — validate host readiness for a Commander sprint; `[--json]`
- `resync_issues_mirror.py` [DB] — force full GitHub → SQLite issues-mirror resync; `[--yes|--force] [--repo OWNER/REPO]`; without --yes prints a dry-run summary and exits 1
- `backfill_agent_runs_project.py` [DB] — attribute empty agent_runs.project rows; `(--dry-run|--apply) [--db PATH]`
- `audit_sprint_collisions.py` — read-only audit for sprint label collisions across projects; `[--db PATH] [--runtime-dir PATH]`
- `repair_sprint_inbox_from_github.py` [DB] — fix stale sprint lifecycle rows vs GitHub truth; `--project PROJECT (--dry-run|--apply) [--limit N]`
- `repair_sprint_lineage.py` [DB] — rebuild sprint lineage DB rows from GitHub truth; `(--dry-run|--apply) --project PROJECT [--db PATH]`
- `clean_sprint_files.py` — archive stale per-sprint runtime files for finished sprints; `--project PROJECT [--dry-run]`
- `collect_stray_estimates.py` — move stray estimate JSONs to canonical .commander/estimates/; `--project PROJECT [--dry-run]`
- `check_neon_connection.py` [Neon] — pre-flight check for Neon database connection; `[--direct]`
- `rebuild_calibration_cache.py` — rebuild estimator calibration cache from sprint history; `--project SLUG [--dry-run]`
- `seed_calibration.py` — seed estimator calibration tiers from past sprint data; `--commander-dir PATH [--record SIZE:MINUTES] [--from-sprint LABEL]`
- `seed_test_issues.py` [GH] — seed sandbox repo with mock issues for tester isolation; `--repo REPO [--wipe] [--append] [--dry-run]`
- `prune_test_files.py` — prune old pytest files keeping N most recently touched; `[--repo-root PATH] [--keep N] [--apply]`
- `deduplicate_labels.py` [GH] — detect and remove duplicate GitHub labels; `--repo REPO [--dry-run] [--ensure NAME COLOR]`
- `copy_to_tmp.sh` — snapshot gitignored config files from all 4 clones into /tmp/commander-sync/
- `copy_from_tmp.sh` — restore gitignored config files from /tmp/commander-sync/ into current clone
- `sync_uat.sh` — pull latest develop commits into the UAT clone
- `update_crg_graphs.sh` — refresh code-review-graph DBs in all agent worktrees

---

## Conventions

- All Python scripts use `argparse`; run `--help` for full arg list.
- Scripts calling the GitHub API use `gh` CLI, not direct HTTP.
- Exit 0 on success, non-zero on error — agents check exit codes.
- Print human-readable status lines to stdout; machines parse the last line or a JSON blob.
- No new Python dependencies without adding to `requirements.txt`.
- Hooks in `hooks/` POST to `localhost:8000` and fail silently if the server is down.

## Danger Zones

- `finish_feature.py` [GH] — merges to `develop` or sprint branch; **Tester-only**; running as Coder breaks the UAT flow.
- `update_ticket.py` [GH] — label transitions affect the sprint board live; incorrect transitions confuse sprint_manager.
- `init_project.py` [GH] — creates directory structure and pushes initial commit; verify the target directory before running.
- `release.py` [GH] — merges develop into main and pushes a tag; irreversible without force-push.
- `resync_issues_mirror.py` [DB] — makes real GitHub API calls for all registered repos; can exhaust rate limit.

## What NOT to Touch from Agent Code

- `start_prd.sh` / `start_uat.sh` — production launch scripts; coordinate with the system owner.
- `install_launchd.sh` — installs the system-level Commander LaunchAgent service.
- `setup_machine.sh` — one-time machine provisioning; do not call from agent code.
