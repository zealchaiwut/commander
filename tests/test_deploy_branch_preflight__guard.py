"""Deploy branch preflight — fail clearly when the clone is on the wrong branch.

Root cause of the "Deploy failed (500): Not possible to fast-forward" report:
deploy runs ``git pull --ff-only origin <branch>`` but is pull-only and never
switches branches. When the clone was left on a feature branch, the pull tried
to fast-forward that branch to <branch> and aborted with a cryptic git error
surfaced as a raw 500. The preflight detects the mismatch and returns an
actionable 409 instead.
"""
from pathlib import Path

from services.sprint_manager import deploy_actions as da

_SERVER = Path(__file__).resolve().parents[1] / "apps" / "dashboard" / "server.py"


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
    # Actionable: names both branches and the exact fix command.
    assert "fix/history-fold-chips-move-menu" in msg
    assert "develop" in msg
    assert "cd /home/u/dev/commander/uat && git checkout develop" in msg


def test_detached_head_is_a_mismatch():
    # `git rev-parse --abbrev-ref HEAD` prints "HEAD" when detached.
    assert da.branch_mismatch_error("HEAD", "develop", "/x") is not None


def test_trailing_newline_tolerated():
    # subprocess stdout carries a trailing newline — must still match.
    assert da.branch_mismatch_error("develop\n", "develop", "/x") is None


def test_blank_inputs_skip_the_check():
    # Undeterminable branch must never block a deploy.
    assert da.branch_mismatch_error("", "develop", "/x") is None
    assert da.branch_mismatch_error("develop", "", "/x") is None


def test_endpoint_wires_preflight_before_pull():
    """The deploy endpoint must run the branch check and raise 409 BEFORE the
    pull, so the mismatch never reaches the cryptic git 500."""
    src = _SERVER.read_text(encoding="utf-8")
    fn = src[src.index("def deploy_environment("):]
    fn = fn[: fn.index("\n@app.", 1)]
    pre = fn.index("build_current_branch_command()")
    pull = fn.index("build_pull_command(branch)")
    assert pre < pull, "branch preflight must run before the pull"
    assert "status_code=409" in fn[pre:pull]
