"""pytest/lint gate functions for the sprint manager.

Contains: _gate_pytest, _gate_lint, _lint_autofix_commit, _run_frontend_lint,
_changed_py_files, _changed_js_ts_files, _changed_frontend_files, and their
supporting constants — extracted from sprint_manager.py (issue #1280).

sprint_manager.py re-imports and re-exports all symbols so existing call sites
remain unmodified.
"""
from __future__ import annotations

import subprocess
import sys
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
    _sm = sys.modules.get("sprint_manager") or sys.modules.get(
        "services.sprint_manager.sprint_manager"
    )
    if _sm is not None:
        _f = getattr(_sm, "_run_timed", None)
        if _f is not None and _f is not _run_timed:
            return _f(*cmd, cwd=cwd)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout, r.stderr


def _try(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    _sm = sys.modules.get("sprint_manager") or sys.modules.get(
        "services.sprint_manager.sprint_manager"
    )
    if _sm is not None:
        _f = getattr(_sm, "_try", None)
        if _f is not None and _f is not _try:
            return _f(*cmd, cwd=cwd)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


# ── _revert_to_sit proxy ──────────────────────────────────────────────────────
# _revert_to_sit has deep dependencies (github_client, record_failure, etc.)
# that are not yet extracted. A sys.modules lookup at call time avoids a
# circular import AND respects monkeypatching in tests that patch
# "sprint_manager._revert_to_sit" (the same pattern used for _run_timed).

def _revert_to_sit(
    issue_num: int,
    gate_name: str,
    output: str,
    repo_name: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> None:
    _sm = sys.modules.get("sprint_manager") or sys.modules.get(
        "services.sprint_manager.sprint_manager"
    )
    if _sm is not None:
        _f = getattr(_sm, "_revert_to_sit", None)
        if _f is not None and _f is not _revert_to_sit:
            return _f(issue_num, gate_name, output,
                      repo_name=repo_name, repo_root=repo_root)
    from services.sprint_manager import sprint_manager as _sm_pkg  # noqa: PLC0415
    return _sm_pkg._revert_to_sit(
        issue_num, gate_name, output,
        repo_name=repo_name, repo_root=repo_root,
    )


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

    # Detect pytest binary
    ok, pytest_path, _ = _try("which", "pytest")
    if not ok:
        # Try inside the tester worktree venv (root-level, not apps/dashboard)
        venv_pytest = wt_root / "venv" / "bin" / "pytest"
        if venv_pytest.exists():
            pytest_bin = str(venv_pytest.resolve())
        else:
            output = "pytest binary not found on PATH and no venv/bin/pytest found."
            structured_log.error("gate_failed", f"[gate:pytest] FAIL: {output}", gate="pytest", issue_num=issue_num)
            return GateResult(gate="pytest", passed=False, output=output)
    else:
        pytest_bin = pytest_path

    # Determine which test files to run based on gate_scope
    if gate_scope == "full":
        sys.stdout.write(str("  [gate:pytest] running pytest -x (full scope) ...") + "\n")
        rc, stdout, stderr = _run_timed(pytest_bin, "-x", cwd=worktester_dashboard)
    else:
        # changed scope: only run test files changed relative to base_branch
        # git diff paths are relative to repo root; run pytest from root so
        # the paths resolve correctly (root-level tests/ is not under apps/dashboard).
        changed = _changed_py_files(base_branch, cwd=worktester_dashboard)
        test_files = [f for f in changed if f.startswith("tests/")]
        if not test_files:
            sys.stdout.write(str("  [gate:pytest] no test files changed — skipped") + "\n")
            return GateResult(gate="pytest", passed=True, output="no test files changed")
        sys.stdout.write(str(f"  [gate:pytest] checking {len(test_files)} file(s): {', '.join(test_files)}") + "\n")
        # Paths from git diff are relative to the git root, not worktester_dashboard.
        # Run pytest from the git root so tests/ paths resolve correctly.
        rc_root, git_root_out, _ = _run_timed(
            "git", "rev-parse", "--show-toplevel", cwd=worktester_dashboard,
        )
        pytest_cwd = Path(git_root_out.strip()) if rc_root == 0 else worktester_dashboard
        rc, stdout, stderr = _run_timed(pytest_bin, "-x", *test_files, cwd=pytest_cwd)

    combined = stdout + stderr
    if rc == 0:
        sys.stdout.write(str("  [gate:pytest] PASS") + "\n")
        return GateResult(gate="pytest", passed=True, output=combined)
    else:
        structured_log.error("gate_failed", f"[gate:pytest] FAIL (exit {rc})", gate="pytest", issue_num=issue_num, exit_code=rc)
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
                sys.stdout.write(str(f"  [gate:lint] ruff checking {len(py_files)} file(s): {', '.join(py_files)}") + "\n")
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
