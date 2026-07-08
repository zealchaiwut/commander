# Dead Code & Dead Endpoint Purge

**Date:** 2026-07-03
**Sprint label:** sprint-109
**Default labels:** cleanup
**Status:** posted

Source: 2026-07-03 four-agent full review (API surface map + dead-code sweep,
read-only, against develop @ 7520d393). This batch deletes code with zero
callers: two orphaned files, three dead HTTP endpoints, one leftover write of
the deprecated `planned` lifecycle state, and an archive decision for six
orphaned one-shot scripts. All deletions are evidence-backed (full-repo grep
across static HTML, src JS, dist bundle, hooks, scripts, services, tests).

## Prompts

```
Delete scripts/build_frontend_bundle.py — orphaned and broken hand-rolled bundler.

The real bundler is esbuild (package.json "build": esbuild apps/dashboard/static/src/index.js --bundle → static/dist/bundle.js). scripts/build_frontend_bundle.py (~137 lines) is a hand-rolled fallback with zero external references: full-repo grep for build_frontend_bundle hits only the file itself — not CLAUDE.md, README, docs/, .github/, any .sh, any .py, any launchd plist. It is also broken: its MANIFEST lists sprint-board/drag-drop.js which does not exist on disk, and build() raises SystemExit on any missing entry, so the script cannot run at all. The MANIFEST is additionally stale versus the real esbuild import graph (missing plan-next, history, scheduled-run, board-overlay, reconcile-modal, bulk-complete-modal, progress-*, shell/*, settings/cleanup).

Fix: delete the file. Grep once more for build_frontend_bundle before deleting to confirm no new reference appeared; confirm npm run build still succeeds afterwards.

AC must cover: file removed; no reference to build_frontend_bundle remains anywhere in the repo; npm run build exits 0.
---
Delete apps/dashboard/routers/split_ticket_service.py — test-only module shadowed by the live XL-split path.

The module (~39 lines) contains only _STRIP_LABELS (frozenset) and build_child_labels(). Its sole consumer is tests/test_1454__strip_sit_uat_labels.py:22. No router, service, or routers/__init__.py imports it; it is not mounted. The live XL-split implementation is split_xl_service.py / xl_suggestions_service.py.

Fix: check whether the strip-SIT/UAT-labels behavior the test asserts is implemented in the live split_xl_service path. If yes, re-anchor the test to the live implementation and delete split_ticket_service.py. If the behavior exists nowhere live, that is a regression from ticket #1454 — port _STRIP_LABELS/build_child_labels into split_xl_service.py, wire it into the child-ticket creation path, keep the test pointing at the live code, and delete the orphan module.

AC must cover: split_ticket_service.py removed; test_1454 (or its replacement) passes against the live split path; child tickets created by XL-split do not inherit sit/uat status labels.
---
Remove three dead API endpoints with zero callers: DELETE /api/alerts/{idx}, POST backlog/cleanup, POST /api/reports/daily.

Full-consumer search (static HTML, static/src, dist bundle, hooks/, scripts/, services/, tests/) found no caller for: (1) DELETE /api/alerts/{idx} at routers/system_misc.py:99 — only POST /api/alerts (services/sprint_manager/alerts.py) and GET /api/alerts (tests) are used; (2) POST /api/projects/{owner}/{repo_name}/backlog/cleanup at routers/tickets.py:112 — the frontend backlog flow (project.html ~19581-19729) calls only backlog/cleanup-preview, backlog/triage, and backlog/triage-apply, never plain cleanup; (3) POST /api/reports/daily at routers/reports.py:29 — appears only in route-existence test scaffolding (tests/test_slim_server_py__1267.py:90) and is explicitly marked "Out of Scope" in tests/test_sprint_run_router__1262.py:5.

Fix: delete the three route handlers and their now-orphaned service helpers (follow each handler's private helpers and delete any that become unreferenced). Update the two route-existence tests to drop the removed routes. If reports.py becomes empty, remove the router file and its include_router line in server.py. Also evaluate the three test-only routes POST /api/sprints/delete-empty (sprint_crud.py:230) and GET/POST /api/sprints/order (sprints.py:78/84): if their tests only assert the route exists, delete route+test; if the tests exercise real service logic, keep the service function and test it directly, dropping the HTTP route.

AC must cover: the three dead routes return 404; no orphaned helpers remain; test suite passes; decision on delete-empty and sprints/order recorded in the PR description (removed or justified-kept).
---
Remove the last writer of the deprecated 'planned' lifecycle state (leftover half of #1686).

apps/dashboard/startup.py:2674-2675 _sprint_signoff_set_approved() still does existing["state"] = "planned" when state is None/draft. This directly contradicts the #1686 invariant documented in db.py:533 ("Nothing writes it anymore") and db.py:587-588 (a "planned" key here would be unreachable dead code). It is reachable only through the signoff approve endpoint, which is default-disabled (config.sprint_signoff_disabled() default "1"), so it is dormant — but it is the one place still emitting the deprecated value and will poison the invariant the moment the flag is flipped.

Fix: stop writing "planned" — write the state the post-#1686 lifecycle expects for an approved-but-not-run sprint (per docs/architecture/sprint-lifecycle.md; likely leave state as "draft" or advance to the sanctioned next state). Keep the read-tolerant legacy handling (_RERUN_REUSABLE_PLAN_STATES at startup.py:2189 and the db.py canonical_lifecycle mapping) — those are intentional forward-only reads. Add a regression test asserting no code path writes state="planned" (e.g. grep-based or a unit test on _sprint_signoff_set_approved).

AC must cover: _sprint_signoff_set_approved no longer writes "planned"; legacy reads unchanged; regression test in place; db.py invariant comments still true.
---
Archive six orphaned one-shot scripts out of scripts/ into scripts/archive/.

These six have zero references in CLAUDE.md, README, docs/, .github/, launchd plists, install_launchd.sh, or any other script (self-references only): scripts/backfill_sprint_summary_label.py (111 lines, one-off label backfill), scripts/batch_approve.py (60, manual ops tool superseded by the batch-approve UI / routers/bulk_tickets), scripts/clean_sprint_issues_json.py (188, one-off cleanup), scripts/resync_issues_mirror.py (125, manual mirror repair), scripts/migrate_add_agents_clone.py (~200, landed migration), scripts/migrate_attachments_to_branch.py (~280, landed migration).

Fix: create scripts/archive/ with a short README stating these are landed one-shots kept for reference and excluded from support; git mv the six files there. Do NOT delete — migrations document how existing state came to be. Exception: if resync_issues_mirror.py still works against the current mirror schema, keep it in scripts/ and instead add a one-line entry for it under Useful Scripts in CLAUDE.md (a working mirror-repair tool is worth keeping discoverable); archive the other five.

AC must cover: scripts/ contains only referenced-or-documented scripts; archive README present; resync decision recorded; no import/test breaks (grep confirms nothing imports the moved files).
```

## Posted issues

| # | Title | Size |
|---|-------|------|
| 1770 | Delete orphaned and broken build_frontend_bundle.py | S |
| 1771 | Remove orphan split_ticket_service.py, anchor test to live path | M |
| 1772 | Remove three dead API endpoints and orphaned helpers | M |
| 1773 | Remove last writer of deprecated 'planned' lifecycle state | M |
| 1774 | Archive six orphaned one-shot scripts from scripts/ | M |
