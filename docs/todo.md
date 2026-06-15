# TODO (formerly milestones.md)

> **Active milestone tracking lives in [`milestones/`](milestones/).**
> Current focus:
> [`milestones/post-lifecycle-backlog.md`](milestones/post-lifecycle-backlog.md)
> — pending work grouped by topic (code-review debt, advisor, refactor, …)
> with operator decisions flagged inline.
> [`milestones/sprint-lifecycle-redesign.md`](milestones/sprint-lifecycle-redesign.md)
> is now **CLOSED** (P0–P4 shipped through sprints 73.x); its design contract
> [`architecture/sprint-lifecycle.md`](architecture/sprint-lifecycle.md) is the
> source of truth for lifecycle behavior.

A running history of what each sprint shipped — derived from sprint summaries,
not a forward roadmap. The documentor appends one entry per finished sprint
inside the auto-managed region below. Hand-written notes above that region are
preserved.

**How entries are produced:** when a sprint finishes and its summary issue is
posted, the documentor reads that summary and adds a one-line milestone — sprint
number, date, and a short description of what shipped.

## TODO

Forward-looking work not yet shipped. Remove items here when they land in a
sprint (the documentor records shipped work in the auto region below).

### Cline coder backend (optional cost split)

Use company Cline subscription for sprint **coder** dispatches only; keep
tester/BA/documentor on Claude Code. Design:
[features/coder-backends.md](features/coder-backends.md).

**Setup script (local or remote Mac mini):**

```bash
bash scripts/setup_cline.sh                  # install + doctor
cd ~/dev/commander/coder && cline auth       # interactive — pick subscription OR API key
bash scripts/setup_cline.sh --enable-followups   # safer default
# bash scripts/setup_cline.sh --enable-always     # full Cline coder
```

See [machine-onboarding.md](machine-onboarding.md#cline-coder-backend-migration).

- [x] **Phase 1 — pluggable coder backend:** `agent_config.coder.backend` +
      `use_cline_followups` in sprint.yaml (landed #916–#920)
- [ ] **Phase 2 — Cline worktree setup:** run `scripts/setup_cline.sh` on each
      machine that dispatches coders (including remote Mac mini after migration)
- [ ] **Phase 3 — telemetry (optional):** Cline hooks or `--json` log parsing into
      `/api/agent-event` and `/api/token-usage` so dashboard shows coder activity
- [ ] **Validation:** 2–3 manual tickets through Cline coder before first
      overnight sprint; confirm feature branch + push + no label/merge violations

### Agent skills — persist install (caveman + code-review-graph)

Installed via `setup_machine.sh` (full bootstrap) or `--resetup-machine` on an
existing host. Runbook: [features/agent-skills.md](features/agent-skills.md).

- [x] **Commit vendored skills:** `.mcp.json`, CRG skill dirs, caveman, and
      `skills-lock.json` are in git; `install_agent_skills.sh` still refreshes
      on `--resetup-machine`

### Resume-from-failure on gate fail (don't rework from scratch)

When a ticket fails a quality gate (design / pytest / lint / merge-preview), the
fix-round re-dispatches the agent with the failure sidecar as context, but the
agent effectively re-runs the whole ticket. Make the re-run **pick up from the
failed step** instead of redoing everything:

- [ ] **Coder:** on a fix-round, reuse the existing feature branch + its diff and
      target only the failing gate (e.g. fix the flagged design anti-pattern /
      failing test), not a full re-implementation. The branch is already checked
      out; bias the prompt to "fix what the gate flagged" over "implement #N".
- [ ] **Tester:** on re-run after a coder fix, re-test only the previously
      failing AC / gate first (fast path), then the rest — avoid a full re-verify
      from zero when only one thing changed.
- [ ] **Re-estimate after a failure:** a ticket that failed once is usually
      bigger/harder than first sized. After the first failure, re-run the
      per-issue estimator (or bump the size) so the budget/forecast and
      model-routing reflect reality on the retry, rather than reusing the stale
      pre-failure estimate.

### Bulk create attachments — durable storage (deferred)

Fix A (sync bare-cache ref before push) and B (surface `attachment_warning` in UI)
shipped 2026-06-15. Remaining hardening:

- [ ] **Persist upload bytes under `.commander/bulk-jobs/{job_id}/files/`** instead of
      temp-only dirs (`tempfile.mkdtemp`) that vanish on server restart. Today a
      restart between draft and post leaves `has_attachments` true but no bytes on
      disk, so pre-commit retries return an empty `image_url_map` with no recovery
      path except re-uploading.

### Pending — model/UI fixes (start AFTER sprint-67.1 finishes; one PR)

Agreed 2026-06-13; do not start while 67.1 is running.

- [ ] **1. Model = single source.** Global Settings Save (`PUT /api/settings`)
      writes only the settings DB; the dispatcher reads sprint.yaml
      `agent_config.default_model` — so Save never applies (coder/estimator
      stayed opus). Wire Save to also write sprint.yaml `agent_config` (via
      `settings_sync._update_sprint_yaml_agent_config`) so sprint.yaml is the
      single source the run reads. Default may stay opus; the sonnet override
      must stick.
- [ ] **2. Move-ticket list ← GitHub only.** Both move UIs list every base
      sprint number from `_smgmtData.sprints` (shows old 59/65/66, base labels,
      no lock). Source the list from **GitHub** (single source), filtered to
      active/current sprints incl sub-labels (66.6/67.1), and **disable the
      running** sprint.
- [ ] **3. Analytics size calibration (S/M/L) by TIME; tokens → "N/A (soon)".**
      Size-vs-actual must key off the **size label** using **time per size**
      (avg minutes from agent_runs; recorded even at 0 tokens). Grey out the
      token column as **"N/A (soon)"** (tokens aren't tracked on the
      subscription). Surface calibrated time-per-size in project settings too.
- [ ] **4. Status pill ← `/api/status`.** The nav pill uses
      `/api/sprint-nav-status` (GitHub-label, base-number heuristic → showed
      "S71 0/11" while 67.1 ran). Make the pill prefer the live running sprint
      from `/api/status` (sub-label aware, real progress), GitHub heuristic only
      when nothing runs. One source for "what's running."

<!-- Anything above this line is hand-maintained. -->

<!-- AUTO:milestones START -->
<!-- The documentor manages everything between these markers. Do not edit by hand. -->

_No milestones recorded yet. The first entry appears after the next sprint finishes._

<!-- AUTO:milestones END -->
