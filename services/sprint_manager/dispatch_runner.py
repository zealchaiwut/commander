"""Sprint dispatch as a queue consumer (issue #2315).

Restored per the operator decision on #2314 (option 1). The privileged agent
spawn lives here, inside Commander, so triggering a run is an ordinary API call
rather than something the caller must elevate for.

**This is a queue consumer, not a scheduler.** It executes the tickets it is
handed, in the order it is handed them, and stops on the first failure. The
three prohibitions carried from #2311 apply verbatim:

  * never mints child sprint labels
  * never reorders tickets
  * never writes sprint lifecycle state

It also does not decide *what* to run. Ordering is the caller's, because the old
rerun's auto-ordering once queued a delete-the-tests ticket ahead of the
deletions it covered.

Stops are honoured at step boundaries only. A coder step that has pushed and
labelled SIT is a consistent state; interrupting mid-step could leave a ticket
half-labelled, which is exactly what the AC forbids.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

RUNTIME_DIRNAME = "runtime"
STEPS = ("coder", "tester")

DEFAULT_MODEL = os.environ.get("COMMANDER_DISPATCH_MODEL", "sonnet")
DEFAULT_AGENT_TIMEOUT = int(os.environ.get("COMMANDER_DISPATCH_AGENT_TIMEOUT", "3600"))

# Appended to every agent prompt. With the gate pipeline deleted, the
# instructions given to an agent are most of the quality bar that remains, so
# they are part of the dispatch contract rather than something a caller
# remembers to include (see #2316 for the objective half).
AGENT_PREAMBLE = """
Constraints for this run:
1. TEST BASELINE: {baseline_note} Pre-existing failures are NOT your problem —
   do not try to fix them. Your bar is: add no NEW failures.
2. Tests must NOT make live HTTP calls. Use TestClient in-process.
3. Do not reorder, retitle, or re-scope the ticket. Implement its acceptance
   criteria as written; if they are wrong, say so in a comment instead.
