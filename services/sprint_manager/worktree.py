"""Worktree and environment helper functions for the sprint manager.

Contains: _resolve_uat_env_for_tester, _worktree_hygiene, _crg_update_worktree,
_stash_to_quarantine, _detect_port — extracted from sprint_manager.py (issue #1283).

sprint_manager.py re-imports all symbols so existing call sites remain
unmodified.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

# Ensure repo root is on sys.path so sibling service imports work regardless
# of invocation path.
_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"
for _p in (str(_REPO_ROOT), str(_DASHBOARD_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from services.logging import log as structured_log  # noqa: E402
from services.sprint_manager.gates import _try  # noqa: E402

# Same value as sprint_manager.REPO_ROOT — identical formula since this file
# lives at services/sprint_manager/worktree.py (three levels below repo root).
REPO_ROOT = _REPO_ROOT

_CRG_UPDATE_TIMEOUT_SECS = 120


# ── sys.modules proxy helper ──────────────────────────────────────────────────
# Deferred lookups via sys.modules avoid a circular import (worktree.py is
# imported BY sprint_manager.py) while still respecting test monkeypatches
# applied to sprint_manager attributes.

def _lookup_in_sm(attr: str, local_fn):
    """Return the sprint_manager attribute if it differs from local_fn.

    Checks both "sprint_manager" and "services.sprint_manager.sprint_manager"
    keys so that monkeypatches applied via either import path are found.
    Returns None when no patch is active so the caller uses its own fallback.
    """
    for _key in ("sprint_manager", "services.sprint_manager.sprint_manager"):
        _sm = sys.modules.get(_key)
        if _sm is not None:
            _f = getattr(_sm, attr, None)
            if _f is not None and _f is not local_fn:
                return _f
    return None


# ── private helpers (single source of truth for the whole sprint_manager pkg) ─
# sprint_manager.py re-imports these from here (issue #1502) — do not duplicate.

def _git_worktree_root(start: "Path") -> "Path | None":
    """Return the git toplevel for ``start``, or None if not inside a worktree."""
    d = start.resolve()
    for _ in range(25):
        if (d / ".git").exists():
            return d
        if d.parent == d:
            break
        d = d.parent
    return None


def _parse_dotenv_value(text: str, key: str) -> Optional[str]:
    """Return the value for ``key=`` from a dotenv file body, or None."""
    prefix = f"{key}="
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        val = line[len(prefix):].strip().strip('"').strip("'")
        return val or None
    return None


def _find_crg_bin(near: Optional[Path] = None) -> Optional[str]:
    """Return path to code-review-graph CLI if installed, else None."""
    candidates: list[Path] = []
    if near is not None:
        candidates.append(near / "venv" / "bin" / "code-review-graph")
        for parent in near.parents:
            if parent.name in ("commander", "dev") or len(candidates) > 6:
                break
            candidates.append(parent / "uat" / "venv" / "bin" / "code-review-graph")
    candidates.append(Path.home() / "dev" / "commander" / "uat" / "venv" / "bin" / "code-review-graph")
    candidates.append(Path.home() / "dev" / "commander" / "prd" / "venv" / "bin" / "code-review-graph")
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    found = shutil.which("code-review-graph")
    return found


def record_failure(*args, **kwargs):
    """Proxy to sprint_manager.record_failure, resolved at call time.

    Avoids a circular module-level import (worktree.py is imported BY
    sprint_manager.py). When sprint_manager is loaded, the real implementation
    is used. Tests that patch sm.record_failure will have their patches
    respected via the _lookup_in_sm mechanism.
    """
    _f = _lookup_in_sm("record_failure", record_failure)
    if _f is not None:
        return _f(*args, **kwargs)
    return None


# ── the five extracted functions ──────────────────────────────────────────────

def _resolve_uat_env_for_tester(
    cfg: Optional["SprintConfig"],
    tester_app_dir: Path,
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """Resolve UAT repo/port for tester dispatch (Commander + generic layouts).

    Returns ``(env_dict, error)``. *env_dict* keys: ``UAT_REPO``, ``UAT_BASE_URL``,
    ``UAT_PORT``. Called by sprint_manager so headless testers get pre-validated env
    vars and do not re-interpret ``.env`` from stale persona memory.
    """
    tester_root = _git_worktree_root(tester_app_dir) or Path(tester_app_dir)
    project_dir = tester_root.parent
    repo_name = tester_root.name

    uat_repo: Optional[Path] = None
    if (project_dir / "uat" / "apps" / "dashboard").is_dir():
        uat_repo = project_dir / "uat"
    elif (
        (project_dir / "uat").is_dir()
        and (project_dir / "uat" / ".env").is_file()
        and not (project_dir / "uat" / repo_name).is_dir()
    ):
        # Nested non-Commander layout (e.g. perf-coach): uat clone beside coder/tester.
        # Guard against a stray top-level uat/.env preempting the legacy
        # uat/<repo_name>/ layout (which is tried in the else branch below).
        uat_repo = project_dir / "uat"
    else:
        candidate = project_dir / "uat" / repo_name
        if candidate.is_dir():
            uat_repo = candidate

    if uat_repo is None:
        return None, f"UAT clone not found beside tester worktree {tester_root}"

    for rel in ("apps/dashboard/.env", "dashboard/.env", ".env"):
        uat_env_path = uat_repo / rel
        if uat_env_path.is_file():
            break
    else:
        return None, f"No .env found under {uat_repo}"

    try:
        env_text = uat_env_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"Cannot read {uat_env_path}: {exc}"

    port = _parse_dotenv_value(env_text, "PORT")
    environment = (_parse_dotenv_value(env_text, "ENVIRONMENT") or "").lower()

    if not port:
        return None, f"PORT= missing in {uat_env_path}"
    if port == "8000":
        return None, f"Refusing port 8000 (PRD) in {uat_env_path}"

    commander_ok = (
        uat_repo.resolve() == (project_dir / "uat").resolve() and port == "8001"
    )
    env_ok = environment in ("uat", "") or (
        commander_ok and environment in ("uat", "prd", "")
    )
    if not env_ok:
        return None, (
            f"{uat_env_path} has ENVIRONMENT={environment!r}; expected uat "
            f"(Commander uat/ on port 8001 also accepts legacy prd)"
        )

    base_url = f"http://localhost:{port}"
    sys.stdout.write(
        str(
            f"  [uat-env] Resolved {uat_env_path} → {base_url} "
            f"(ENVIRONMENT={environment or 'unset'}, commander_ok={commander_ok})"
        ) + "\n"
    )
    sys.stdout.flush()
    return {
        "UAT_REPO": str(uat_repo),
        "UAT_BASE_URL": base_url,
        "UAT_PORT": port,
        "COMMANDER_UAT_PREVALIDATED": "1",
    }, None


def _crg_update_worktree(worktree: Path, *, role: str = "agent") -> None:
    """Best-effort CRG graph refresh before headless agent dispatch.

    Headless ``claude -p`` does not reliably run CRG file hooks. Each worktree
    keeps its own ``.code-review-graph/`` — coder and tester must refresh
    separately. Set ``COMMANDER_SKIP_CRG_UPDATE=1`` to disable.
    """
    if os.environ.get("COMMANDER_SKIP_CRG_UPDATE", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        return
    crg = _find_crg_bin(worktree)
    if not crg:
        return
    graph_dir = worktree / ".code-review-graph"
    subcmd = "build" if not graph_dir.is_dir() else "update"
    try:
        r = subprocess.run(
            [crg, subcmd],
            capture_output=True,
            text=True,
            cwd=str(worktree),
            timeout=_CRG_UPDATE_TIMEOUT_SECS,
            check=False,
        )
        if r.returncode == 0:
            sys.stdout.write(str(f"  [crg] {subcmd} ok ({role} worktree)\n"))
        else:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            hint = tail[-1] if tail else f"exit {r.returncode}"
            sys.stdout.write(str(f"  [crg] {subcmd} failed ({role}): {hint}\n"))
        sys.stdout.flush()
    except subprocess.TimeoutExpired:
        sys.stdout.write(str(
            f"  [crg] {subcmd} timed out after {_CRG_UPDATE_TIMEOUT_SECS}s ({role}) — continuing\n"
        ))
        sys.stdout.flush()
    except Exception as exc:
        sys.stdout.write(str(f"  [crg] update skipped ({role}): {exc}\n"))
        sys.stdout.flush()


def _detect_port(cfg: "SprintConfig") -> Optional[int]:
    """Call find_port.py and return the chosen port, or None if no app section.

    AC-5: Called before coder is dispatched when app_default_port is set.
    """
    if cfg.app_default_port is None:
        return None  # non-server project: skip port detection

    find_port_script = cfg.scripts_dir / "find_port.py"
    cmd = [
        sys.executable, str(find_port_script),
        "--prefer", str(cfg.app_default_port),
        "--strategy", cfg.app_port_strategy,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        port_str = result.stdout.strip()
        chosen_port = int(port_str)
        sys.stdout.write(str(f"  [port] chosen port: {chosen_port} "
              f"(preferred: {cfg.app_default_port}, strategy: {cfg.app_port_strategy})") + "\n")
        return chosen_port
    except (subprocess.CalledProcessError, ValueError) as e:
        structured_log.warn("port_detection_failed", f"find_port.py failed: {e}", exc=str(e))
        return None


def _stash_to_quarantine(
    worktree: Path,
    ticket_id: int | str,
    effective_root: Path,
) -> None:
    """Stash dirty worktree state to quarantine before reset (issue #788 AC2).

    Saves tracked changes as tracked.patch and lists untracked files in
    untracked-list.txt.  Never overwrites an existing entry — each call creates
    a fresh timestamp subdirectory so entries accumulate (AC8).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_dir = (
        effective_root / ".commander" / "runtime" / "quarantine"
        / str(ticket_id) / ts
    )
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    sys.stdout.write(str(f"  [hygiene] WARNING: Dirty worktree for ticket #{ticket_id} — "
        f"stashing to quarantine: {quarantine_dir}") + "\n")
    sys.stdout.flush()

    ok, patch_out, _ = _try("git", "diff", "HEAD", cwd=worktree)
    if ok and patch_out:
        (quarantine_dir / "tracked.patch").write_text(patch_out, encoding="utf-8")

    ok, untracked_out, _ = _try(
        "git", "ls-files", "--others", "--exclude-standard", cwd=worktree,
    )
    if ok and untracked_out:
        (quarantine_dir / "untracked-list.txt").write_text(untracked_out, encoding="utf-8")


def _worktree_hygiene(
    worktree: Path,
    ticket_id: int | str,
    merge_target: str,
    is_retry: bool = False,
    repo_root: Optional[Path] = None,
    recover_on_rebase_conflict: bool = False,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Pre-dispatch hygiene sequence for coder/tester worktrees (issue #788).

    Steps:
      1. git fetch origin
      2. Resolve base SHA (origin/<merge_target>)
      3. Check dirty state; stash to quarantine if dirty (never discards silently)
      4. git reset --hard origin/<merge_target>
      5a. Fresh-ticket: verify feature branch absent or at base SHA; abort on divergent
      5b. Retry-round: checkout feature branch, rebase onto base; abort on conflict

    Returns (worktree_sha, base_sha, error_category).
      error_category is None on success, 'merge' on rebase conflict,
      or 'divergent-branch' if a fresh-ticket finds a divergent feature branch.

    recover_on_rebase_conflict: when True (coder path only), a 5b rebase
    conflict is NOT fatal — instead of dead-ending the ticket as 'merge', the
    stale feature branch is deleted and the worktree hard-reset to base so the
    coder rebuilds the branch fresh against the current base (mirrors 5a). This
    breaks the deadlock where a rerun-child sub-sprint forever skips a ticket
    whose stale branch overlaps another ticket's already-merged files (#1073).
    The tester path leaves this False — a tester must not delete the coder's
    just-built branch.
    """
    effective_root = repo_root or REPO_ROOT
    worktree = Path(worktree)
    sys.stdout.write(str(f"  [hygiene] Pre-dispatch hygiene for ticket #{ticket_id} in {worktree}") + "\n")
    sys.stdout.flush()

    if not worktree.is_dir():
        sys.stdout.write(str(
            f"  [hygiene] ERROR: worktree path missing ({worktree}) — cannot run hygiene"
        ) + "\n")
        sys.stdout.flush()
        return None, None, "worktree-missing"

    # 1 — fetch
    sys.stdout.write(str("  [hygiene] git fetch origin ...") + "\n")
    sys.stdout.flush()
    _try("git", "fetch", "origin", cwd=worktree)

    # 2 — resolve base SHA
    ok, base_sha, _ = _try("git", "rev-parse", f"origin/{merge_target}", cwd=worktree)
    base_sha = base_sha.strip() if ok and base_sha.strip() else None
    if not base_sha:
        sys.stdout.write(str(f"  [hygiene] WARNING: could not resolve origin/{merge_target}") + "\n")
        sys.stdout.flush()

    # 3 — dirty-state check
    ok, dirty_out, _ = _try("git", "status", "--porcelain", cwd=worktree)
    if ok and dirty_out.strip():
        _stash_to_quarantine(worktree, ticket_id, effective_root)

    # 4 — hard reset
    if base_sha:
        sys.stdout.write(str(f"  [hygiene] git reset --hard origin/{merge_target} ({base_sha[:8]})") + "\n")
        sys.stdout.flush()
        _try("git", "reset", "--hard", f"origin/{merge_target}", cwd=worktree)

    # Get worktree SHA post-reset
    ok, wt_sha, _ = _try("git", "rev-parse", "HEAD", cwd=worktree)
    worktree_sha = wt_sha.strip() if ok and wt_sha.strip() else None

    # 5 — branch validation
    # Find feature branch in this worktree
    ok, br_out, _ = _try("git", "branch", "--list", f"feature/{ticket_id}-*", cwd=worktree)
    feature_branch: Optional[str] = None
    if ok and br_out.strip():
        feature_branch = br_out.strip().splitlines()[0].strip().lstrip("* ")
    if feature_branch is None:
        ok2, br_out2, _ = _try(
            "git", "branch", "-r", "--list", f"origin/feature/{ticket_id}-*", cwd=worktree,
        )
        if ok2 and br_out2.strip():
            feature_branch = br_out2.strip().splitlines()[0].strip().removeprefix("origin/")

    if not is_retry:
        # 5a — fresh-ticket: a pre-existing feature branch at a divergent SHA is a
        # stale leftover from a prior interrupted run (a genuinely fresh ticket has
        # no branch, or one already at base). Delete the stale LOCAL branch so the
        # coder recreates it cleanly off base, instead of aborting the whole ticket
        # as 'divergent-branch' — that false abort cascaded sprint-73's
        # #928/929/932/933. Uncommitted work was already quarantine-stashed in
        # step 3; a remote-only ref (origin/...) never blocks a fresh checkout, so
        # only a local branch needs clearing and the remote is left untouched.
        if feature_branch is not None and base_sha:
            ok, branch_sha, _ = _try("git", "rev-parse", feature_branch, cwd=worktree)
            branch_sha = branch_sha.strip() if ok else None
            if branch_sha and branch_sha != base_sha:
                ok_local, _, _ = _try(
                    "git", "show-ref", "--verify", "--quiet",
                    f"refs/heads/{feature_branch}", cwd=worktree,
                )
                if ok_local:
                    detail = (
                        f"Stale feature branch {feature_branch} at {branch_sha[:8]} "
                        f"(base {base_sha[:8]}) — deleting so the coder recreates it "
                        f"fresh off base"
                    )
                    sys.stdout.write(str(f"  [hygiene] {detail}") + "\n")
                    sys.stdout.flush()
                    _try("git", "branch", "-D", feature_branch, cwd=worktree)
                    try:
                        structured_log.warn(
                            "stale_feature_branch_cleared",
                            f"deleted stale local {feature_branch} before fresh dispatch of #{ticket_id}",
                            issue_num=int(ticket_id), branch=feature_branch,
                            stale_sha=branch_sha, base_sha=base_sha,
                        )
                    except Exception:
                        pass
    else:
        # 5b — retry-round: checkout feature branch and rebase onto base
        if feature_branch is not None:
            sys.stdout.write(str(f"  [hygiene] Rebasing {feature_branch} onto origin/{merge_target}") + "\n")
            sys.stdout.flush()
            _try("git", "checkout", feature_branch, cwd=worktree)
            ok, _, rebase_err = _try(
                "git", "rebase", f"origin/{merge_target}", cwd=worktree,
            )
            if not ok:
                sys.stdout.write(str(f"  [hygiene] Rebase conflict for #{ticket_id}: {rebase_err}") + "\n")
                sys.stdout.flush()
                _try("git", "rebase", "--abort", cwd=worktree)

                if recover_on_rebase_conflict and base_sha:
                    # Self-heal (coder path): the stale feature branch can't be
                    # rebased onto the current base (typically it overlaps files
                    # already merged from another ticket). Rather than dead-end
                    # the ticket, drop the local branch and reset to base so the
                    # coder recreates it fresh — the conflicting tip stays
                    # recoverable via reflog, and the failure sidecar carries the
                    # prior-attempt context. (#1073 deadlock.)
                    detail = (
                        f"Rebase of {feature_branch} onto origin/{merge_target} "
                        f"conflicted — deleting stale branch and resetting to base "
                        f"so the coder rebuilds fresh"
                    )
                    sys.stdout.write(str(f"  [hygiene] {detail}") + "\n")
                    sys.stdout.flush()
                    _try("git", "checkout", "--detach", cwd=worktree)
                    _try("git", "branch", "-D", feature_branch, cwd=worktree)
                    reset_ok, _, reset_err = _try(
                        "git", "reset", "--hard", f"origin/{merge_target}", cwd=worktree,
                    )
                    ok_sha, wt_sha2, _ = _try("git", "rev-parse", "HEAD", cwd=worktree)
                    new_sha = wt_sha2.strip() if ok_sha and wt_sha2.strip() else None
                    # Only report a clean recovery when the worktree is verifiably
                    # ON base. If the reset failed or HEAD didn't land on base, the
                    # worktree is in an unknown state — do NOT dispatch the coder
                    # into it; fall through to the merge-failure path so the ticket
                    # is skipped rather than corrupted (returning a stale SHA here
                    # would silently run the coder against the wrong tree).
                    if reset_ok and new_sha == base_sha:
                        worktree_sha = new_sha
                        try:
                            structured_log.warn(
                                "rebase_conflict_recovered",
                                f"deleted conflicting {feature_branch} and reset to base "
                                f"before fresh dispatch of #{ticket_id}",
                                issue_num=int(ticket_id), branch=feature_branch,
                                base_sha=base_sha,
                            )
                        except Exception:
                            pass
                        return worktree_sha, base_sha, None
                    sys.stdout.write(str(
                        f"  [hygiene] recovery reset failed for #{ticket_id} "
                        f"(reset_ok={reset_ok}, head={new_sha}, base={base_sha[:8]}, "
                        f"err={reset_err!r}) — failing as merge") + "\n")
                    sys.stdout.flush()

                detail = (
                    f"Rebase of {feature_branch} onto origin/{merge_target} "
                    f"failed with conflict"
                )
                record_failure(
                    int(ticket_id),
                    "merge",
                    detail=detail,
                    repo_root=effective_root,
                )
                return worktree_sha, base_sha, "merge"

    return worktree_sha, base_sha, None
