# Sandbox Repo for Tester Isolation

Commander's tester and coder agents perform real GitHub operations (create issues,
apply labels, post comments, merge branches). To prevent test runs from polluting
the real `zealchaiwut/commander` backlog, all test-mode operations target a
dedicated sandbox repository: **`zealchaiwut/commander-issue-test`**.

## One-time Setup

1. Create the sandbox repo on GitHub (empty, no README needed):
   ```bash
   gh repo create zealchaiwut/commander-issue-test --public --description "Commander test sandbox"
   ```

2. Verify it exists:
   ```bash
   gh repo view zealchaiwut/commander-issue-test
   ```

3. Seed with initial fixture data:
   ```bash
   python3 scripts/seed_test_issues.py --wipe
   ```

That's it. No code changes needed — the sandbox is used automatically in test mode.

## How Sandbox Routing Works

Two mechanisms activate sandbox routing (both handled by `github_client.get_repo_for_operation()`):

| Trigger | Behavior |
|---|---|
| `COMMANDER_TEST_MODE=1` env var | All GitHub operations redirect to `TEST_GITHUB_REPO` |
| Operation targets `zealchaiwut/commander` directly | Self-referential detection — redirects automatically |

Override the sandbox target:
```bash
export COMMANDER_TEST_REPO=yourorg/your-sandbox
```

## Seeding with `seed_test_issues.py`

The seed script reads `scripts/test_fixtures/issues.yaml` and creates mock issues on the sandbox.

```bash
# Default — idempotent, creates only missing issues
python3 scripts/seed_test_issues.py

# Wipe all open issues and seed fresh (full reset)
python3 scripts/seed_test_issues.py --wipe

# Preview what would happen without making API calls
python3 scripts/seed_test_issues.py --wipe --dry-run

# Seed and create matching feature branches
python3 scripts/seed_test_issues.py --wipe --include-branches

# Add issues on top of existing ones (may duplicate)
python3 scripts/seed_test_issues.py --append
```

### Flags

| Flag | Description |
|---|---|
| `--wipe` | Close all open issues on sandbox, then seed fresh |
| `--append` | Create all fixture issues, ignoring existing ones |
| `--hard-delete` | Alias for `--wipe` (GitHub API cannot delete issues) |
| `--include-branches` | Create a `feature/<N>-<slug>` branch for each issue |
| `--dry-run` | Print planned operations; make no API calls |
| `--repo owner/repo` | Override target repo (default: `COMMANDER_TEST_REPO`) |

## Resetting Between Test Runs

For a clean state before a test suite:

```bash
# Quick reset — close all and reseed
python3 scripts/seed_test_issues.py --wipe

# Full reset with branches
python3 scripts/seed_test_issues.py --wipe --include-branches
```

For CI pipelines, add a reset step before any tests that read GitHub state:

```yaml
- name: Reset sandbox
  run: python3 scripts/seed_test_issues.py --wipe
  env:
    COMMANDER_TEST_REPO: zealchaiwut/commander-issue-test
```

## Fixture File

The fixture is `scripts/test_fixtures/issues.yaml`. It contains 15 mock issues
covering all label states (backlog, in-progress, SIT, UAT, UAT-approved, released),
all sprint labels (sprint-1 through sprint-3), size labels (S/M/L), and edge cases
(no labels, conflicting labels).

To add new fixture scenarios, append entries to the `issues` list. Each entry
requires `title` and `body`; `labels`, `state`, and `comments` are optional.
The `title` is used as the idempotency key on default (no-flag) runs.

## Verifying Isolation

Run the integration test to confirm no operations target the real repo:

```bash
COMMANDER_TEST_MODE=1 pytest tests/integration/test_sandbox_isolation.py -v
```

All tests should pass and confirm that `get_repo_for_operation()` returns
`commander-issue-test`, not `commander`.