""".strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StepOutcome:
    issue: int
    step: str
    ok: bool
    detail: str = ""
    finished_at: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "issue": self.issue,
            "step": self.step,
            "ok": self.ok,
            "detail": self.detail,
            "finished_at": self.finished_at,
        }


@dataclass
class DispatchRun:
    run_id: str
    sprint_label: str
    tickets: list[int]
    repo: Optional[str] = None
    status: str = "queued"  # queued | running | done | failed | stopped
    current_issue: Optional[int] = None
    current_step: Optional[str] = None
    failed_issue: Optional[int] = None
    outcomes: list[StepOutcome] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "sprint_label": self.sprint_label,
            "tickets": list(self.tickets),
            "repo": self.repo,
            "status": self.status,
            "current_issue": self.current_issue,
            "current_step": self.current_step,
            "failed_issue": self.failed_issue,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "remaining": self.remaining(),
        }

    def remaining(self) -> list[int]:
        done = {o.issue for o in self.outcomes if o.step == "tester" and o.ok}
        return [t for t in self.tickets if t not in done]


def runtime_dir(repo_root: Path) -> Path:
    return repo_root / ".commander" / RUNTIME_DIRNAME


def run_path(run_id: str, repo_root: Path) -> Path:
    return runtime_dir(repo_root) / f"dispatch-{run_id}.json"


def stop_flag_path(run_id: str, repo_root: Path) -> Path:
    return runtime_dir(repo_root) / f"dispatch-{run_id}.stop"


def save_run(run: DispatchRun, repo_root: Path) -> Path:
    path = run_path(run.run_id, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run.to_dict(), indent=2), encoding="utf-8")
    return path


def load_run(run_id: str, repo_root: Path) -> Optional[dict]:
    path = run_path(run_id, repo_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def request_stop(run_id: str, repo_root: Path) -> bool:
    """Ask a run to stop at the next step boundary. Returns False if unknown."""
    if load_run(run_id, repo_root) is None:
        return False
    path = stop_flag_path(run_id, repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_now(), encoding="utf-8")
    return True


def stop_requested(run_id: str, repo_root: Path) -> bool:
    return stop_flag_path(run_id, repo_root).exists()


# Text an agent emits when it did not actually do the work. `claude -p` exits 0
# in these cases, so the exit code alone cannot be trusted (issue #2324).
_FAILURE_MARKERS = (
    "unknown command",
    "did you mean",
    "no such command",
)


def judge_agent_result(returncode: int, stdout: str, stderr: str) -> tuple[bool, str]:
    """Decide whether an agent run actually succeeded.

    `claude -p` exits **0** when the prompt names a command that does not exist,
    so a returncode check alone reports a no-op as a pass. Run 94914e4a8e47
    dispatched five tickets, recorded ten passing steps, and did nothing at all.

    Success is therefore read from the JSON result envelope
    (`--output-format json` gives `is_error` / `subtype` / `result`), and
    anything unparseable or ambiguous is treated as a **failure**. Unknown state
    must never resolve to success: a silent green run is worse than a loud red
    one, because nothing prompts the operator to look.
    """
    combined = ((stdout or "") + (stderr or "")).strip()
    tail = combined[-2000:]

    if returncode != 0:
        return False, f"agent exited {returncode}: {tail}"

    if not combined:
        return False, "agent produced no output"

    lowered = combined.lower()
    for marker in _FAILURE_MARKERS:
        if marker in lowered:
            return False, f"agent did not run the requested command: {tail}"

    # Parse the result envelope. The last JSON object on stdout is the result.
    envelope = None
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                envelope = json.loads(line)
                break
            except ValueError:
                continue

    if envelope is None:
        return False, f"could not parse an agent result envelope: {tail}"

    if envelope.get("is_error"):
        return False, f"agent reported is_error: {str(envelope.get('result'))[:800]}"

    if envelope.get("subtype") and envelope.get("subtype") != "success":
        return False, f"agent subtype {envelope.get('subtype')!r}: {tail}"

    return True, str(envelope.get("result", ""))[:2000]


def default_spawn(
    step: str,
    issue: int,
    repo: str,
    *,
    cwd: Path,
    baseline_note: str,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_AGENT_TIMEOUT,
) -> tuple[bool, str]:
    """Spawn a Claude Code agent for one step. The privileged call lives here."""
    url = f"https://github.com/{repo}/issues/{issue}"
    prompt = f"/{step} {url}\n\n" + AGENT_PREAMBLE.format(baseline_note=baseline_note)

    env = dict(os.environ)
    env["CLAUDE_AGENT_ROLE"] = step
    env["CLAUDE_AGENT_ISSUE"] = str(issue)

    try:
        proc = subprocess.run(
            [
                "claude", "-p", prompt,
                "--dangerously-skip-permissions",
                "--model", model,
                "--output-format", "json",
            ],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"{step} agent exceeded {timeout}s"
    except FileNotFoundError:
        return False, "claude CLI not found on PATH"

    return judge_agent_result(proc.returncode, proc.stdout or "", proc.stderr or "")


def execute_run(
    run: DispatchRun,
    *,
    repo_root: Path,
    cwd: Path,
    baseline_note: str = "run the suite and compare against the recorded baseline.",
    spawn: Optional[Callable[..., tuple[bool, str]]] = None,
) -> DispatchRun:
    """Run every ticket through coder then tester, in the order given.

    Stops on the first failed step and records which ticket failed. Dependent
    tickets are never attempted after a failure — continuing past one is how a
    broken ticket poisons the ones that build on it.
    """
    spawn = spawn or default_spawn
    run.status = "running"
    run.started_at = run.started_at or _now()
    save_run(run, repo_root)

    for issue in run.tickets:
        for step in STEPS:
            if stop_requested(run.run_id, repo_root):
                run.status = "stopped"
                run.current_issue = None
                run.current_step = None
                run.finished_at = _now()
                save_run(run, repo_root)
                return run

            run.current_issue = issue
            run.current_step = step
            save_run(run, repo_root)

            ok, detail = spawn(
                step, issue, run.repo, cwd=cwd, baseline_note=baseline_note
            )
            run.outcomes.append(StepOutcome(issue=issue, step=step, ok=ok, detail=detail))

            if not ok:
                run.status = "failed"
                run.failed_issue = issue
                run.current_issue = None
                run.current_step = None
                run.finished_at = _now()
                save_run(run, repo_root)
                return run

            save_run(run, repo_root)

    run.status = "done"
    run.current_issue = None
    run.current_step = None
    run.finished_at = _now()
    save_run(run, repo_root)
    return run


def start_run(
    sprint_label: str,
    tickets: list[int],
    *,
    repo: Optional[str],
    repo_root: Path,
    cwd: Path,
    baseline_note: str = "run the suite and compare against the recorded baseline.",
    spawn: Optional[Callable[..., tuple[bool, str]]] = None,
    background: bool = True,
) -> DispatchRun:
    """Create a run and start it. Returns the handle immediately when backgrounded.

    ``tickets`` is used exactly as given — this function does not sort, dedupe,
    or topologically order it.
    """
    run = DispatchRun(
        run_id=uuid.uuid4().hex[:12],
        sprint_label=sprint_label,
        tickets=list(tickets),
        repo=repo,
        started_at=_now(),
    )
    save_run(run, repo_root)

    if not background:
        return execute_run(
            run, repo_root=repo_root, cwd=cwd, baseline_note=baseline_note, spawn=spawn
        )

    thread = threading.Thread(
        target=execute_run,
        args=(run,),
        kwargs={
            "repo_root": repo_root,
            "cwd": cwd,
            "baseline_note": baseline_note,
            "spawn": spawn,
        },
        daemon=True,
        name=f"dispatch-{run.run_id}",
    )
    thread.start()
    return run
