# Milestones

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

<!-- Anything above this line is hand-maintained. -->

<!-- AUTO:milestones START -->
<!-- The documentor manages everything between these markers. Do not edit by hand. -->

_No milestones recorded yet. The first entry appears after the next sprint finishes._

<!-- AUTO:milestones END -->
