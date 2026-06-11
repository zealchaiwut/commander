"""AC3: the seven checks are performed in the exact order specified."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import doctor  # noqa: E402

EXPECTED_ORDER = [
    "claude",       # claude CLI on PATH + authenticates
    "gh",           # gh on PATH + authenticates
    "git identity", # git user.name + user.email
    "venv",         # venv present + packages importable
    "DB_PATH",      # DB_PATH writable
    "plist PATH",   # launchd plist PATH includes claude/gh dirs
    "headless",     # headless auth tokens in plist
]


def test_seven_checks_in_order():
    results = doctor.run_all_checks()
    names = [r.name.lower() for r in results]
    assert len(names) == 7, f"expected 7 checks, got {len(names)}: {names}"
    for keyword, name in zip(EXPECTED_ORDER, names):
        assert keyword.lower() in name, (
            f"check out of order: expected a check matching '{keyword}', got '{name}'"
        )


def test_check_callables_match_order():
    # The orchestration list is the single source of order truth.
    assert [c.__name__ for c in doctor.CHECKS] == [
        "check_claude_cli",
        "check_gh_cli",
        "check_git_identity",
        "check_venv_packages",
        "check_db_path_writable",
        "check_plist_path",
        "check_headless_tokens",
    ]
