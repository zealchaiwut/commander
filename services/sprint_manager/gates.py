"""pytest/lint gate functions for the sprint manager.

Contains: _gate_pytest, _gate_lint, _lint_autofix_commit, _run_frontend_lint,
_changed_py_files, _changed_js_ts_files, _changed_frontend_files, and their
supporting constants — extracted from sprint_manager.py (issue #1280).

sprint_manager.py re-imports and re-exports all symbols so existing call sites
remain unmodified.
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from services.logging import log as structured_log
from services.sprint_manager.events import _post_agent_event
from services.sprint_manager.state import GateResult

# Same calculation as in sprint_manager.py: this file lives at
# services/sprint_manager/gates.py, so repo root is three levels up.
REPO_ROOT = Path(__file__).parent.parent.parent


# ── subprocess helpers ────────────────────────────────────────────────────────
# These forward to sprint_manager._run_timed / _try at call time so that
# existing tests patching "sprint_manager._run_timed" still work after the
# extraction. When no patch is active the forwarding resolves to the original
# subprocess wrapper in sprint_manager; when a patch is active it resolves to
# the mock. Direct circular import is avoided because the lookup is deferred.

def _run_timed(*cmd, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    _f = _lookup_in_sm("_run_timed", _run_timed)
    if _f is not None:
        return _f(*cmd, cwd=cwd)
    cwd_arg = str(cwd) if cwd is not None else None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd_arg)
    except FileNotFoundError as exc:
        return 1, "", str(exc)
    return r.returncode, r.stdout, r.stderr


def _try(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    _f = _lookup_in_sm("_try", _try)
    if _f is not None:
        return _f(*cmd, cwd=cwd)
    cwd_arg = str(cwd) if cwd is not None else None
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd_arg)
    except FileNotFoundError as exc:
        return False, "", str(exc)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


# ── _revert_to_sit proxy ──────────────────────────────────────────────────────
# _revert_to_sit has deep dependencies (github_client, record_failure, etc.)
# that are not yet extracted. A sys.modules lookup at call time avoids a
# circular import AND respects monkeypatching in tests that patch
# "sprint_manager._revert_to_sit" (the same pattern used for _run_timed).

# When True, per-gate _revert_to_sit calls are no-ops: run_quality_gates runs
# ALL gates and aggregates every failure into a single revert/comment/sidecar
# (so one retry fixes all failing gates at once instead of one-per-attempt).
_REVERT_SUPPRESSED = False


def _revert_to_sit(
    issue_num: int,
    gate_name: str,
    output: str,
    repo_name: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> None:
    if _REVERT_SUPPRESSED:
        return
    _f = _lookup_in_sm("_revert_to_sit", _revert_to_sit)
    if _f is not None:
        kw: dict = {"repo_name": repo_name}
        if repo_root is not None:
            kw["repo_root"] = repo_root
        return _f(issue_num, gate_name, output, **kw)
    from services.sprint_manager import sprint_manager as _sm_pkg  # noqa: PLC0415
    return _sm_pkg._revert_to_sit_impl(
        issue_num, gate_name, output,
        repo_name=repo_name, repo_root=repo_root,
    )


# ── sys.modules proxy helper ──────────────────────────────────────────────────
# Tests may import sprint_manager via two different paths in the same pytest
# session: the flat path ("sprint_manager" via sys.path insertion) and the
# package path ("services.sprint_manager.sprint_manager"). Each import creates
# a separate module object in sys.modules. A patch on one alias won't be seen
# by a lookup that only checks the other.  _lookup_in_sm iterates both keys so
# proxies always find the patched version regardless of which path was used.

def _lookup_in_sm(attr: str, local_fn):
    """Return the sprint_manager attribute if it differs from local_fn.

    Checks both "sprint_manager" and "services.sprint_manager.sprint_manager"
    keys so that monkeypatches applied via either import path are found.
    Returns None when the attribute matches local_fn in every reachable module
    (i.e. no patch is active and the caller should use its own implementation).

    After importlib.reload(gates) the module's __dict__ is updated in-place, so
    OLD function objects (still held by sprint_manager via from-imports) see NEW
    function names via __globals__.  Object identity alone then wrongly flags the
    OLD sprint_manager copy as an active patch, causing infinite recursion.  The
    fix: treat two callables as "same function" when they share the same code
    origin (co_qualname + co_filename + co_firstlineno), regardless of whether
    they are the same object.  A genuine monkeypatch always has a different
    qualname (MagicMock, lambda, nested helper), so this check stays tight.
    """
    import types as _types  # local import avoids adding to module-level namespace
    _local_code = local_fn.__code__ if isinstance(local_fn, _types.FunctionType) else None
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None:
            _f = getattr(_sm, attr, None)
            if _f is not None and _f is not local_fn:
                # Skip if this is a stale pre-reload copy of the same function
                # (same source definition, different object identity).
                if _local_code is not None and isinstance(_f, _types.FunctionType):
                    _f_code = _f.__code__
                    if (
                        _f_code.co_qualname == _local_code.co_qualname
                        and _f_code.co_filename == _local_code.co_filename
                        and _f_code.co_firstlineno == _local_code.co_firstlineno
                    ):
                        continue  # same definition, not an active patch
                return _f
    return None


# ── module-level constants ────────────────────────────────────────────────────

_JS_TS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")

_JS_TS_LINT_EXCLUDE = ("/dist/", ".map")

# Frontend file extensions the impeccable design detector analyses.
_DESIGN_FE_EXTENSIONS = (".html", ".css", ".jsx", ".tsx")


# ── _changed_* helpers ────────────────────────────────────────────────────────

def _changed_py_files(base_branch: str, cwd: Path) -> list[str]:
    """Return .py files added/modified in HEAD relative to base_branch.

    Uses git diff <base_branch> --name-only --diff-filter=ACM to find files
    that were Added, Copied, or Modified relative to base_branch.
    Returns a list of relative paths (e.g. ['server.py', 'tests/test_foo.py']).
    """
    _f = _lookup_in_sm("_changed_py_files", _changed_py_files)
    if _f is not None:
        return _f(base_branch, cwd)
    rc, out, _ = _run_timed(
        "git", "diff", base_branch, "--name-only", "--diff-filter=ACM",
        cwd=cwd,
    )
    if rc != 0:
        return []
    return [f for f in out.splitlines() if f.endswith(".py")]


def _changed_js_ts_files(base_branch: str, cwd: Path) -> list[str]:
    """Return JS/TS files added/modified in HEAD relative to base_branch.

    Excludes generated build artifacts (dist/ dirs and .map files) that
    should never be linted — ESLint treats explicitly-passed ignored files
    as warnings under --max-warnings=0.
    """
    _f = _lookup_in_sm("_changed_js_ts_files", _changed_js_ts_files)
    if _f is not None:
        return _f(base_branch, cwd)
    rc, out, _ = _run_timed(
        "git", "diff", base_branch, "--name-only", "--diff-filter=ACM",
        cwd=cwd,
    )
    if rc != 0:
        return []
    return [
        f for f in out.splitlines()
        if any(f.endswith(ext) for ext in _JS_TS_EXTENSIONS)
        and not any(pat in f for pat in _JS_TS_LINT_EXCLUDE)
    ]


def _changed_frontend_files(base_branch: str, cwd: Path) -> list[str]:
    """Return frontend files added/modified in HEAD relative to base_branch.

    Scoped to the extensions the impeccable design gate analyses
    (.html/.css/.jsx/.tsx). Returns repo-root-relative paths.
    """
    _f = _lookup_in_sm("_changed_frontend_files", _changed_frontend_files)
    if _f is not None:
        return _f(base_branch, cwd)
    rc, out, _ = _run_timed(
        "git", "diff", base_branch, "--name-only", "--diff-filter=ACM",
        cwd=cwd,
    )
    if rc != 0:
        return []
    return [f for f in out.splitlines() if any(f.endswith(ext) for ext in _DESIGN_FE_EXTENSIONS)]


# ── gate functions ────────────────────────────────────────────────────────────

def _gate_pytest(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
    worktester_root: Optional[Path] = None,
) -> GateResult:
    """Gate 1 — run pytest -x inside the tester worktree dashboard.

    gate_scope='changed' (default): only run test files changed relative to
    base_branch. gate_scope='full': run full pytest suite (legacy behaviour).
    """
    if skip:
        sys.stdout.write(str("  [gate:pytest] skipped") + "\n")
        return GateResult(gate="pytest", passed=True, skipped=True)

    _post_agent_event("gate:pytest")

    # Resolve tester root — used for venv detection and changed-scope cwd.
    # Root is the git repo root (parent of apps/dashboard). Fall back to
    # worktester_dashboard so the logic degrades gracefully on flat layouts.
    wt_root = worktester_root or worktester_dashboard

    # Determine scope / what to run FIRST — before requiring a pytest binary.
    # A changed-scope ticket that touches no test files has nothing to run, so it
    # must skip cleanly rather than hard-fail on a missing binary (a frontend-only
    # ticket would otherwise fail the pytest gate even though it has no tests).
    if gate_scope == "full":
        run_args: tuple[str, ...] = ("-x",)
        pytest_cwd = worktester_dashboard
        scope_msg = "running pytest -x (full scope) ..."
    else:
        # changed scope: only run test files changed relative to base_branch
        changed = _changed_py_files(base_branch, cwd=worktester_dashboard)
        test_files = [f for f in changed if f.startswith("tests/")]
        if not test_files:
            sys.stdout.write(str("  [gate:pytest] no test files changed — skipped") + "\n")
            return GateResult(gate="pytest", passed=True, output="no test files changed")
        # Paths from git diff are relative to the git root, not worktester_dashboard.
        # Run pytest from the git root so tests/ paths resolve correctly.
        rc_root, git_root_out, _ = _run_timed(
            "git", "rev-parse", "--show-toplevel", cwd=worktester_dashboard,
        )
        pytest_cwd = Path(git_root_out.strip()) if rc_root == 0 else worktester_dashboard
        run_args = ("-x", *test_files)
        scope_msg = f"checking {len(test_files)} file(s): {', '.join(test_files)}"

    # Now resolve the pytest binary — only reached when there is something to run.
    ok, pytest_path, _ = _try("which", "pytest")
    if ok:
        pytest_bin = pytest_path
    else:
        # Try inside the tester worktree venv (root-level, not apps/dashboard)
        venv_pytest = wt_root / "venv" / "bin" / "pytest"
        if venv_pytest.exists():
            pytest_bin = str(venv_pytest.resolve())
        else:
            # Tests exist to run but pytest is unavailable — this is an ENVIRONMENT
            # problem (no venv with pytest in this worktree / fresh clone), not a
            # code defect. Classify as ENV_ERROR so the dispatch layer does NOT send
            # the ticket back to the coder (a missing binary can't be fixed by
            # editing code) and does NOT apply needs-rework. (See _LOGIC_FAILURE_
            # CATEGORIES — ENV_ERROR is intentionally excluded.)
            output = (
                "pytest binary not found on PATH and no venv/bin/pytest found — "
                "test infrastructure missing in this worktree (no venv with pytest). "
                "Environment problem, not a code defect."
            )
            structured_log.error(
                "gate_failed", f"[gate:pytest] ENV_ERROR: {output}",
                gate="pytest", issue_num=issue_num,
            )
            return GateResult(gate="pytest", passed=False, output=output, category="ENV_ERROR")

    sys.stdout.write(str(f"  [gate:pytest] {scope_msg}") + "\n")
    rc, stdout, stderr = _run_timed(pytest_bin, *run_args, cwd=pytest_cwd)

    combined = stdout + stderr
    if rc == 0:
        sys.stdout.write(str("  [gate:pytest] PASS") + "\n")
        return GateResult(gate="pytest", passed=True, output=combined)
    else:
        structured_log.error(
            "gate_failed", f"[gate:pytest] FAIL (exit {rc})",
            gate="pytest", issue_num=issue_num, exit_code=rc,
        )
        _revert_to_sit(issue_num, "pytest", combined, repo_name=repo_name)
        return GateResult(gate="pytest", passed=False, output=combined)


def _lint_autofix_commit(
    issue_num: int,
    worktester_dashboard: Path,
    base_branch: str,
    gate_scope: str,
    gate_frontend_lint: bool,
) -> None:
    """Best-effort: auto-fix trivially-fixable lint (ruff --fix, prettier --write)
    and commit+push it on the current feature branch BEFORE the lint check runs.

    This stops formatting / unused-import churn from burning bounded coder
    fix-rounds — only genuinely non-auto-fixable issues reach the check and fail
    the gate. Atomic: if the push fails, the local commit is rolled back so
    origin and local never split (merge-preview merges ``origin/<branch>``). Any
    error is swallowed — the gate then runs exactly as before (no regression).
    """
    try:
        rc_root, git_root_out, _ = _run_timed(
            "git", "rev-parse", "--show-toplevel", cwd=worktester_dashboard)
        git_root = Path(git_root_out.strip()) if rc_root == 0 else worktester_dashboard

        # ── ruff --fix on changed Python files ──────────────────────────────
        py_files = (["."] if gate_scope == "full"
                    else _changed_py_files(base_branch, cwd=worktester_dashboard))
        if py_files:
            ok_ruff, ruff_path, _ = _try("which", "ruff")
            if not ok_ruff:
                venv_ruff = worktester_dashboard / ".." / "venv" / "bin" / "ruff"
                ruff_path = str(venv_ruff.resolve()) if venv_ruff.exists() else None
            if ruff_path:
                _run_timed(ruff_path, "check", "--fix", "--exit-zero", *py_files,
                           cwd=worktester_dashboard)

        # ── prettier --write on changed JS/TS files ─────────────────────────
        if gate_frontend_lint:
            js_ts = (["."] if gate_scope == "full"
                     else _changed_js_ts_files(base_branch, cwd=worktester_dashboard))
            if js_ts:
                ok_prettier, prettier_bin, _ = _try("which", "prettier")
                prefix: list[str] = []
                if not ok_prettier:
                    ok_npx, npx_path, _ = _try("which", "npx")
                    prettier_bin = None
                    if ok_npx:
                        rc_pv, _, _ = _run_timed(npx_path, "--no", "prettier",
                                                 "--version", cwd=git_root)
                        if rc_pv == 0:
                            prettier_bin, prefix = npx_path, ["--no", "prettier"]
                if prettier_bin:
                    _run_timed(prettier_bin, *prefix, "--write", *js_ts, cwd=git_root)

        # ── commit + push only if the auto-fixers changed something ──────────
        rc_st, st_out, _ = _run_timed("git", "status", "--porcelain", cwd=git_root)
        if rc_st != 0 or not st_out.strip():
            return
        rc_br, br_out, _ = _run_timed("git", "rev-parse", "--abbrev-ref", "HEAD",
                                      cwd=git_root)
        branch = br_out.strip()
        if rc_br != 0 or not branch or branch in ("HEAD", base_branch, "master", "develop"):
            return  # detached / on a protected branch — never auto-commit here
        _run_timed("git", "add", "-A", cwd=git_root)
        rc_ci, _, _ = _run_timed(
            "git", "commit", "-m", f"style: auto-fix lint (issue #{issue_num})",
            cwd=git_root)
        if rc_ci != 0:
            return
        rc_push, _, push_err = _run_timed("git", "push", "origin", "HEAD", cwd=git_root)
        if rc_push != 0:
            _run_timed("git", "reset", "--soft", "HEAD~1", cwd=git_root)
            structured_log.warn(
                "lint_autofix_push_failed",
                f"[gate:lint] auto-fix push failed; reverted local commit: {push_err.strip()[:200]}",
                issue_num=issue_num)
            return
        sys.stdout.write(str(f"  [gate:lint] auto-fixed + committed lint churn (issue #{issue_num})") + "\n")
        structured_log.info("lint_autofix_committed",
                            "[gate:lint] auto-fixed lint churn and pushed",
                            gate="lint", issue_num=issue_num)
    except Exception as e:
        structured_log.warn("lint_autofix_error",
                            f"[gate:lint] auto-fix pass errored (ignored): {e}",
                            issue_num=issue_num, exc=str(e))


def _gate_lint(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
    gate_frontend_lint: bool = True,
) -> GateResult:
    """Gate: run ruff (Python) and eslint/biome + prettier (JS/TS).

    gate_scope='changed' (default): only lint files changed relative to
    base_branch. gate_scope='full': run against whole codebase (legacy behaviour).
    Skips gracefully when tools are not installed or no relevant files changed.
    gate_frontend_lint: when False, skips the frontend (JS/TS) lint portion.
    """
    if skip:
        sys.stdout.write(str("  [gate:lint] skipped") + "\n")
        return GateResult(gate="lint", passed=True, skipped=True)

    _post_agent_event("gate:lint")

    # Auto-fix trivially-fixable lint (ruff --fix / prettier --write) and commit
    # before checking, so formatting / unused-import churn doesn't burn bounded
    # coder fix-rounds. Best-effort; on any error the check below runs unchanged.
    _lint_autofix_commit(issue_num, worktester_dashboard, base_branch,
                         gate_scope, gate_frontend_lint)

    combined = ""
    any_ran = False

    # ── Python lint via ruff ───────────────────────────────────────────────────
    ok_ruff, ruff_path, _ = _try("which", "ruff")
    if not ok_ruff:
        venv_ruff = worktester_dashboard / ".." / "venv" / "bin" / "ruff"
        ruff_bin = str(venv_ruff.resolve()) if venv_ruff.exists() else None
    else:
        ruff_bin = ruff_path

    if ruff_bin:
        if gate_scope == "full":
            sys.stdout.write(str("  [gate:lint] running ruff check . (full scope) ...") + "\n")
            rc, stdout, stderr = _run_timed(ruff_bin, "check", ".", cwd=worktester_dashboard)
            combined += stdout + stderr
            any_ran = True
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:lint] ruff FAIL (exit {rc})",
                                     gate="lint", issue_num=issue_num, exit_code=rc)
                _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
                return GateResult(gate="lint", passed=False, output=combined)
            sys.stdout.write(str("  [gate:lint] ruff PASS") + "\n")
        else:
            py_files = _changed_py_files(base_branch, cwd=worktester_dashboard)
            if py_files:
                sys.stdout.write(
                    str(f"  [gate:lint] ruff checking {len(py_files)} file(s): {', '.join(py_files)}") + "\n"
                )
                # Paths from git diff are relative to the repo root, not worktester_dashboard.
                _rc_root, _root_out, _ = _run_timed(
                    "git", "rev-parse", "--show-toplevel", cwd=worktester_dashboard
                )
                ruff_cwd = Path(_root_out.strip()) if _rc_root == 0 and _root_out.strip() else worktester_dashboard
                rc, stdout, stderr = _run_timed(ruff_bin, "check", *py_files,
                                                cwd=ruff_cwd)
                combined += stdout + stderr
                any_ran = True
                if rc != 0:
                    structured_log.error("gate_failed", f"[gate:lint] ruff FAIL (exit {rc})",
                                         gate="lint", issue_num=issue_num, exit_code=rc)
                    _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
                    return GateResult(gate="lint", passed=False, output=combined)
                sys.stdout.write(str("  [gate:lint] ruff PASS") + "\n")
            else:
                sys.stdout.write(str("  [gate:lint] no Python files changed — ruff skipped") + "\n")
    else:
        structured_log.warn("lint_tool_missing", "[gate:lint] ruff not found; skipping Python lint",
                            issue_num=issue_num)

    # ── Frontend lint via eslint/biome + prettier ──────────────────────────────
    if not gate_frontend_lint:
        sys.stdout.write(str("  [gate:lint] frontend lint disabled (COMMANDER_GATE_FRONTEND_LINT=0)") + "\n")
    else:
        if gate_scope == "full":
            js_ts_files_for_fe: list[str] = ["."]
        else:
            js_ts_files_for_fe = _changed_js_ts_files(base_branch, cwd=worktester_dashboard)

        if js_ts_files_for_fe:
            fe_passed, fe_output = _run_frontend_lint(
                issue_num, worktester_dashboard, js_ts_files_for_fe, gate_scope
            )
            combined += fe_output
            if fe_output.strip():
                any_ran = True
            if not fe_passed:
                _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
                return GateResult(gate="lint", passed=False, output=combined)
        else:
            sys.stdout.write(str("  [gate:lint] no JS/TS files changed — frontend lint skipped") + "\n")

    if not any_ran:
        sys.stdout.write(str("  [gate:lint] no lintable files changed — skipped") + "\n")
        return GateResult(gate="lint", passed=True, output="no lintable files changed")

    sys.stdout.write(str("  [gate:lint] PASS") + "\n")
    return GateResult(gate="lint", passed=True, output=combined)


def _run_frontend_lint(
    issue_num: int,
    worktester_dashboard: Path,
    js_ts_files: list[str],
    gate_scope: str,
) -> tuple[bool, str]:
    """Run eslint/biome + prettier --check on JS/TS files.

    Returns (passed: bool, combined_output: str).
    Skips gracefully when tools are missing.
    """
    combined = ""
    passed = True

    # Paths from _changed_js_ts_files are relative to the git root.
    # Resolve the git root so linters run from there, not worktester_dashboard.
    rc_root, git_root_out, _ = _run_timed(
        "git", "rev-parse", "--show-toplevel", cwd=worktester_dashboard
    )
    lint_cwd = Path(git_root_out.strip()) if rc_root == 0 else worktester_dashboard

    # eslint or biome (prefer biome if configured)
    ok_biome, biome_path, _ = _try("which", "biome")
    ok_eslint, eslint_path, _ = _try("which", "eslint")
    ok_npx, npx_path, _ = _try("which", "npx")

    linter_bin: Optional[str] = None
    linter_args: list[str] = []
    if ok_biome:
        linter_bin = biome_path
        linter_args = ["check"] + (["--apply=false"] if gate_scope != "full" else ["--apply=false", "."])
    elif ok_eslint:
        linter_bin = eslint_path
        linter_args = ["--max-warnings=0"]
    elif ok_npx:
        for _eslint_candidate in [
            lint_cwd / "node_modules" / ".bin" / "eslint",
            REPO_ROOT / "node_modules" / ".bin" / "eslint",
        ]:
            if _eslint_candidate.exists():
                linter_bin = str(_eslint_candidate.resolve())
                linter_args = ["--max-warnings=0"]
                break
        if not linter_bin:
            _local_biome = lint_cwd / "node_modules" / ".bin" / "biome"
            if _local_biome.exists():
                linter_bin = npx_path
                linter_args = ["--no", "biome", "check", "--apply=false"]

    if linter_bin:
        targets = ["."] if gate_scope == "full" else js_ts_files
        sys.stdout.write(str(f"  [gate:lint-fe] running frontend linter on {len(targets)} target(s) ...") + "\n")
        rc, stdout, stderr = _run_timed(linter_bin, *linter_args, *targets,
                                        cwd=lint_cwd)
        combined += stdout + stderr
        if rc != 0:
            structured_log.error("gate_failed", f"[gate:lint-fe] FAIL (exit {rc})",
                                 gate="lint", issue_num=issue_num, exit_code=rc)
            passed = False
        else:
            sys.stdout.write(str("  [gate:lint-fe] PASS") + "\n")
    else:
        structured_log.warn("lint_tool_missing",
                            "[gate:lint-fe] no eslint/biome found; skipping JS/TS lint",
                            issue_num=issue_num)

    # prettier --check
    if passed:
        ok_prettier, prettier_bin, _ = _try("which", "prettier")
        if not ok_prettier and ok_npx:
            rc_pcheck, _, _ = _run_timed(
                npx_path, "--no", "prettier", "--version", cwd=lint_cwd
            )
            if rc_pcheck == 0:
                prettier_bin = npx_path
                prettier_prefix = ["--no", "prettier"]
            else:
                prettier_bin = None
                prettier_prefix = []
        else:
            prettier_prefix = []

        if ok_prettier or (not ok_prettier and ok_npx and prettier_bin):
            targets = ["."] if gate_scope == "full" else js_ts_files
            cmd_args = prettier_prefix + ["--check"] + targets
            sys.stdout.write(str(f"  [gate:lint-fe] running prettier --check on {len(targets)} target(s) ...") + "\n")
            rc, stdout, stderr = _run_timed(prettier_bin, *cmd_args,
                                            cwd=lint_cwd)
            combined += stdout + stderr
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:lint-fe] prettier FAIL (exit {rc})",
                                     gate="lint", issue_num=issue_num, exit_code=rc)
                passed = False
            else:
                sys.stdout.write(str("  [gate:lint-fe] prettier PASS") + "\n")

    return passed, combined


# ── constants for the remaining gate functions ────────────────────────────────

_MERGE_PREVIEW_TMP_BRANCH = "_cmdr_merge_preview_tmp"

# Repo-root-relative path of the monolith we are strangling (issue #761).
MONOLITH_GUARDED_FILE = "apps/dashboard/server.py"


# ── _file_line_count_at_ref ───────────────────────────────────────────────────

def _file_line_count_at_ref(ref: str, rel_path: str, cwd: Path) -> Optional[int]:
    """Return the line count of rel_path at a git ref, or None if absent.

    Uses ``git show <ref>:<rel_path>`` so the count reflects the committed file
    at that ref rather than the working tree. Returns None when the file does
    not exist at the ref or the path is not inside a git repo.
    """
    _f = _lookup_in_sm("_file_line_count_at_ref", _file_line_count_at_ref)
    if _f is not None:
        return _f(ref, rel_path, cwd)
    rc, out, _ = _run_timed("git", "show", f"{ref}:{rel_path}", cwd=cwd)
    if rc != 0:
        return None
    return len(out.splitlines())


# ── impeccable findings helpers ───────────────────────────────────────────────

def _impeccable_findings(npx_path: str, target: str, cwd: Path) -> Optional[list[dict]]:
    """Run ``impeccable detect <target> --json`` and return the findings list.

    Returns ``[]`` when the target is clean, the parsed list when anti-patterns
    are found, or ``None`` when output could not be parsed (caller decides how
    to handle an inconclusive scan).
    """
    _f = _lookup_in_sm("_impeccable_findings", _impeccable_findings)
    if _f is not None:
        return _f(npx_path, target, cwd)
    rc, out, _ = _run_timed(
        npx_path, "--yes", "impeccable", "detect", target, "--json", cwd=cwd,
    )
    text = (out or "").strip()
    if not text:
        return [] if rc == 0 else None
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        data = data.get("findings") or data.get("results") or []
    return data if isinstance(data, list) else []


def _finding_sig(f: dict) -> tuple:
    """Stable signature for a finding, ignoring the file path (so base and HEAD
    copies of the same file compare cleanly even from different temp paths)."""
    return (f.get("antipattern"), f.get("line"), (f.get("snippet") or "").strip())


def _net_new_findings(base: list[dict], head: list[dict]) -> list[dict]:
    """Findings present at HEAD but not accounted for at base (multiset diff).

    Each base finding cancels at most one HEAD finding with the same signature,
    so adding a *second* identical anti-pattern still surfaces as net-new.
    """
    remaining: dict[tuple, int] = {}
    for f in base:
        sig = _finding_sig(f)
        remaining[sig] = remaining.get(sig, 0) + 1
    net_new: list[dict] = []
    for f in head:
        sig = _finding_sig(f)
        if remaining.get(sig, 0) > 0:
            remaining[sig] -= 1
        else:
            net_new.append(f)
    return net_new


# ── gate functions extracted from sprint_manager.py (issue #1281) ─────────────


def _gate_merge_preview(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    skip: bool,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
) -> "GateResult":
    """Gate 3 -- simulate merge in worktester root without committing.

    Rebase path (when origin/feature exists):
    1. fetch origin
    2. checkout/track origin/target_branch locally
    3. create temp branch _cmdr_merge_preview_tmp at origin/feature tip
    4. rebase temp branch onto origin/target_branch
    5. if rebase fails → gate fail (like merge conflict)
    6. if rebase ok → merge --no-commit --no-ff temp into target
    7. finally: merge --abort, delete temp branch, leave clean

    Fallback (no origin/feature): merge origin/feature ref directly.
    """
    if skip:
        sys.stdout.write(str("  [gate:merge-preview] skipped") + "\n")
        return GateResult(gate="merge-preview", passed=True, skipped=True)

    _post_agent_event("gate:merge-preview")
    sys.stdout.write(str(
        f"  [gate:merge-preview] simulating merge of {feature_branch} into {target_branch} ..."
    ) + "\n")

    merge_ok = False
    combined = ""
    stashed = False

    try:
        # Step 0: stash any unstaged/untracked changes so the worktree is clean
        # (a dirty worktree causes `git rebase` to abort with "unstaged changes")
        stash_rc, stash_out, stash_err = _run_timed(
            "git", "stash", "--include-untracked", cwd=worktester_root
        )
        stashed = "No local changes to save" not in stash_out and stash_rc == 0

        # Step 1: fetch
        _run_timed("git", "fetch", "origin", cwd=worktester_root)

        # Step 2: ensure target branch exists locally and is up-to-date
        ok, _, _ = _try("git", "show-ref", "--verify", "--quiet",
                        f"refs/heads/{target_branch}", cwd=worktester_root)
        if ok:
            _run_timed("git", "checkout", target_branch, cwd=worktester_root)
            _run_timed("git", "pull", "origin", target_branch, cwd=worktester_root)
        else:
            _run_timed("git", "checkout", "--track", f"origin/{target_branch}",
                       cwd=worktester_root)

        # Step 3: check whether origin/feature exists
        origin_feature_ok, _, _ = _try(
            "git", "rev-parse", "--verify", f"origin/{feature_branch}",
            cwd=worktester_root,
        )

        if origin_feature_ok:
            # Clean up any leftover tmp branch from a prior aborted run
            _try("git", "branch", "-D", _MERGE_PREVIEW_TMP_BRANCH, cwd=worktester_root)

            # Create temp branch at origin/feature tip
            _run_timed(
                "git", "checkout", "-b", _MERGE_PREVIEW_TMP_BRANCH,
                f"origin/{feature_branch}",
                cwd=worktester_root,
            )

            # Step 4: rebase temp onto origin/target
            rebase_rc, rebase_out, rebase_err = _run_timed(
                "git", "rebase", f"origin/{target_branch}",
                cwd=worktester_root,
            )
            combined += rebase_out + rebase_err

            if rebase_rc != 0:
                sys.stdout.write(str(
                    f"  [gate:merge-preview] FAIL -- rebase onto {target_branch} failed"
                ) + "\n")
                structured_log.error(
                    "gate_failed",
                    f"[gate:merge-preview] FAIL: rebase onto {target_branch} failed",
                    gate="merge-preview", issue_num=issue_num, target_branch=target_branch,
                )
                # Abort rebase so the worktree is clean
                _run_timed("git", "rebase", "--abort", cwd=worktester_root)
                _run_timed("git", "checkout", target_branch, cwd=worktester_root)
                _try("git", "branch", "-D", _MERGE_PREVIEW_TMP_BRANCH, cwd=worktester_root)
                _revert_to_sit(issue_num, "merge-preview", combined, repo_name=repo_name)
                return GateResult(gate="merge-preview", passed=False, output=combined)

            # Step 5: return to target and do dry-run merge of rebased temp
            _run_timed("git", "checkout", target_branch, cwd=worktester_root)
            rc, stdout, stderr = _run_timed(
                "git", "merge", "--no-commit", "--no-ff", _MERGE_PREVIEW_TMP_BRANCH,
                cwd=worktester_root,
            )
            combined += stdout + stderr
            merge_ok = (rc == 0)
        else:
            # Fallback: merge remote tracking ref directly (original behaviour)
            rc, stdout, stderr = _run_timed(
                "git", "merge", "--no-commit", "--no-ff", f"origin/{feature_branch}",
                cwd=worktester_root,
            )
            combined = stdout + stderr
            merge_ok = (rc == 0)

        if merge_ok:
            sys.stdout.write(str("  [gate:merge-preview] PASS -- no conflicts") + "\n")
        else:
            structured_log.error(
                "gate_failed",
                f"[gate:merge-preview] FAIL: conflicts detected merging into {target_branch}",
                gate="merge-preview", issue_num=issue_num, target_branch=target_branch,
            )
    finally:
        # Always abort to leave working tree clean
        _run_timed("git", "merge", "--abort", cwd=worktester_root)
        # Ensure we're back on target branch
        _run_timed("git", "checkout", target_branch, cwd=worktester_root)
        # Delete tmp branch if it exists
        _try("git", "branch", "-D", _MERGE_PREVIEW_TMP_BRANCH, cwd=worktester_root)
        # Restore any stashed changes
        if stashed:
            _run_timed("git", "stash", "pop", cwd=worktester_root)

    if not merge_ok:
        _revert_to_sit(issue_num, "merge-preview", combined, repo_name=repo_name)
        return GateResult(gate="merge-preview", passed=False, output=combined)

    return GateResult(gate="merge-preview", passed=True, output=combined)


def _gate_typecheck(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> "GateResult":
    """Gate: run mypy (Python) and/or tsc --noEmit (TypeScript) on changed files.

    Skips gracefully when the tool is not installed or no relevant files changed.
    """
    if skip:
        sys.stdout.write(str("  [gate:typecheck] skipped") + "\n")
        return GateResult(gate="typecheck", passed=True, skipped=True)

    _post_agent_event("gate:typecheck")
    results_passed = True
    combined = ""

    # ── Python typecheck via mypy ──────────────────────────────────────────────
    if gate_scope == "full":
        py_files: list[str] = ["apps/dashboard"]
    else:
        py_files = _changed_py_files(base_branch, cwd=worktester_dashboard)

    if py_files:
        ok_mypy, mypy_path, _ = _try("which", "mypy")
        if not ok_mypy:
            venv_mypy = worktester_dashboard / ".." / "venv" / "bin" / "mypy"
            mypy_bin = str(venv_mypy.resolve()) if venv_mypy.exists() else None
        else:
            mypy_bin = mypy_path

        if mypy_bin:
            targets = ["."] if gate_scope == "full" else py_files
            sys.stdout.write(str(f"  [gate:typecheck] running mypy on {len(targets)} target(s) ...") + "\n")
            rc, stdout, stderr = _run_timed(mypy_bin, "--ignore-missing-imports", *targets,
                                            cwd=worktester_dashboard)
            combined += stdout + stderr
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:typecheck] mypy FAIL (exit {rc})",
                                     gate="typecheck", issue_num=issue_num, exit_code=rc)
                results_passed = False
            else:
                sys.stdout.write(str("  [gate:typecheck] mypy PASS") + "\n")
        else:
            structured_log.warn("typecheck_tool_missing",
                                "[gate:typecheck] mypy not found; skipping Python typecheck",
                                issue_num=issue_num)

    # ── TypeScript typecheck via tsc --noEmit ──────────────────────────────────
    if gate_scope == "full":
        ts_files: list[str] = []
        ok_ts, ts_out, _ = _try("find", ".", "-name", "tsconfig.json", "-maxdepth", "3",
                                cwd=worktester_dashboard)
        if ok_ts and ts_out.strip():
            ts_files = ["_has_tsconfig_"]  # sentinel — just triggers tsc check
    else:
        ts_files = _changed_js_ts_files(base_branch, cwd=worktester_dashboard)

    if ts_files:
        ok_tsc, tsc_path, _ = _try("which", "tsc")
        if ok_tsc:
            sys.stdout.write(str("  [gate:typecheck] running tsc --noEmit ...") + "\n")
            rc, stdout, stderr = _run_timed(tsc_path, "--noEmit", cwd=worktester_dashboard)
            combined += stdout + stderr
            if rc != 0:
                structured_log.error("gate_failed", f"[gate:typecheck] tsc FAIL (exit {rc})",
                                     gate="typecheck", issue_num=issue_num, exit_code=rc)
                results_passed = False
            else:
                sys.stdout.write(str("  [gate:typecheck] tsc PASS") + "\n")
        else:
            structured_log.warn("typecheck_tool_missing",
                                "[gate:typecheck] tsc not found; skipping TS typecheck",
                                issue_num=issue_num)

    if not py_files and not ts_files:
        sys.stdout.write(str("  [gate:typecheck] no typed files changed — skipped") + "\n")
        return GateResult(gate="typecheck", passed=True, output="no typed files changed")

    if not results_passed:
        _revert_to_sit(issue_num, "typecheck", combined, repo_name=repo_name)
        return GateResult(gate="typecheck", passed=False, output=combined)

    if not combined:
        sys.stdout.write(str("  [gate:typecheck] no typecheck tools found — skipped") + "\n")
        return GateResult(gate="typecheck", passed=True, skipped=True,
                          output="no typecheck tools found")

    return GateResult(gate="typecheck", passed=True, output=combined)


def _gate_design(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> "GateResult":
    """Gate: run impeccable detect on the frontend for UI anti-patterns.

    Uses the deterministic pattern-matching detector (no LLM).

    gate_scope='changed' (default): scope to frontend files changed relative to
    base_branch and fail only on *net-new* anti-patterns the diff introduces —
    pre-existing baseline findings in those files do not bounce the ticket
    (mirrors the diff-aware monolith/typecheck/lint gates). gate_scope='full'
    restores the legacy whole-directory scan that fails on any finding.
    """
    if skip:
        sys.stdout.write(str("  [gate:design] skipped") + "\n")
        return GateResult(gate="design", passed=True, skipped=True)

    _post_agent_event("gate:design")

    ok_npx, npx_path, _ = _try("which", "npx")
    if not ok_npx:
        structured_log.warn("design_tool_missing",
                            "[gate:design] npx not found; skipping design gate",
                            issue_num=issue_num)
        return GateResult(gate="design", passed=True, skipped=True,
                          output="npx not found — design gate skipped")

    # Detect if there is any frontend to scan
    has_frontend = any(
        True for ext in _DESIGN_FE_EXTENSIONS
        for _ in worktester_dashboard.rglob(f"*{ext}")
    ) if worktester_dashboard.exists() else False

    if not has_frontend:
        sys.stdout.write(str("  [gate:design] no frontend files found — skipped") + "\n")
        return GateResult(gate="design", passed=True, skipped=True,
                          output="no frontend files — design gate skipped")

    # ── changed-scope: fail only on net-new anti-patterns the diff introduces ──
    if gate_scope != "full":
        # Pin the comparison to the merge-base (where this feature branch diverged
        # from the sprint branch), NOT the moving branch tip. Sibling tickets that
        # merge into the sprint branch mid-run otherwise shift the baseline, so a
        # file that PASSED the design gate at coder time can flip to FAIL post-tester
        # (observed: #1059's 11.5px tiny-text passed at coder, failed at tester once
        # a sibling merged into the sprint branch). Merge-base keeps "net-new vs the
        # diff this branch introduces" stable across both gate stages.
        mb_rc, mb_out, _ = _run_timed(
            "git", "merge-base", "HEAD", base_branch, cwd=worktester_dashboard)
        base_ref = mb_out.strip() if (mb_rc == 0 and mb_out.strip()) else base_branch
        changed = _changed_frontend_files(base_ref, cwd=worktester_dashboard)
        if not changed:
            sys.stdout.write(str("  [gate:design] no changed frontend files — PASS") + "\n")
            return GateResult(gate="design", passed=True,
                              output="no changed frontend files")

        sys.stdout.write(
            str(f"  [gate:design] checking {len(changed)} changed frontend file(s) "
                f"for net-new anti-patterns vs {base_branch} ...") + "\n")

        net_new: list[dict] = []
        inconclusive: list[str] = []
        for rel in changed:
            head_findings = _impeccable_findings(npx_path, rel, cwd=worktester_dashboard)
            if head_findings is None:
                inconclusive.append(rel)
                continue
            if not head_findings:
                continue  # file is clean at HEAD; nothing to compare
            # Base version of the file (empty if newly added → all findings net-new)
            rc_show, base_src, _ = _run_timed(
                "git", "show", f"{base_ref}:{rel}", cwd=worktester_dashboard,
            )
            base_findings: list[dict] = []
            if rc_show == 0:
                suffix = os.path.splitext(rel)[1] or ".html"
                tmp = tempfile.NamedTemporaryFile(
                    mode="w", suffix=suffix, delete=False, encoding="utf-8")
                try:
                    tmp.write(base_src)
                    tmp.close()
                    parsed = _impeccable_findings(npx_path, tmp.name, cwd=worktester_dashboard)
                    base_findings = parsed or []
                finally:
                    try:
                        os.unlink(tmp.name)
                    except OSError:
                        pass
            file_net_new = _net_new_findings(base_findings, head_findings)
            for f in file_net_new:
                f = dict(f)
                f.setdefault("file", rel)
                net_new.append(f)

        if inconclusive:
            # Could not analyse a changed file — fail closed rather than wave it through.
            msg = ("design gate could not analyse changed file(s): "
                   + ", ".join(inconclusive))
            structured_log.error("gate_failed", f"[gate:design] FAIL — {msg}",
                                 gate="design", issue_num=issue_num)
            _revert_to_sit(issue_num, "design", msg, repo_name=repo_name)
            return GateResult(gate="design", passed=False, output=msg)

        if net_new:
            lines = [f"{len(net_new)} net-new design anti-pattern(s) introduced by this diff:"]
            for f in net_new:
                lines.append(
                    f"  [{f.get('antipattern')}] {f.get('file')}: {f.get('snippet') or f.get('name')}")
            msg = "\n".join(lines)
            structured_log.error("gate_failed", f"[gate:design] FAIL — {len(net_new)} net-new",
                                 gate="design", issue_num=issue_num)
            _revert_to_sit(issue_num, "design", msg, repo_name=repo_name)
            return GateResult(gate="design", passed=False, output=msg)

        sys.stdout.write(
            str("  [gate:design] PASS — no net-new design anti-patterns in changed files") + "\n")
        return GateResult(
            gate="design", passed=True,
            output=f"{len(changed)} changed frontend file(s); no net-new anti-patterns")

    # ── full-scope (legacy): fail on any finding across the whole static dir ──
    static_dir = worktester_dashboard / "apps" / "dashboard" / "static"
    if not static_dir.exists():
        static_dir = worktester_dashboard / "static"
    if not static_dir.exists():
        static_dir = worktester_dashboard

    sys.stdout.write(str(f"  [gate:design] running impeccable detect {static_dir.name}/ (full) ...") + "\n")
    rc, stdout, stderr = _run_timed(
        npx_path, "--yes", "impeccable", "detect", str(static_dir), "--json",
        cwd=worktester_dashboard,
    )
    combined = stdout + stderr

    if rc == 0:
        sys.stdout.write(str("  [gate:design] PASS — no design anti-patterns detected") + "\n")
        return GateResult(gate="design", passed=True, output=combined)
    else:
        # impeccable exits non-zero when issues are found
        structured_log.error("gate_failed", f"[gate:design] FAIL (exit {rc})",
                             gate="design", issue_num=issue_num, exit_code=rc)
        _revert_to_sit(issue_num, "design", combined, repo_name=repo_name)
        return GateResult(gate="design", passed=False, output=combined)


def _gate_monolith(
    issue_num: int,
    worktester_root: Path,
    skip: bool,
    base_branch: str = "develop",
    repo_name: Optional[str] = None,
    guarded_file: str = MONOLITH_GUARDED_FILE,
) -> "GateResult":
    """Gate: reject any diff that grows the server.py monolith (issue #761).

    Compares the line count of ``guarded_file`` at HEAD against ``base_branch``.
    A strict increase fails the gate (and reverts the ticket to SIT); a decrease
    or unchanged count passes. Skips gracefully when the file is absent at either
    ref (e.g. not a git repo) so the gate never blocks non-dashboard projects.
    """
    if skip:
        sys.stdout.write(str("  [gate:monolith] skipped") + "\n")
        return GateResult(gate="monolith", passed=True, skipped=True)

    _post_agent_event("gate:monolith")

    base_count = _file_line_count_at_ref(base_branch, guarded_file, cwd=worktester_root)
    head_count = _file_line_count_at_ref("HEAD", guarded_file, cwd=worktester_root)

    if base_count is None or head_count is None:
        sys.stdout.write(str(f"  [gate:monolith] {guarded_file} not found at base/HEAD — skipped") + "\n")
        return GateResult(
            gate="monolith", passed=True, skipped=True,
            output=f"{guarded_file} not found at base or HEAD — monolith gate skipped",
        )

    if head_count > base_count:
        msg = (
            f"{guarded_file} grew {base_count} → {head_count} lines "
            f"(+{head_count - base_count}). New endpoints belong in "
            f"apps/dashboard/routers/<area>.py — adding routes to server.py is forbidden."
        )
        structured_log.error("gate_failed", f"[gate:monolith] FAIL — {msg}",
                             gate="monolith", issue_num=issue_num)
        _revert_to_sit(issue_num, "monolith", msg, repo_name=repo_name)
        return GateResult(gate="monolith", passed=False, output=msg)

    sys.stdout.write(
        str(f"  [gate:monolith] PASS — {guarded_file} {base_count} → {head_count} lines (no growth)") + "\n"
    )
    return GateResult(
        gate="monolith", passed=True,
        output=f"{guarded_file} {base_count} → {head_count} lines (no growth)",
    )


# ── coder-no-test-edits gate ──────────────────────────────────────────────────

_CODER_BLOCKED_DEFAULT_PATTERNS: list[str] = ["tests/**"]


def _coder_no_test_edits_gate_enabled() -> bool:
    """COMMANDER_GATE_CODER_NO_TEST_EDITS defaults on; 'false'/'0'/'no'/'off' disables."""
    return os.environ.get("COMMANDER_GATE_CODER_NO_TEST_EDITS", "1").strip().lower() not in (
        "false", "0", "no", "off"
    )


def _get_coder_blocked_patterns() -> list[str]:
    """Return blocked path glob patterns from CODER_BLOCKED_PATH_PATTERNS or defaults."""
    env = os.environ.get("CODER_BLOCKED_PATH_PATTERNS", "").strip()
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    return list(_CODER_BLOCKED_DEFAULT_PATTERNS)


def _get_coder_test_allowlist() -> list[str]:
    """Return allowlisted paths from CODER_TEST_PATH_ALLOWLIST (comma-separated)."""
    env = os.environ.get("CODER_TEST_PATH_ALLOWLIST", "").strip()
    if not env:
        return []
    return [p.strip() for p in env.split(",") if p.strip()]


def _gate_coder_no_test_edits(
    issue_num: int,
    worktester_root: Path,
    skip: bool,
    base_branch: str = "develop",
    repo_name: Optional[str] = None,
    blocked_patterns: Optional[list[str]] = None,
    allowlist: Optional[list[str]] = None,
) -> "GateResult":
    """Gate: fail if the coder's diff modifies any path matching blocked_patterns.

    Uses --diff-filter=CMD (Modified, Deleted, Copied) so TDD-written new test
    files (Added) are allowed through while edits, deletions, and copies of
    existing grading tests are blocked. Renamed files are caught via a separate
    --diff-filter=R pass. Paths listed in allowlist are exempted unconditionally.

    blocked_patterns: fnmatch glob patterns; reads CODER_BLOCKED_PATH_PATTERNS
    when None.  allowlist: exact path matches; reads CODER_TEST_PATH_ALLOWLIST
    when None.
    """
    if skip:
        sys.stdout.write(str("  [gate:coder-no-test-edits] skipped") + "\n")
        return GateResult(gate="coder-no-test-edits", passed=True, skipped=True)

    _post_agent_event("gate:coder-no-test-edits")

    if blocked_patterns is None:
        blocked_patterns = _get_coder_blocked_patterns()
    if allowlist is None:
        allowlist = _get_coder_test_allowlist()

    rc, out, _ = _run_timed(
        "git", "diff", base_branch, "--name-only", "--diff-filter=CMD",
        cwd=worktester_root,
    )
    rc_r, out_r, _ = _run_timed(
        "git", "diff", base_branch, "--name-status", "--diff-filter=R",
        cwd=worktester_root,
    )

    changed_paths = [p for p in out.splitlines() if p.strip()] if rc == 0 else []
    for line in (out_r.splitlines() if rc_r == 0 else []):
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            old_path, new_path = parts[1].strip(), parts[2].strip()
            if old_path:
                changed_paths.append(old_path)
            if new_path:
                changed_paths.append(new_path)

    if not changed_paths:
        sys.stdout.write(str("  [gate:coder-no-test-edits] empty diff — PASS") + "\n")
        return GateResult(gate="coder-no-test-edits", passed=True, output="empty diff")
    allowlist_set = set(allowlist)

    blocked: list[str] = []
    for path in changed_paths:
        if path in allowlist_set:
            continue
        if any(fnmatch.fnmatch(path, pat) for pat in blocked_patterns):
            blocked.append(path)

    if not blocked:
        sys.stdout.write(str("  [gate:coder-no-test-edits] PASS") + "\n")
        return GateResult(gate="coder-no-test-edits", passed=True)

    msg = (
        f"Coder modified {len(blocked)} grading test file(s) — "
        f"coders may not modify grading tests:\n"
        + "\n".join(f"  {p}" for p in blocked)
    )
    structured_log.error(
        "gate_failed",
        f"[gate:coder-no-test-edits] FAIL — {len(blocked)} blocked path(s): "
        + ", ".join(blocked[:3]),
        gate="coder-no-test-edits", issue_num=issue_num,
    )
    _revert_to_sit(issue_num, "coder-no-test-edits", msg, repo_name=repo_name)
    return GateResult(gate="coder-no-test-edits", passed=False, output=msg)


def _log_gate_result(r: "GateResult", issue_num: int) -> None:
    """Uniform per-gate outcome logging. Skipped gates are not logged."""
    try:
        if getattr(r, "skipped", False):
            return
        if r.passed:
            structured_log.info(
                "gate_passed", f"gate {r.gate} passed for #{issue_num}",
                gate=r.gate, issue_num=issue_num,
            )
        else:
            _lines = [ln for ln in (r.output or "").strip().splitlines() if ln.strip()]
            _reason = _lines[-1][:200] if _lines else ""
            structured_log.error(
                "gate_failed", f"gate {r.gate} failed for #{issue_num}",
                gate=r.gate, issue_num=issue_num, reason=_reason,
            )
    except Exception:
        pass


def _run_quality_gates(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    worktester_dashboard: Path,
    skip_all: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    gate_typecheck: bool = True,
    gate_design: bool = True,
    gate_frontend_lint: bool = True,
    gate_monolith: bool = True,
    gate_coder_no_test_edits: bool = True,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> "list[GateResult]":
    """Run ALL quality gates in one pass. Returns list of GateResult.

    Order (cheap/deterministic first): typecheck → lint → design → pytest →
    merge-preview → monolith. Every gate runs (no early-return), so a single pass
    surfaces all failures at once — including merge conflicts — and the caller
    aggregates them into one revert + one retry instead of one-failure-per-attempt.
    Per-gate reverts are suppressed during the pass (the caller does the combined
    revert). If skip_all is True, all gates are skipped.

    base_branch: branch to diff against when gate_scope='changed' (default: 'develop').
    gate_scope: 'changed' (default) scopes gates to changed files only;
                'full' restores legacy full-codebase behaviour.
    """
    # Proxy: allow tests to patch gate functions on the sprint_manager module.
    # Uses _lookup_in_sm so patches applied via either the flat ("sprint_manager")
    # or the package ("services.sprint_manager.sprint_manager") import path are found.
    def _resolve(local_fn, name: str):
        remote = _lookup_in_sm(name, local_fn)
        return remote if remote is not None else local_fn

    fn_coder_test = _resolve(_gate_coder_no_test_edits, "_gate_coder_no_test_edits")
    fn_tc = _resolve(_gate_typecheck, "_gate_typecheck")
    fn_lint = _resolve(_gate_lint, "_gate_lint")
    fn_design = _resolve(_gate_design, "_gate_design")
    fn_pytest = _resolve(_gate_pytest, "_gate_pytest")
    fn_merge = _resolve(_gate_merge_preview, "_gate_merge_preview")
    fn_monolith = _resolve(_gate_monolith, "_gate_monolith")
    fn_log = _resolve(_log_gate_result, "_log_gate_result")

    results: list[GateResult] = []

    # Run ALL gates (no early-return) so every failure surfaces in one pass.
    # Suppress each gate's own revert/comment/sidecar; the caller aggregates all
    # failures into a single report + one retry. Restored before returning.
    global _REVERT_SUPPRESSED
    _prev_suppressed = _REVERT_SUPPRESSED
    _REVERT_SUPPRESSED = True

    # Gate 0 -- coder-no-test-edits (cheapest: pure git diff path scan)
    r_coder_test = fn_coder_test(
        issue_num,
        worktester_root,
        skip=(skip_all or not gate_coder_no_test_edits),
        base_branch=base_branch,
        repo_name=repo_name,
    )
    results.append(r_coder_test)
    fn_log(r_coder_test, issue_num)

    # Gate 1 -- typecheck (cheap, deterministic)
    r_tc = fn_tc(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_typecheck),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )
    results.append(r_tc)
    fn_log(r_tc, issue_num)

    # Gate 2 -- lint (Python ruff + frontend eslint/biome/prettier)
    r_lint = fn_lint(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_lint),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
        gate_frontend_lint=gate_frontend_lint,
    )
    results.append(r_lint)
    fn_log(r_lint, issue_num)

    # Gate 3 -- design (impeccable UI anti-pattern detector, no LLM)
    r_design = fn_design(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_design),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )
    results.append(r_design)
    fn_log(r_design, issue_num)

    # Gate 4 -- pytest
    r_pytest = fn_pytest(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_pytest),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
        worktester_root=worktester_root,
    )
    results.append(r_pytest)
    fn_log(r_pytest, issue_num)

    # Gate 5 -- merge-preview (most expensive; run last)
    r_merge = fn_merge(
        issue_num,
        feature_branch,
        worktester_root,
        skip=(skip_all or not gate_merge_preview),
        target_branch=target_branch,
        repo_name=repo_name,
    )
    results.append(r_merge)
    fn_log(r_merge, issue_num)

    # Gate 6 -- monolith (strangler-fig: reject server.py growth, issue #761)
    r_monolith = fn_monolith(
        issue_num,
        worktester_root,
        skip=(skip_all or not gate_monolith),
        base_branch=base_branch,
        repo_name=repo_name,
    )
    results.append(r_monolith)
    fn_log(r_monolith, issue_num)

    _REVERT_SUPPRESSED = _prev_suppressed
    return results
