# AGENTS.md — scripts

## Purpose

CLI helper scripts for the Commander workflow — ticket creation, branch
management, label updates, sprint lifecycle operations, and project
onboarding. Called by BA, Coder, and Tester agents as well as the human
directly. Scripts are pure Python and use only stdlib + `gh` CLI.

## Key Files

- `create_ticket.py` — file a new GitHub issue from the feature template
- `start_feature.py` — create feature branch off develop (or sprint branch); used by Coder
- `finish_feature.py` — merge feature branch to target branch; **Tester-only**
- `update_ticket.py` — change GitHub labels (`in-progress`, `sit`, `uat`, `blocked`)
- `comment_ticket.py` — add a comment to a GitHub issue
- `post_test_report.py` — post structured test report to issue (Tester-only)
- `sprint_estimator.py` — estimate all backlog tickets in one pass (Sonnet)
- `sprint_review.py` — generate sprint review summary (Haiku)
- `init_project.py` — onboard a new project (`--nested` for nested layout)
- `scaffold_project.py` — stamp missing standard docs into an existing project

## Conventions

- All scripts use `argparse` for argument parsing — no positional-arg-only scripts.
- Scripts that call the GitHub API use `gh` CLI subprocess, not direct HTTP.
- Exit 0 on success, non-zero on error — agents check exit codes.
- Print human-readable status lines to stdout; machines parse the last line or a JSON blob.
- No new Python dependencies without adding to `requirements.txt`.
- Hooks in `hooks/` POST to `localhost:8000` and fail silently if the server is down.

## Danger Zones

- `finish_feature.py` — merges to `develop` or sprint branch; Tester-only; running it as Coder breaks the UAT flow.
- `update_ticket.py --status` — label transitions affect the sprint board live; incorrect transitions confuse the sprint manager.
- `init_project.py` — creates directory structure and pushes initial commit; verify the target directory before running.
- `scaffold_project.py` — safe to re-run (never overwrites); `--check` mode is read-only.

## What NOT to Touch

- `start_prd.sh` / `start_uat.sh` — production launch scripts; coordinate with the system owner before changing.
- `install_launchd.sh` — installs a system-level launchd service; do not auto-run from code.
- `com.commander.daily-report.plist` — launchd plist file; changes affect the live unattended runner.
- `setup_machine.sh` — one-time machine provisioning; do not call from agent code.
