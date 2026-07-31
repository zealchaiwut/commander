---
description: Capture a decision as a dated ADR entry in docs/decisions/. Usage: /decide <slug> [context] [options] [decision] [consequences] [implemented-by]
argument-hint: <slug> "<context>" "<options>" "<decision>" "<consequences>" "<implemented-by>"
---

Capture a decision reached in this session as a dated Architecture Decision
Record (ADR) in `docs/decisions/`.

## Arguments

The user invoked: `/decide $ARGUMENTS`

Parse `$ARGUMENTS` as positional fields (all after the first are optional):

1. **SLUG** (required) — short kebab-case label, e.g. `delete-planned-state`
2. **CONTEXT** — what situation prompted the decision
3. **OPTIONS** — options considered, e.g. "A do X; B do Y"
4. **DECISION** — which option was chosen and why
5. **CONSEQUENCES** — what changes as a result; follow-up work
6. **IMPLEMENTED-BY** — GitHub issue number(s), e.g. "#1686"

If only a slug is given and the other fields are missing, read the recent
conversation to infer them, then call the script.

## Run

From the repository root, call:

```bash
python3 scripts/log_decision.py \
  --slug       "<SLUG>" \
  --context    "<CONTEXT>" \
  --options    "<OPTIONS>" \
  --decision   "<DECISION>" \
  --consequences "<CONSEQUENCES>" \
  --implemented-by "<IMPLEMENTED-BY>"
```

The script:
- Computes today's BKK date and the next sequence number N automatically.
- Creates `docs/decisions/YYYY-MM-DD-N-<slug>.md` with all five sections.
- Updates `docs/decisions/README.md` (the index) with a link to the new entry.
- Prints the path of the created file.

## After running

Report back:
- The file path created.
- The full filename (naming scheme: `YYYY-MM-DD-N-<slug>.md`).
- Suggest committing: `git add docs/decisions/ && git commit -m "docs: add ADR <slug>"`
