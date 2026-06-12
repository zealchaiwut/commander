# Deploy tab follow-ups (from testing the live feature)

**Date:** 2026-06-10
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

The deploy/restart + Render env-editor feature shipped in sprint-55
([2026-06-09-deploy-restart-env.md](2026-06-09-deploy-restart-env.md), already
delivered). After running the live Deploy tab, these refinements came up.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Scope the Deploy tab to the current project only. Today the Deploy tab shows every project's environments in one grid (commander prd/uat AND perf-coach prd/uat at once). It should show only the environments of the project you are viewing. When on commander's Deploy tab show commander prd and uat; when on perf-coach show perf-coach prd and uat. Filter the deploy-grid by the active project slug. Acceptance: opening a project's Deploy tab lists only that project's environments, not other projects'.
---
Show and edit the local run folder and port on each Deploy card. For local-host environments, each Deploy card should display the working folder the environment runs from (its git clone path) and the port its server listens on. Make both editable from the card or an inline edit control, and persist them to the per-environment deploy config (working_dir already exists; add a port field). The restart/start path should use the configured port instead of a hardcoded 8000/8001. Validate that the folder exists and is a git clone, and that the port is a valid free number, before saving. Acceptance: each local env card shows its folder and port; I can change them and the value is used on the next deploy/restart.
---
Stream a live log after Deploy or Restart. When I click Deploy or Restart, open a small live log panel under that card and tail the action output plus the environment's server log, showing roughly the last 10 to 20 lines and following new output as it arrives (like tail -f, capped to the last lines). For a git-pull deploy show the pull output then the restart and health result; for a restart show the launchctl or script output and the post-restart health check; for Render show the build status stream. Auto-collapse when the action finishes. Acceptance: after clicking Deploy or Restart I can watch a live, capped tail of what is happening and see success or failure inline, without leaving the Deploy tab.
---
Add explicit Start and Stop controls and clarify Deploy vs Start. Today the buttons are Deploy (git pull then restart) and Restart (bounce). That conflates lifecycle with deployment. Add Stop (halt the environment's server) and Start (bring it up) as first-class actions next to Deploy and Restart, so an environment can be stopped and started independently, not only restarted. Keep Deploy meaning pull-latest then start or restart. Reflect the real run state on the card (running, stopped, idle) and enable or disable Start vs Stop accordingly. For local envs use launchctl bootout and bootstrap or the stop and start scripts; for Render map Stop and Start to the closest Render API actions, or hide them if Render has no stop. Acceptance: each environment card has Deploy, Restart, Start, Stop with correct enabled state, and the card shows whether the env is currently running or stopped.
---
Make the deploy and restart launchd path work headless via a gh token. Discovered while testing: restarting a local environment via launchd fails because the dashboard startup repo check runs gh, which is authenticated via the macOS keychain, and a detached launchd process cannot read the login keychain, so gh fails auth and the server exits with "Configured GITHUB_REPO does not exist or is inaccessible". The dashboard only starts cleanly from an interactive shell (start_prd.sh) where the keychain is unlocked. Fix so the dashboard runs under launchd: give gh a headless token by setting GH_TOKEN or GITHUB_TOKEN in the launchd plist EnvironmentVariables and the agent .env, which gh prefers over the keychain, and have the generalized launchd installer wire it in. This is a prerequisite for the deploy and restart feature to function under the production launchd runner on the Mac mini. Acceptance: the dashboard starts and stays up under launchd without an interactive shell, and the Deploy tab Restart button cleanly restarts it.
```

## Notes

- The **headless gh-auth** prompt is the important one: the deploy/restart
  launchd path does not work until gh authenticates without the keychain. Same
  root cause as the estimator "not logged in" issue. Prerequisite for the
  feature to run under the production launchd runner.
- "Deploy" today means pull-latest then (re)start; the Start/Stop prompt makes
  lifecycle explicit.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
