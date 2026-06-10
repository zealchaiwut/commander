"""Tests for issue #694: Rename _pr_err to pr_err in sprint_branch_status error logging.

This is a backend internal-logging variable rename inside
get_sprint_branch_status() in server.py. It has no HTTP-observable surface
(the renamed variable only appears in an exception log path), so the single
acceptance criterion is verified by static source inspection of the exact
function body rather than an HTTP round-trip.
"""
import ast
import os

SERVER_PY = os.path.join(os.path.dirname(__file__), "..", "server.py")


def _func_source(func_name: str) -> str:
    with open(SERVER_PY, "r") as f:
        src = f.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"function {func_name!r} not found in server.py")


# --- Acceptance Criteria ---

def test_rename_pr_err__pr_err_used_underscore_gone():
    # AC: exception var renamed from `_pr_err` to `pr_err` in
    # get_sprint_branch_status error logging; no `_pr_err` remains there.
    body = _func_source("get_sprint_branch_status")
    assert "except Exception as pr_err:" in body, (
        "expected `except Exception as pr_err:` in get_sprint_branch_status"
    )
    assert "_pr_err" not in body, (
        "stale `_pr_err` still present in get_sprint_branch_status"
    )
    # the variable is actually consumed in the log message + error= kwarg
    assert "{pr_err}" in body and "str(pr_err)" in body, (
        "renamed var must still be used in the log message and error= kwarg"
    )
