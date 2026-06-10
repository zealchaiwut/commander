"""Commander configuration constants.

Values can be overridden via environment variables.
"""
import os

# Sandbox GitHub repo used for all tester-side GitHub operations in test mode.
# Override with COMMANDER_TEST_REPO env var.
# Falls back to GITHUB_ISSUE_TEST_REPO (the canonical env var for the throwaway
# issue/label test repo).  If neither is set, no default is assumed — callers
# must handle the empty-string case explicitly.
TEST_GITHUB_REPO: str = (
    os.environ.get("COMMANDER_TEST_REPO", "")
    or os.environ.get("GITHUB_ISSUE_TEST_REPO", "")
)

# Issues-mirror sync cadence (issue #756). The dashboard reads issue state from
# the local DB mirror, refreshed every SYNC_INTERVAL_SECONDS via ETag-conditional
# polling. Default is 60 s; override per-machine in apps/dashboard/.env.
# The authoritative resolver lives in github_events_sync.get_sync_interval().
SYNC_INTERVAL_SECONDS: int = int(os.environ.get("SYNC_INTERVAL_SECONDS", "60") or "60")
