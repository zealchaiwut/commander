"""AI-powered branch conflict resolution service.

Spawns 'claude -p' in a local git clone to resolve merge conflicts,
streaming progress via SSE (same job/subscriber pattern as finish_progress_service).
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
if str(_DASHBOARD_ROOT) not in sys.path:
    sys.path.insert(0, str(_DASHBOARD_ROOT))

# ── Job store ─────────────────────────────────────────────────────────────────

_RESOLVE_JOBS: dict[str, dict] = {}
_RESOLVE_SUBS: dict[str, list[asyncio.Queue]] = {}


def job_key(owner: str, repo_name: str, head: str) -> str:
    safe_head = head.replace("/", "_").replace("\\", "_")
    return f"resolve:{safe_head}@{owner}/{repo_name}"


def get_snapshot(key: str) -> dict | None:
    return _RESOLVE_JOBS.get(key)


def is_running(key: str) -> bool:
    snap = _RESOLVE_JOBS.get(key)
    return snap is not None and snap.get("status") == "running"


def subscribe(key: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    _RESOLVE_SUBS.setdefault(key, []).append(q)
    return q


def unsubscribe(key: str, q: asyncio.Queue) -> None:
    subs = _RESOLVE_SUBS.get(key, [])
    if q in subs:
        subs.remove(q)


async def _emit(key: str, snapshot: dict) -> None:
    _RESOLVE_JOBS[key] = snapshot
    for q in list(_RESOLVE_SUBS.get(key, [])):
        try:
            q.put_nowait(snapshot)
        except asyncio.QueueFull:
            pass
    await asyncio.sleep(0)


def _server():
    import server as _srv  # noqa: PLC0415
    return _srv


# ── Clone discovery ───────────────────────────────────────────────────────────

def _find_clone_dir(repo: str) -> Path | None:
    """Find a local git clone of the repo, preferring the coder clone."""
    project_root = _server()._project_root_path(repo)
    # Nested layout: prefer coder (sprint branches live there), fall back to uat/main
    for name in ("coder", "uat", "main", "tester"):
        candidate = project_root / name
        if (candidate / ".git").exists():
            return candidate
    # Flat layout: project_root itself is the main clone
    if (project_root / ".git").exists():
        return project_root
    # Flat layout sibling directories
    slug = repo.split("/")[-1]
    for suffix in ("-coder", "-uat", ""):
        candidate = project_root.parent / f"{slug}{suffix}"
        if (candidate / ".git").exists():
            return candidate
    return None


# ── Claude prompt ─────────────────────────────────────────────────────────────

def _build_prompt(head: str, base: str, clone_dir: Path) -> str:
    return f"""You are resolving a git merge conflict in the repository at: {clone_dir}

Goal: make branch '{head}' cleanly mergeable into '{base}' by merging '{base}' into '{head}' and resolving all conflicts.

Steps:
1. git -C {clone_dir} fetch origin
2. git -C {clone_dir} checkout -B ai-resolve-work origin/{head}
3. git -C {clone_dir} merge origin/{base}
   — If conflicts exist the merge stops with conflict markers in files.
4. For each file that has conflict markers (<<<<<<<, =======, >>>>>>>):
   — Read the file, understand both sides, resolve each conflict intelligently.
   — Prefer keeping both changes where they are complementary.
   — Never leave raw conflict markers in the final file.
5. git -C {clone_dir} add -A
6. git -C {clone_dir} commit -m "fix: resolve merge conflicts — {base} merged into {head}"
7. git -C {clone_dir} push origin ai-resolve-work:{head}

After step 7 the GitHub pull request for {head} → {base} will be conflict-free.
If the merge produces no conflicts, still commit and push so the remote branch is up to date.
"""


# ── Background task ───────────────────────────────────────────────────────────

async def run_resolve_conflict(key: str, repo: str, head: str, base: str) -> None:
    log_tail: list[str] = []

    def _log(msg: str) -> None:
        log_tail.append(msg)
        if len(log_tail) > 200:
            log_tail.pop(0)

    snapshot: dict = {
        "status": "running",
        "mode": "indeterminate",
        "current": "Locating local clone…",
        "log_tail": [],
    }
    await _emit(key, snapshot)

    try:
        clone_dir = await asyncio.to_thread(_find_clone_dir, repo)
        if clone_dir is None:
            raise RuntimeError(f"No local git clone found for {repo}")

        _log(f"Clone: {clone_dir}")
        snapshot = {**snapshot, "current": "Launching AI resolver…", "log_tail": list(log_tail)}
        await _emit(key, snapshot)

        claude_bin = shutil.which("claude") or "/Users/zeal-server/.local/bin/claude"
        prompt = _build_prompt(head, base, clone_dir)

        import os as _os
        from services.sprint_manager.model_routing import apply_provider_env
        _env = {k: v for k, v in _os.environ.items() if k != "ANTHROPIC_API_KEY"}
        _resolver_model = apply_provider_env(_env, "claude-sonnet-4-6", repo=repo)
        cmd = [
            claude_bin,
            "--model", _resolver_model,
            "--allowedTools", "Bash,Read,Edit,Write",
            "--max-turns", "40",
            "-p", prompt,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(clone_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_env,
        )

        async for raw_line in proc.stdout:
            line = raw_line.decode(errors="replace").rstrip()
            if not line:
                continue
            _log(line)
            snapshot = {**snapshot, "current": line[:120], "log_tail": list(log_tail)}
            await _emit(key, snapshot)

        rc = await proc.wait()

        if rc == 0:
            _log("AI resolver finished (exit 0)")
            await _emit(key, {
                "status": "done",
                "mode": "indeterminate",
                "current": "Conflicts resolved",
                "result": f"Branch {head} resolved — ready to merge into {base}",
                "log_tail": list(log_tail),
            })
        else:
            _log(f"AI resolver exited with code {rc}")
            await _emit(key, {
                "status": "error",
                "mode": "indeterminate",
                "error": f"AI resolver failed (exit {rc}) — check log for details",
                "log_tail": list(log_tail),
            })

    except Exception as exc:
        _log(f"Fatal: {exc}")
        await _emit(key, {
            "status": "error",
            "mode": "indeterminate",
            "error": str(exc),
            "log_tail": list(log_tail),
        })
