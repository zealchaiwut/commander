"""Commander configuration constants.

Values can be overridden via environment variables.
"""
import os

# Sandbox GitHub repo used for all tester-side GitHub operations in test mode.
# Override with COMMANDER_TEST_REPO env var.
TEST_GITHUB_REPO: str = os.environ.get(
    "COMMANDER_TEST_REPO", "zealchaiwut/commander-test-issue"
)

# Real production repo — never targeted in test mode.
PRODUCTION_REPO: str = "zealchaiwut/commander"
