# Machine onboarding — launchd PATH, headless tokens, install doctor

**Date:** 2026-06-11
**Sprint label:** NEW
**Default labels:** enhancement
**Status:** drafted

From debugging sprint-59 on the remote Mac mini: all 9 tickets crashed in 2–4s
because the launchd plist PATH did not include the directory where the claude
CLI lives (`~/.local/bin` there), and the plist had no headless auth tokens.
Three reruns wasted before the per-issue log named it ("claude CLI not found").
Every new machine will hit some variant of this unless install wires it.

## Prompts

Paste one code block into the Bulk Create textarea. Prompts are `---`-separated.

```
Make install_launchd.sh build the service PATH from the real tool locations instead of a hardcoded list. At install time, resolve the directories that contain the claude CLI, the gh CLI, and the project venv bin on the current machine (for example via command -v from the installing user's shell), and write those directories into the plist EnvironmentVariables PATH. Warn and abort the install if claude or gh cannot be found. Today the PATH is a fixed string, and on a machine where claude lives in ~/.local/bin every agent dispatch fails with "claude CLI not found". Acceptance: installing the launchd service on a machine where claude is in a nonstandard directory produces a plist whose PATH contains that directory, and a dispatch from the launchd-run dashboard finds claude.
---
Wire headless auth tokens into the launchd service at install time. A launchd process cannot read the login keychain or shell rc files, so subscription auth for the claude CLI and keychain auth for gh both fail under the service even when they work in an interactive shell. Extend install_launchd.sh to accept and write CLAUDE_CODE_OAUTH_TOKEN and GH_TOKEN into the plist EnvironmentVariables (flags or prompts; values never echoed or committed), and document how to obtain them (claude setup-token; gh auth token). If a token is missing, print a clear warning naming what will break (agent dispatch; gh API calls). Acceptance: after install with both tokens, a sprint dispatched by the launchd-run dashboard authenticates claude and gh without an interactive session.
---
Add a machine doctor command that validates a host before its first sprint. A script (for example scripts/doctor.py, also exposed as a button or check on the dashboard) that verifies, with named pass/fail lines: claude CLI found on the service PATH and authenticates (cheap probe); gh found and authenticates; git identity set; venv present and importable; DB_PATH writable; the launchd plist PATH contains the claude and gh directories; headless tokens present in the plist when the service is installed. Exit nonzero with a summary of failures and the exact fix for each. This is the install-time complement of the per-dispatch doctor in issue 789. Acceptance: running the doctor on a machine missing claude from the service PATH reports exactly that with the fix, in under ten seconds, instead of a sprint of nine CRASH tickets.
---
Write a machine onboarding runbook in the docs. A docs/machine-onboarding.md (linked from quickstart) with the exact steps to bring up a new Commander machine: clone layout, venv per clone, claude install plus setup-token, gh auth plus token, install_launchd.sh invocation with tokens, doctor run, first sprint smoke test. Include the failure signatures table: "claude CLI not found" means service PATH; "Not logged in" means missing OAuth token; "repo inaccessible" at startup means gh has no headless auth. Acceptance: a new machine can be onboarded start to finish following only this document, and each known failure signature maps to its fix.
```

## Notes

- The per-dispatch doctor (issue #789, sprint-59) covers runtime; this batch
  covers install time. Both reference the same checks — keep the check list
  shared if convenient.
- Related earlier ticket: headless gh auth for deploy/restart
  ([2026-06-10-deploy-tab-followups.md](2026-06-10-deploy-tab-followups.md)) —
  the token-wiring prompt here supersedes/extends it to claude.
- Real incident: sprint-59 on the mini, 3 runs × 9 CRASH; root cause one
  missing PATH entry. Logs that named it: `.commander/logs/sprint-issue-N.log`.

## Posted issues

| # | Title | Size |
|---|-------|------|
| _pending_ | | |
