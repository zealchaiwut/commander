# `.commander/` — Sprint Manager Configuration

This directory holds **user-specific** sprint manager config. The `sprint.yaml.example` template is checked in; the real `sprint.yaml` (with absolute paths to your local clones) is gitignored.

## First-time setup

After cloning the repo:

```bash
./.commander/setup.sh
```

This creates `.commander/sprint.yaml` from the template, substituting your `$HOME` for `/Users/USER`. Open the resulting file and verify paths match your local layout.

## Why is this needed?

Sprint manager auto-discovers `.commander/sprint.yaml` by walking up from `cwd`. Without it, sprint manager falls back to stale default paths (`~/commander/work-coder`, `~/commander/work-tester`) that no longer exist after the repo restructure (#52). When those defaults are missing, `subprocess.Popen` raises `FileNotFoundError` on `cwd`, which sprint manager wrongly reports as "claude CLI not found — stub success". The result is a silent stub run that pretends to succeed but does no real work.

## Files in this directory

- `sprint.yaml.example` — template (committed)
- `setup.sh` — helper that creates `sprint.yaml` from the template (committed)
- `README.md` — this file (committed)
- `sprint.yaml` — your actual config (gitignored)
- `logs/`, `sprints/`, `alerts/`, `runtime/` — runtime artifacts (gitignored)
