# Coder backends (Claude Code vs Cline)

Design note for swapping the **coder dispatch** to an alternate agent harness
while leaving the rest of the sprint pipeline on Claude Code.

See also: [Sprint Manager](sprint-manager.md).

---

## Goal

Run overnight sprints with:

| Role | Tool | Funding |
|------|------|---------|
| **Coder** | Cline CLI (headless) | Company Cline subscription (Sonnet) |
| **Tester, BA, documentor, reviewer, estimator** | Claude Code CLI | Claude.ai subscription |
| **Human pairing** | Cursor (Composer 2.5 default; Opus for hard problems) | Cursor subscription |

The sprint manager only needs the coder subprocess to exit 0 and leave a
pushed `feature/<N>-*` branch. Everything after that is unchanged.

---

## What stays the same

When only the coder backend changes, these paths are untouched:

- Worktree hygiene (`_worktree_hygiene`) before dispatch
- Sprint state machine and label transitions (`in-progress` → `SIT` → tester → gates → `UAT`)
- Tester, documenter, reviewer, BA, estimator — still `claude -p`
- Post-coder validation: exit code 0 **and** `_find_feature_branch(N)` must succeed
- Fix loops, hang detection (`HangDetector`), rate-limit retries
- Quality gates after tester (pytest, lint, merge-preview, design, monolith)

---

## Integration point

All coder subprocess spawning lives in `_dispatch_coder()` in
`services/sprint_manager/sprint_manager.py`.

Backend selection (issues #916–#920, landed on `develop`):

- `sprint.yaml` → `agent_config.coder.backend` (`claude-code` default, or `cline`)
- `use_cline_followups` + `follow-up` label → routes to Cline when `.clinerules` exists
- Fix-loop escalation: Cline failure → retry on `claude-code` (`_next_coder_backend`)

Cline headless dispatch:

```bash
cline -y -m <model> "<persona + prompt>"
```

`-y` skips tool-approval prompts (analogous to
`--dangerously-skip-permissions`). Persona from `.claude/agents/coder.md` is
prepended to the prompt (Cline has no `--append-system-prompt`).

---

## Code changes required

| Location | Change |
|----------|--------|
| `_dispatch_coder()` | Build `cmd` from resolved `coder_backend` |
| `_dispatch_doctor()` | Probe `cline` or `claude` on PATH per backend |
| `_doctor_probe_auth()` | Split by backend (`cline --version` vs `claude --version`) |
| `SprintConfig` | `coder_backend`, `use_cline_followups` from sprint.yaml |
| FileNotFound handler | Report missing `cline` or `claude` by backend |

---

## Gaps and mitigations

### Dashboard telemetry

Hooks in `hooks/post_tool_used.py` and `hooks/tool_used.py` are Claude Code–specific.
Cline coder runs will not populate live agent events or `token_usage` unless:

- Cline hooks are added that POST to the same dashboard endpoints, or
- `cline --json` output is parsed after dispatch and forwarded to `/api/agent-event`

Sprint execution works without this; visibility for the coder step is reduced.

### MCP servers

Claude Code sessions use codedb, github, and sqlite MCP (see `CLAUDE.md`).
Replicate the same MCP config in Cline for the `coder/` worktree, or the agent
falls back to shell (`gh`, grep) — slower and less reliable.

### Auth surfaces

Two subscriptions coexist:

- **Claude Code** — tester, BA, and other dispatches (`cline auth` not involved)
- **Cline** — coder only (`cline auth` with company subscription)

Pre-dispatch doctor checks must validate the backend that will actually run.

### Behavior tuning

Same prompt, different harness. Before trusting overnight sprints, manually verify
2–3 tickets:

- Runs `scripts/start_feature.py` reliably
- Pushes feature branch
- Does not merge or mutate GitHub labels (sprint_manager owns labels since #509)

---

## Recommended rollout

**Phase 1 — sprint.yaml backend flag** ✅ landed (#916–#920)

- `agent_config.coder.backend: cline|claude-code` in sprint.yaml
- `_dispatch_coder`, `_dispatch_doctor`, `_doctor_probe_auth` backend-aware
- Follow-up label routing + fix-loop escalation to claude-code

**Phase 2 — Cline setup in coder worktree** — use the setup script:

```bash
cd ~/dev/commander/uat    # any clone with scripts/
bash scripts/setup_cline.sh
cd ~/dev/commander/coder
cline auth                    # company subscription (interactive)
# OR metered API: export ANTHROPIC_API_KEY=sk-ant-...  — pick one, not both

# Enable (safer first):
bash scripts/setup_cline.sh --enable-followups
# OR full switch:
# bash scripts/setup_cline.sh --enable-always
```

Remote Mac mini: run the same script after `setup_machine.sh` on that host.
See [machine-onboarding.md](../machine-onboarding.md#cline-coder-backend-migration).

Manual checklist (if not using the script):

```bash
cd ~/dev/commander/coder
cline auth                    # company subscription
# replicate MCP config from Claude Code (optional — falls back to gh/grep)
# .clinerules ships in repo (issue #916)
```

**Phase 3 — telemetry (optional)**

- Parse `cline --json` into existing dashboard APIs, or accept blind coder runs

**Sanity check before wiring in:**

```bash
cd ~/dev/commander/coder
cline -y -m claude-sonnet-4-6 \
  "Read CLAUDE.md and .claude/agents/coder.md. Dry-run only: explain the coder workflow for issue #123 without making changes."
```

---

## Operator tool split (reference)

| Task | Tool |
|------|------|
| Interactive development, UAT review, dashboard edits | Cursor on `uat/` or `prd/` (Composer 2.5; Opus when stuck) |
| Overnight sprint coder | Cline CLI in `coder/` (when sprint.yaml backend is `cline`) |
| Overnight sprint tester + BA | Claude Code in `tester/` / dashboard |
| Manual spikes on other repos | Cline VS Code extension or CLI |

Do not edit the `coder/` worktree while an overnight sprint is running — agents
and humans will conflict on the same worktree.

---

## Status

**Phase 1 implemented** on `develop` (issues #916–#920). Phase 2 (worktree MCP +
`.clinerules`) and Phase 3 (telemetry) remain optional follow-ups — see
[todo.md](../todo.md#todo).
