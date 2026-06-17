# Estimation Lifecycle

## When estimation runs

Estimation runs once at ticket creation via `estimate_issue.py`. The BA agent
(or the dashboard bulk-create flow) triggers estimation immediately after a
ticket is filed so the size label and canonical JSON are both written before the
sprint starts.

## Sprint-start estimation

Sprint-start estimation (the `sprint_estimator` that runs before the per-ticket
dispatch loop) is **off by default**. It can be enabled per-project by setting
`calibration_refresh_enabled = True` in the project config, but the default is
disabled because estimation at ticket creation already provides the data
calibration needs.

## Canonical estimate path

Canonical estimates live at:

```
<project-root>/.commander/estimates/issue-<N>.json
```

Every tool that writes estimates (the CLI `estimate_issue.py`, the dashboard
`POST /api/issues/{id}/estimate`, and the preflight auto-fix) must write the
JSON to this path. The sprint-start estimator also writes here when enabled.

## How calibration reads size

Calibration uses a three-tier fallback to resolve the size for each completed
ticket:

1. **Canonical JSON** — `<project-root>/.commander/estimates/issue-<N>.json` (first choice)
2. **Sprint state estimates** — `state.estimates[issue_num].size` embedded in the sprint state file (second choice)
3. **Size label** — the `size-*` GitHub label on the issue (last resort)

This fallback means calibration does **not** require sprint-start estimation to
have run. A ticket that was estimated at creation (canonical JSON written) or
that has a `size-*` label applied by preflight auto-fix will still be included
in calibration history.

## Preflight auto-fix and canonical JSON

When the preflight auto-fix estimates a ticket (via `POST /api/sprints/{label}/preflight-fix`),
it both applies the `size-*` label **and** writes the canonical JSON file. If the
estimation subprocess exits 0 but the JSON is somehow absent, a warning is logged:

```
[preflight] size label applied but canonical JSON missing for issue #<N>
```

## Calibration rebuild

If the `calibration_cache.json` is empty or stale (e.g. after migrating from an
older Commander version), use:

```
POST /api/maintenance/calibration/rebuild?project=<slug>
```

or the CLI:

```bash
python3 scripts/rebuild_calibration_cache.py --project <slug>
```

The rebuild clears the cache and rescans all `sprint-*-state.json` files using
the three-tier size fallback above, so every completed ticket — even those with
only a `size-*` label — appears in calibration history.
