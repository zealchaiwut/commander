#!/usr/bin/env python3
"""Scaffold and enforce the standard Commander docs structure.

Every project run by Commander (including Commander itself) shares the same
docs layout:

    README.md              hub linking to everything below
    CHANGELOG.md           change-log
    docs/
      quickstart.md        install + first run
      tutorial.md          full walkthrough
      workflow.md          Bulk Create -> Run Sprint -> Finish/Rerun
      architecture.md      system map (documentor-owned region)
      milestones.md        sprint history (documentor-owned region)
      features/            one .md per subsystem
      bulk-create/         saved bulk-create prompts and outputs
      changelog/           dated per-sprint entries

This script stamps that structure into a target project and reports drift.

Modes
-----
default      Create any missing standard files/dirs from template. Existing
             files are NEVER overwritten, so re-running is always safe. Stray
             top-level docs are reported for manual review, never deleted.
--check      Report drift (missing standard items, stray top-level docs) and
             exit non-zero if anything is missing. Writes nothing. CI-friendly.

Usage
-----
    python3 scripts/scaffold_docs.py --project ~/dev/perf-coach/main
    python3 scripts/scaffold_docs.py --project . --check
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── skeleton content ─────────────────────────────────────────────────────────
# {project} is substituted with the project name (the repo directory name).
# These are project-agnostic skeletons; each project fills in the detail over
# time, and the documentor maintains the AUTO regions.

_QUICKSTART = """\
# Quick Start

Get {project} running locally in a few minutes. For the full walkthrough see
[tutorial.md](tutorial.md).

## Prerequisites

- _List runtimes, accounts, and CLIs required._

## Install

```bash
# 1. Clone
git clone <repo-url> {project}
cd {project}

# 2. Install dependencies
# ...

# 3. Configure
# copy the example env file and fill in secrets

# 4. Run
# start command here
```

## First run

_Describe the first thing a new user should do once it is running._

## Next steps

- [tutorial.md](tutorial.md) — full walkthrough
- [workflow.md](workflow.md) — how work flows from idea to shipped
- [features/](features/) — per-subsystem reference
"""

_TUTORIAL = """\
# Tutorial

A full walkthrough of {project}: setup, the main flows, and how the pieces fit
together. Start with [quickstart.md](quickstart.md) if you just want it running.

## Setup

_Step-by-step setup beyond the quick start._

## Core concepts

_Explain the domain model and the main objects a user works with._

## Walkthrough

_Take the reader through a complete end-to-end task._

## Reference

- [workflow.md](workflow.md)
- [architecture.md](architecture.md)
- [features/](features/)
"""

_WORKFLOW = """\
# Workflow

How work flows from raw idea to signed-off code in {project}, driven by
Commander. Three stages: **Bulk Create**, **Run Sprint**, and **Finish / Rerun
Sprint**. Each stage names the agents it uses.

> This document describes current behavior. When a sprint changes the pipeline,
> the documentor updates this file.

## Stage 1 — Bulk Create

- Paste prompts (separated by `---`) into the Bulk Create tab.
- **BA agent** drafts each ticket (title, body, AC, UAT steps), one per prompt.
- **Estimator** sizes each draft (S/M/L/XL).
- Review and edit the drafts, then post the selected ones as GitHub issues.

Records of past batches live in [bulk-create/](bulk-create/).

## Stage 2 — Run Sprint

For each ticket in a `sprint-N` label:

- **Coder** branches off develop, implements, and pushes (`in-progress` → `SIT`).
- **Tester** writes and runs tests per acceptance criterion, posts a report.
- **Fix loop** re-dispatches the coder on failure, up to 3 attempts, then tags
  `needs-rework`.
- **Quality gates** (typecheck, lint, design, pytest, merge-preview) must pass.
- **Documentor** updates the changelog and docs.
- On pass, the feature branch merges into the sprint branch and the issue → `UAT`.
- **Reviewer** runs once after the sprint PR, posts findings, and opens
  follow-up tickets.

## Stage 3 — Finish / Rerun Sprint

- **Finish:** the human reviews UAT tickets, closes the good ones; a sprint
  summary is posted as a GitHub issue, which marks the sprint finished.
- **Rerun:** tickets tagged `needs-rework` run as an independent sub-sprint
  (`sprint-N.1`, `sprint-N.2`, …) with their own label, branch, PR, and summary.

## Agents at a glance

| Stage | Agent | Role |
|-------|-------|------|
| Bulk Create | BA | Draft ticket title, body, AC, UAT steps |
| Bulk Create | Estimator | Size each draft |
| Run Sprint | Coder | Implement on a feature branch |
| Run Sprint | Tester | Write/run tests, post report |
| Run Sprint | Documentor | Update changelog and docs |
| Run Sprint | Reviewer | Review diff, open follow-up tickets |
"""

_ARCHITECTURE = """\
# Architecture

System map for {project}: the major components, how they talk to each other,
and where data lives. The documentor maintains the auto-managed region below;
discuss changes with it via the documentor command.

<!-- Anything above this line is hand-maintained. -->

<!-- AUTO:architecture START -->
<!-- The documentor manages everything between these markers. Do not edit by hand. -->

_No architecture recorded yet. The documentor populates this after the next
sprint that touches structure._

<!-- AUTO:architecture END -->
"""

_MILESTONES = """\
# Milestones

