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
`services/sprint_manager/sprint_manager.py` (~lines 3866–3917).

Today:

```python
cmd = [
    "claude",
    "--model", coder_model,
    "--dangerously-skip-permissions",
]
coder_persona = _load_agent_persona("coder", cwd_path)
if coder_persona:
    cmd += ["--append-system-prompt", coder_persona]
cmd += ["-p", prompt]
```

Proposed env flag (not implemented yet):

```bash
COMMANDER_CODER_BACKEND=cline   # default: claude
```

Cline equivalent (headless, auto-approve):

```bash
cline -y -m claude-sonnet-4-6 "<persona + prompt>"
```

`-y` / `--yolo` skips tool-approval prompts (analogous to
`--dangerously-skip-permissions`). The process exits when the task completes.

Persona from `.claude/agents/coder.md` must be **prepended to the prompt**
(Cline has no `--append-system-prompt`), or mirrored in Cline project rules
(`.clinerules` in the coder worktree).

---

## Code changes required

| Location | Change |
|----------|--------|
| `_dispatch_coder()` | Build `cmd` from `COMMANDER_CODER_BACKEND` |
| `_dispatch_doctor()` | When backend is `cline`, probe `cline` on PATH + auth; do not require `claude` for coder-only doctor path |
| `_doctor_probe_auth()` | Split by role/backend (coder = Cline, tester = Claude) |
| FileNotFound handler in `_dispatch_coder()` | Report missing `cline` CLI when backend is Cline |

Default remains `claude` so existing sprints need no config change.

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

**Phase 1 — flag + minimal code**

- Add `COMMANDER_CODER_BACKEND=cline|claude` (default `claude`)
- Touch only `_dispatch_coder`, `_dispatch_doctor`, `_doctor_probe_auth`

**Phase 2 — Cline setup in coder worktree**

```bash
cd ~/dev/commander/coder
cline auth                    # company subscription
# replicate MCP config from Claude Code
# add .clinerules mirroring .claude/agents/coder.md workflow
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
| Overnight sprint coder | Cline CLI in `coder/` (when implemented) |
| Overnight sprint tester + BA | Claude Code in `tester/` / dashboard |
| Manual spikes on other repos | Cline VS Code extension or CLI |

Do not edit the `coder/` worktree while an overnight sprint is running — agents
and humans will conflict on the same worktree.

---

## Status

**Not implemented.** Documented 2026-06-12 from architecture discussion. Track
implementation tasks in [milestones.md](../milestones.md#todo).
