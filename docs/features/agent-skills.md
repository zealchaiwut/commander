# Agent skills — caveman & code-review-graph

Installed 2026-06-12 in `uat`, `coder`, `tester`, and `prd` clones.

See also: [MCP servers in CLAUDE.md](../../CLAUDE.md#mcp-servers-available-in-all-sessions).

---

## What is installed

| Component | Location | Clones |
|-----------|----------|--------|
| **caveman** skill | `.claude/skills/caveman/SKILL.md` + `.agents/skills/caveman/` (uat) | uat, coder, tester, prd |
| **code-review-graph** CLI | `uat/venv/bin/code-review-graph` | shared via uat venv |
| **CRG MCP** | `.mcp.json` per clone | uat, coder, tester, prd |
| **CRG skills** | `.claude/skills/{explore-codebase,review-changes,debug-issue,refactor-safely}/` | uat, coder, tester, prd |
| **CRG graph DB** | `.code-review-graph/` (gitignored) | built per clone |
| **Agent wiring** | `.claude/agents/coder.md`, `tester.md` § Sprint skills | uat → replicated |

Source: [mattpocock/skills — caveman](https://www.skills.sh/mattpocock/skills/caveman),
[tirth8205/code-review-graph](https://github.com/tirth8205/code-review-graph).

**codedb** (existing, user-scope MCP) is unchanged — use CRG first, codedb second.

---

## How to apply in the loop (skills do NOT auto-trigger)

Neither skill runs automatically during overnight sprints. Headless `claude -p`
does not load slash skills — only the agent persona (`--append-system-prompt`
from `.claude/agents/*.md`) and MCP tools apply.

### Overnight sprint (Coder → Tester)

Already wired in agent personas:

| Stage | caveman | code-review-graph |
|-------|---------|-------------------|
| **Coder** | `caveman lite` for one-line status only | `get_minimal_context` → `semantic_search_nodes` / `query_graph` before coding |
| **Tester** | `caveman lite` for progress + report headers | `detect_changes` / `get_review_context` on feature branch before full file reads |
| **BA** | Off — tickets stay human-readable | Optional |

No dashboard change needed. Restart not required for persona edits on next dispatch.

### Interactive Claude Code (you at the desk)

| Want | Do |
|------|-----|
| Terse replies | Say **"caveman lite"** or **"/caveman"** (skill in `.claude/skills/`) |
| Stop terse | **"stop caveman"** or **"normal mode"** |
| Graph review | **"/review-changes"** or ask: *"Use detect_changes on this branch"* |
| Explore structure | **"/explore-codebase"** or *"get_architecture_overview"* |
| Rebuild graph after big merge | `uat/venv/bin/code-review-graph build` in that clone |

Restart Claude Code after first MCP install so `.mcp.json` loads.

### Cursor (you pairing)

| Want | Do |
|------|-----|
| caveman | Type **"use caveman"** or **"less tokens"** in chat (skill at `.agents/skills/caveman`) |
| CRG tools | Restart Cursor once; MCP at `.cursor/mcp.json`. Hooks at `~/.cursor/hooks.json` auto-update graph on save |
| Stale graph | `cd ~/dev/commander/uat && ./venv/bin/code-review-graph build` |

### Manual one-liners (any terminal)

```bash
# Rebuild graph in a clone
cd ~/dev/commander/uat && ./venv/bin/code-review-graph build

# Token savings panel for current diff
./venv/bin/code-review-graph detect-changes --brief

# Background freshness (Cursor / no Claude hooks)
crg-daemon add ~/dev/commander/uat --alias commander-uat
crg-daemon start
```

---

## If agents ignore CRG or caveman

1. **Check MCP** — in Claude Code: `/mcp` should list `code-review-graph`. If missing, restart the tool.
2. **Check graph exists** — `ls .code-review-graph/` in the worktree; if empty, run `build`.
3. **Headless sprint** — skills won't fire; behavior comes from **coder.md / tester.md** only. Re-read § Sprint skills in those files.
4. **Force in prompt** — when running `/coder` manually, prefix: *"caveman lite for status. CRG get_minimal_context first."*
5. **Sprint manager** — optional future: append the same one-liner to `_dispatch_coder()` prompt in `sprint_manager.py` (not done yet).

---

## When you need to reinstall

| Situation | Reinstall? |
|-----------|------------|
| Open Claude Code again **same machine, same clone** | **No** — skills + `.mcp.json` stay on disk |
| Large codebase change | **No** — CRG hooks/`build` incrementally; run `build` only if graph feels stale |
| Recreated venv (`pip install -r requirements.txt`) | **No** for CRG CLI — pinned in `requirements.txt`; run `--resetup-machine` for MCP/skills/graph |
| Fresh `git clone` or new worktree | **Yes** — run `bash scripts/setup_machine.sh --resetup-machine` after venv |
| New Mac / new machine | **Yes** — full `bash scripts/setup_machine.sh` (includes agent skills) |
| Same Mac mini, skills feel stale | **Yes** — `bash scripts/setup_machine.sh --resetup-machine` |

Track optional vendoring in milestones TODO.

---

## Maintenance

```bash
# New machine (from any clone, e.g. prd)
bash scripts/setup_machine.sh

# Second Mac mini / refresh skills + CRG graphs on all clones
bash scripts/setup_machine.sh --resetup-machine

# Single clone only
bash scripts/install_agent_skills.sh --clone uat --force
```

```bash
# Legacy manual update
cd ~/dev/commander/uat
npx skills add https://github.com/mattpocock/skills --skill caveman -y
/bin/cp -f .agents/skills/caveman/SKILL.md .claude/skills/caveman/SKILL.md
# replicate to coder/tester/prd with /bin/cp -f (macOS cp alias may prompt)

# Update CRG
./venv/bin/pip install -U code-review-graph
for c in uat coder tester prd; do
  cd ~/dev/commander/$c && ../uat/venv/bin/code-review-graph install --platform claude-code
  ../uat/venv/bin/code-review-graph build
done
```

Use **`/bin/cp -f`**, not `cp -f`, on macOS if `cp` is aliased to interactive mode.

---

## Role defaults

| Role | caveman | code-review-graph |
|------|---------|-------------------|
| BA | Off | Optional |
| Coder | lite (status only) | explore + query tools |
| Tester | lite (report skeleton) | detect_changes + review context |
| Human / Cursor | On demand | On demand |
