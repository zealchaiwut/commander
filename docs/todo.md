# TODO (formerly milestones.md)

> **Active milestone tracking lives in [`milestones/`](milestones/).**
> Current focus:
> [`milestones/sprint-lifecycle-redesign.md`](milestones/sprint-lifecycle-redesign.md)
> — the prioritized fix list (P0–P4) for the sprint lifecycle redesign. Its
> design contract is
> [`architecture/sprint-lifecycle.md`](architecture/sprint-lifecycle.md);
> reread that doc before picking up items or re-opening the design discussion.

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

- [ ] **Phase 1 — pluggable coder backend:** `COMMANDER_CODER_BACKEND=cline|claude`
      (default `claude`); update `_dispatch_coder()`, `_dispatch_doctor()`, and
      `_doctor_probe_auth()` in `services/sprint_manager/sprint_manager.py`
- [ ] **Phase 2 — Cline worktree setup:** `cline auth` in `~/dev/commander/coder`,
      replicate MCP (codedb/github/sqlite), add `.clinerules` from
      `.claude/agents/coder.md`
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

<!-- Anything above this line is hand-maintained. -->

<!-- AUTO:milestones START -->
<!-- The documentor manages everything between these markers. Do not edit by hand. -->

_No milestones recorded yet. The first entry appears after the next sprint finishes._

<!-- AUTO:milestones END -->
