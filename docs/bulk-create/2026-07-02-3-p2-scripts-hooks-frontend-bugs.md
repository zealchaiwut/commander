# P2 Scripts, Hooks & Frontend Bugs

**Date:** 2026-07-02
**Sprint label:** NEW
**Default labels:** bug
**Status:** drafted

Source: `docs/bug-audit-2026-07-02.md`. This batch = P2 findings in helper
scripts, hooks, and the dashboard frontend (audit items 5–11).

## Prompts

```
Fix token-usage double-counting in hooks/post_tool_used.py.

hooks/post_tool_used.py:~108: on EVERY PostToolUse event the hook re-reads the transcript, takes the LAST assistant message's usage block, and POSTs it to /api/token-usage with no dedupe — so one assistant turn containing N tool calls posts the same usage N times. token_usage totals, cost estimates, and the by-agent-model debug endpoint all over-report (multiples, not percent). Fix: dedupe before posting — track the last-posted message id/usage fingerprint (e.g. sidecar file keyed by session id, or send message_id and have the server upsert-unique). Prefer server-side uniqueness (message_id column with UNIQUE) so restarts and racing hooks stay correct; backfill note for historical inflation in the ticket, no data rewrite. AC: replaying a transcript with 5 tool uses in one assistant turn records usage exactly once; multi-turn session records once per assistant turn; existing dashboards read unchanged schema (new column additive).
---
Fix scripts/start_feature.py repo auto-detection resolving to Commander's own repo.

scripts/start_feature.py:~80: when --repo is omitted the script derives the repo from its own file location / default remote instead of the git clone it is EXECUTED in, so running it inside another project's coder clone creates the feature branch and issue links against zealchaiwut/commander. Fix: detect the repo from the current working directory's `git remote get-url origin` (like finish_feature does, or shared helper), falling back to explicit --repo; hard-error if CWD is not a git repo. AC: running from perf-coach clone targets perf-coach; running from commander clone unchanged; --repo override still wins; non-repo CWD exits with clear error.
---
Fix scripts/repair_sprint_lineage.py composite-PK collision crash.

scripts/repair_sprint_lineage.py:~170: after upserting the scoped row, the script UPDATEs the stray ('label','') row's project to the target repo — colliding with the freshly-upserted composite-PK row and crashing with IntegrityError, so the repair tool fails exactly on the rows it exists to repair. Same defect family as the db.py backfill P1 (see batch 1) — reuse the merge-or-delete helper introduced there: if the scoped row exists, merge useful fields then DELETE the '' row. AC: repair run on a DB seeded with the duplicate pair completes and leaves exactly one scoped row; idempotent second run is a no-op.
---
Guard scripts/init_project.py --rollback against deleting unmanaged directories.

scripts/init_project.py:~803: --rollback shutil.rmtree's ~/dev/<name> plus -coder/-tester siblings based only on the name argument — no check that the directory was created by init_project (no marker/registration check) and no confirmation prompt. A typo'd name deletes an unrelated project tree irrecoverably. Fix: only remove directories that contain the init marker (e.g. .commander/sprint.yaml created by this script, or an explicit .commander/init-manifest.json listing created paths — prefer writing a manifest at init time and rolling back exactly those paths); require --yes for non-interactive delete, otherwise prompt showing the exact paths. AC: rollback of a non-init directory refuses; rollback of a genuine init removes only manifest-listed paths; --yes bypasses prompt but never bypasses the manifest check.
---
Fix scripts/deduplicate_labels.py destroying label assignments.

scripts/deduplicate_labels.py:~139: to merge duplicate labels it deletes the duplicate label — which GitHub-side removes it from every issue — but never re-attaches the canonical label to those issues first. Label membership data (sprint membership! status labels!) is silently lost. Fix: before deleting a duplicate, list its issues and add the canonical label to each, then delete; dry-run mode printing the plan; abort if canonical-add fails partway. AC: merging dup->canonical leaves every previously-labeled issue carrying the canonical label; dry-run makes zero mutations; partial-failure aborts before delete.
---
Fix XL-suggestion Dismiss button ReferenceError in run-controls.

apps/dashboard/static/src/sprint-board/run-controls.js:~427: the XL-ticket suggestion banner renders an inline onclick="_pfDismissXLSuggestion(...)" but the function is a module-scoped ES export that is never attached to window, and esbuild tree-shakes it — clicking Dismiss throws ReferenceError and the banner cannot be dismissed. Fix: attach the handler via addEventListener at render time (preferred, matches other module handlers) or explicitly expose it on window like the module's other onclick bridges; audit the module for other inline-onclick handlers referencing non-global functions. Rebuild bundle (npm run build) and commit dist per deploy model. AC: Dismiss hides the banner without console errors; grep finds no inline onclick referencing non-window functions in the module.
---
Fix Cline follow-ups opt-in always sent as false from the run modal.

apps/dashboard/static/src/sprint-board/run-controls.js:~909: the run-confirm flow reads the follow-ups opt-in flag AFTER _pfClose() has already reset it, so the run POST always sends false and the opt-in silently never takes effect. Fix: capture the flag (and any other modal state the POST needs) into locals BEFORE closing/resetting the modal, then build the request body from the captured values; add a regression test or assertion path if the module has test coverage, otherwise verify via network payload. Rebuild bundle. AC: checking the opt-in produces a run request with the flag true; unchecked sends false; modal close still resets state for the next open.
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| — | (not yet posted) | — |