A running history of what each sprint shipped for {project} — derived from
sprint summaries, not a forward roadmap. The documentor appends one entry per
finished sprint inside the auto-managed region below.

<!-- Anything above this line is hand-maintained. -->

<!-- AUTO:milestones START -->
<!-- The documentor manages everything between these markers. Do not edit by hand. -->

_No milestones recorded yet. The first entry appears after the next sprint finishes._

<!-- AUTO:milestones END -->
"""

_BULK_CREATE_README = """\
# Bulk Create — Prompt & Output Record

Durable record of every bulk-create batch run against {project}. Each file holds
the prompts pasted into the Bulk Create tab plus the issues they produced, so a
batch can be reviewed, re-run, or copied later.

## Naming

```
docs/bulk-create/YYYY-MM-DD-<topic>.md
```

One file per batch.

## File format

```markdown
# <Batch title>

**Date:** YYYY-MM-DD
**Sprint label:** sprint-N (or NEW)
**Default labels:** ...
**Status:** drafted | posted | run

## Prompts

(one code block; prompts separated by `---`)

## Posted issues

| # | Title | Size |
|---|-------|------|
```
"""

_CHANGELOG = """\
# Changelog

Per-sprint changelog for {project}. Entries are written by the documentor when a
sprint finishes. Dated per-sprint files live under [docs/changelog/](docs/changelog/).
"""

_FEATURES_README = """\
# Features

One document per subsystem of {project}. Add a file here when a new subsystem
lands; the documentor keeps existing files current.
"""

# ── standard structure definition ────────────────────────────────────────────
# Standard files: created from template when missing, and NEVER overwritten.
# Existing project content (narrative docs) and the documentor's AUTO-region
# history (architecture.md, milestones.md) are always preserved. Re-running is
# therefore always safe.
_STANDARD_FILES: dict[str, str] = {
    "docs/quickstart.md":          _QUICKSTART,
    "docs/tutorial.md":            _TUTORIAL,
    "docs/workflow.md":            _WORKFLOW,
    "docs/architecture.md":        _ARCHITECTURE,
    "docs/milestones.md":          _MILESTONES,
    "docs/bulk-create/README.md":  _BULK_CREATE_README,
    "docs/features/README.md":     _FEATURES_README,
    "CHANGELOG.md":                _CHANGELOG,
}

_DIRS = [
    "docs",
    "docs/features",
    "docs/bulk-create",
    "docs/changelog",
    "docs/changelog/uat",
    "docs/changelog/prd",
]

# Top-level docs/ entries that are part of the standard. Anything else found at
# the top level of docs/ is reported as "stray" for manual review.
_STANDARD_DOCS_ENTRIES = {
    "quickstart.md", "tutorial.md", "workflow.md", "architecture.md",
    "milestones.md", "features", "bulk-create", "changelog",
    # commonly-kept extras that are not stray:
    "README.md", "TRAVEL_PLAYBOOK.md", "inspiration", "testing", "smoke-tests",
}


def scaffold(root: Path, project: str, *, check: bool) -> int:
    """Return 0 if structure is compliant (or was made compliant), 1 on drift
    in --check mode."""
    created: list[str] = []
    missing: list[str] = []

    # Directories
    for d in _DIRS:
        p = root / d
        if not p.exists():
            if check:
                missing.append(d + "/")
            else:
                p.mkdir(parents=True, exist_ok=True)
                created.append(d + "/")

    # Standard files — created if missing, NEVER overwritten. Existing project
    # content and the documentor's AUTO regions are always preserved.
    for rel, template in _STANDARD_FILES.items():
        p = root / rel
        if not p.exists():
            if check:
                missing.append(rel)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(template.format(project=project))
                created.append(rel)

    # Stray top-level docs entries (report only — never auto-delete)
    stray: list[str] = []
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for entry in sorted(docs_dir.iterdir()):
            name = entry.name
            if name.startswith(".") or name.endswith(".excalidraw"):
                continue
            if name not in _STANDARD_DOCS_ENTRIES:
                stray.append("docs/" + name + ("/" if entry.is_dir() else ""))

    # Report
    print(f"Project: {project}  ({root})")
    if created:
        print(f"\nCreated ({len(created)}):")
        for c in created:
            print(f"  + {c}")
    if check and missing:
        print(f"\nMissing ({len(missing)}):")
        for m in missing:
            print(f"  - {m}")
    if stray:
        print(f"\nStray (review manually — not part of standard, not deleted):")
        for s in stray:
            print(f"  ? {s}")
    if not created and not missing and not stray:
        print("\nStructure is compliant. Nothing to do.")
    elif not check and not stray:
        print("\nStructure is now compliant.")

    if check and missing:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Scaffold/enforce the standard Commander docs structure.")
    ap.add_argument("--project", required=True, help="Path to the project root (the git working clone).")
    ap.add_argument("--name", help="Project name for templates (default: project dir name).")
    ap.add_argument("--check", action="store_true", help="Report drift and exit non-zero if missing. Writes nothing.")
    args = ap.parse_args()

    root = Path(args.project).expanduser().resolve()
    if not root.is_dir():
        print(f"error: project path is not a directory: {root}", file=sys.stderr)
        return 2

    # Project name: explicit --name, else dir name (handle nested layout's main/)
    name = args.name
    if not name:
        name = root.name
        if name in ("main", "prd") and root.parent != root:
            name = root.parent.name

    return scaffold(root, name, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
