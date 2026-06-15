"""Deploy branch sync — checkout configured branch before pull.

When the clone was left on a feature branch, deploy now runs
``git checkout <branch>`` before ``git pull --ff-only`` (same as
``scripts/sync_uat.sh``). Checkout failure (e.g. dirty worktree) returns 409.
"""
from pathlib import Path

from services.sprint_manager import deploy_actions as da

_SERVER = Path(__file__).resolve().parents[1] / "apps" / "dashboard" / "server.py"


def test_build_checkout_command():
    assert da.build_checkout_command("develop") == ["git", "checkout", "develop"]


def test_current_branch_command():
    assert da.build_current_branch_command() == [
        "git", "rev-parse", "--abbrev-ref", "HEAD",
    ]


def test_no_error_when_branch_matches():
    assert da.branch_mismatch_error("develop", "develop", "/x/uat") is None


def test_error_when_on_feature_branch():
    msg = da.branch_mismatch_error(
        "fix/history-fold-chips-move-menu", "develop", "/home/u/dev/commander/uat"
    )
    assert msg is not None
    assert "fix/history-fold-chips-move-menu" in msg
    assert "develop" in msg


def test_detached_head_is_a_mismatch():
    assert da.branch_mismatch_error("HEAD", "develop", "/x") is not None


def test_endpoint_wires_checkout_before_sync():
    """Deploy must checkout the configured branch before fetch + reset."""
    src = _SERVER.read_text(encoding="utf-8")
    fn = src[src.index("def deploy_environment("):]
    fn = fn[: fn.index("\n@app.", 1)]
    checkout = fn.index("build_checkout_command(branch)")
    fetch = fn.index("build_fetch_command(branch)")
    reset = fn.index("build_reset_hard_command(branch)")
    assert checkout < fetch < reset, "git checkout → fetch → reset --hard"
