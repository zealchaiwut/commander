"""Issue #694 — rename `_pr_err` to `pr_err` in get_sprint_branch_status error logging.

AC: the exception variable in the PR-lookup except block must be named `pr_err`
(no leading underscore), since it is actually used in the log message. The
leading-underscore form `_pr_err` (Python convention for *unused* variables) is
misleading and must not appear.

The check reads the source of `get_sprint_branch_status` directly from
server.py (no import) to avoid module import side-effects.
"""
import ast
from pathlib import Path

_SERVER = Path(__file__).resolve().parents[1] / "apps" / "dashboard" / "server.py"


def _func_source():
    src = _SERVER.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_sprint_branch_status":
            return ast.get_source_segment(src, node)
    raise AssertionError("get_sprint_branch_status not found in server.py")


def test_no_leading_underscore_pr_err():
    """`_pr_err` must not appear anywhere in the function."""
    assert "_pr_err" not in _func_source()


def test_uses_pr_err_in_except_and_log():
    """Exception is bound as `pr_err` and used in the warn message + error kwarg."""
    src = _func_source()
    assert "except Exception as pr_err:" in src
    assert "failed: {pr_err}" in src
    assert "error=str(pr_err)" in src
