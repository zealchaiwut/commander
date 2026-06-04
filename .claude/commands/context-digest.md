---
description: Generate a context digest markdown for starting a new Claude Code session. Usage: /context-digest
---

Generate a context digest for this project by running the following command:

```bash
python3 "$(git rev-parse --show-toplevel)/scripts/generate_context_digest.py"
```

The script will:
1. Read `CLAUDE.md` to extract project goals (if present)
2. Capture the current working directory and active git branch
3. Query GitHub for open in-progress issues (active work)
4. Read session memory files from `~/.claude/projects/<encoded-cwd>/memory/` (recent decisions)
5. Write a self-contained markdown digest to `.claude/context-digest.md`
6. Print the output path to stdout

After the script completes:
- Show the user the generated file path
- Offer to display the digest contents inline

If the script fails for any reason, fall back to generating the digest manually:
- Run `git rev-parse --abbrev-ref HEAD` for the active branch
- Read `CLAUDE.md` if it exists
- List in-progress GitHub issues with `gh issue list --label in-progress --state open`
- Write the digest to `.claude/context-digest.md` following the structure below

## Digest structure

```markdown
> **How to Use:** Paste this digest at the start of a new Claude Code session to restore project context immediately.

_Generated: <timestamp>_

## Project State

- **Working directory:** `<cwd>`
- **Active branch:** `<branch>`
- **Goals:**
  - <bullet from CLAUDE.md>

## Active Work

- #<N> <title>

## Recent Decisions

### <memory-name>
_<description>_

<body content>
```
