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
import shlex
import subprocess
import sys
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
    sprint_branch: Optional[str] = None  # sprint/sprint-N when branch model is active (#2329)
    sprint_pr_number: Optional[int] = None  # PR opened from sprint branch → develop

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
            "sprint_branch": self.sprint_branch,
            "sprint_pr_number": self.sprint_pr_number,
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


@dataclass
class ProjectDispatchConfig:
    """Per-step dispatch settings read from a project's .commander/sprint.yaml.

    Cross-project dispatch cannot use commander's `/coder` and `/tester` slash
    commands: those live in commander's own `.claude/commands/`, and a target
    repo has no such directory. Run 94914e4a8e47 failed for exactly that reason
    (issue #2325). Each project already declares plain prompt templates, and a
    separate worktree per step, so read them.
    """

    repo_name: str
    coder_prompt: str
    tester_prompt: str
    coder_worktree: Path
    tester_worktree: Path
    coder_model: Optional[str] = None

    def prompt_for(self, step: str, issue_url: str) -> str:
        template = self.coder_prompt if step == "coder" else self.tester_prompt
        return template.replace("{issue_url}", issue_url)

    def worktree_for(self, step: str) -> Path:
        return self.coder_worktree if step == "coder" else self.tester_worktree


class DispatchConfigError(RuntimeError):
    """Raised when a project cannot be dispatched. Never falls back silently."""


def load_project_config(project_root: Path) -> ProjectDispatchConfig:
    """Load dispatch settings for a project, or fail loudly.

    A missing config, template, or worktree raises. It must never degrade into
    a default that silently does nothing — that is the failure mode #2325 exists
    to remove.
    """
    import yaml

    from services.sprint_manager.commander_paths import discover_commander_dir

    commander_dir = discover_commander_dir(project_root)
    path = commander_dir / "sprint.yaml"
    if not path.exists():
        raise DispatchConfigError(
            f"no sprint.yaml found for {project_root} (looked in {commander_dir}) — "
            "dispatch needs the project's prompt templates and worktrees"
        )

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise DispatchConfigError(f"could not parse {path}: {exc}") from exc

    agents = data.get("agents") or {}
    worktrees = data.get("worktrees") or {}
    agent_config = data.get("agent_config") or {}

    missing = [
        key for key, val in (
            ("agents.coder_prompt_template", agents.get("coder_prompt_template")),
            ("agents.tester_prompt_template", agents.get("tester_prompt_template")),
            ("worktrees.coder", worktrees.get("coder")),
            ("worktrees.tester", worktrees.get("tester")),
        ) if not val
    ]
    if missing:
        raise DispatchConfigError(f"{path} is missing: {', '.join(missing)}")

    return ProjectDispatchConfig(
        repo_name=data.get("repo_name", ""),
        coder_prompt=agents["coder_prompt_template"],
        tester_prompt=agents["tester_prompt_template"],
        coder_worktree=Path(worktrees["coder"]),
        tester_worktree=Path(worktrees["tester"]),
        coder_model=agent_config.get("coder_model"),
    )


def build_agent_env(step: str, issue: int) -> dict:
    """Environment for a dispatched `claude -p` subprocess.

    Strips ANTHROPIC_API_KEY so the CLI authenticates via the Claude.ai
    subscription rather than metered API billing. CLAUDE.md's pricing table says
    coder and tester runs are subscription-funded and cost $0.00; inheriting the
    key silently contradicts that. Six other agent-spawning call sites already do
    this — startup.py, backlog_triage.py, estimate_issue.py, model_routing.py,
    fill_acceptance_criteria.py and sprint_estimator.py — and dispatch was the
    only one that did not (issue #2334).

    The symptom when it is inherited: `claude -p` exits with api_error_status 400
    and "Credit balance is too low", because the configured key is unfunded. That
    is the benign outcome — a funded key would have billed every dispatched agent
    against a policy that says they are free, with no signal anywhere.

    Returns a copy; the parent process environment is never modified.
    """
    env = dict(os.environ)
    env.pop("ANTHROPIC_API_KEY", None)
    # Hook telemetry attributes the session from these; without them a run lands
    # unattributed and the Running view cannot show it.
    env["CLAUDE_AGENT_ROLE"] = step
    env["CLAUDE_AGENT_ISSUE"] = str(issue)
    return env


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
    prompt: Optional[str] = None,
    model: Optional[str] = None,
    timeout: int = DEFAULT_AGENT_TIMEOUT,
) -> tuple[bool, str]:
    """Spawn a Claude Code agent for one step. The privileged call lives here.

    ``prompt`` comes from the project's sprint.yaml. There is deliberately **no
    fallback** to a `/coder` slash command: those exist only inside commander,
    and falling back to one is what made run 94914e4a8e47 silently do nothing
    across five tickets (issue #2325).
    """
    if not prompt:
        raise DispatchConfigError(
            f"no prompt template supplied for the {step} step — refusing to "
            "guess. Check the project's .commander/sprint.yaml."
        )

    model = model or DEFAULT_MODEL
    prompt = prompt.rstrip() + "\n\n" + AGENT_PREAMBLE.format(baseline_note=baseline_note)

    env = build_agent_env(step, issue)

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


def _default_verify(*, step: str, issue: int, repo, report: str) -> tuple[bool, str]:
    """Real ticket verification against GitHub (issue #2328).

    Kept as a module-level default so tests can inject a stub, and so a caller
    can pass ``verify=None`` to skip verification entirely (e.g. dry runs).
    """
    from services.sprint_manager.ticket_verify import verify_step

    def _labels(issue_num: int, repo_name):
        import github_client

        data = github_client.get_issue_live(issue_num, repo_name=repo_name)
        return [lbl["name"] for lbl in data.get("labels", [])]

    def _open_pr(issue_num: int, repo_name):
        """Number of an open PR whose branch belongs to this issue, if any."""
        import subprocess

        try:
            out = subprocess.run(
                ["gh", "pr", "list", "--repo", repo_name or "", "--state", "open",
                 "--json", "number,headRefName"],
                capture_output=True, text=True, timeout=60,
            )
            if out.returncode != 0:
                return None
            for pr in json.loads(out.stdout or "[]"):
                head = str(pr.get("headRefName", ""))
                if head.startswith(f"feature/{issue_num}-") or f"-{issue_num}" == head[-len(str(issue_num)) - 1:]:
                    return pr.get("number")
        except Exception:
            return None
        return None

    return verify_step(
        step=step, issue=issue, repo=repo, report=report,
        fetch_labels=_labels, fetch_open_pr=_open_pr,
    )


def _gate_sprint_pr(
    *, sprint_branch: str, target: str = "develop", repo: str, cwd: Path, **kw
) -> tuple[bool, str]:
    """Baseline-delta check for the sprint → develop PR (AC #2316 + #2329).

    Runs the suite on the sprint branch in `cwd` and compares against the
    recorded develop baseline.  Returns (allowed, reason).  Called by
    _open_sprint_pr before `gh pr create` so neither merge type bypasses the
    gate (per AC4: "the baseline-delta check runs … against develop for the
    final sprint merge").

    Kill-switch: COMMANDER_BASELINE_CHECK=0 skips and returns True.
    """
    from services.sprint_manager.commander_paths import discover_commander_dir
    from services.sprint_manager.merge_baseline import (
        check_against_baseline,
        collection_failed,
        load_baseline,
        parse_errored_test_ids,
        parse_failed_test_ids,
    )
    from services.sprint_manager.suite_health_gate import _parse_pytest_output

    if os.environ.get("COMMANDER_BASELINE_CHECK", "1") == "0":
        return True, "Baseline-delta check disabled (COMMANDER_BASELINE_CHECK=0)"

    try:
        commander_dir = discover_commander_dir(cwd)
    except Exception as exc:
        return False, f"could not locate .commander dir from {cwd}: {exc}"

    baseline = load_baseline(repo or "", commander_dir)
    if baseline is None:
        # AC4 of #2329: "Neither merge may bypass it." Skipping here would be a
        # bypass — and not a hypothetical one: per-ticket merges can be waved
        # through with --override-baseline, so "the per-ticket gate already
        # checked" does not hold. #2316's rule is that an unmeasured project is
        # not a safe one, and a missing baseline refuses.
        return False, (
            f"no baseline recorded for {repo} — record one with "
            f"scripts/record_test_baseline.py before the sprint PR can open"
        )

    # shlex, not str.split: since #2339 a recorded baseline carries
    # `tests/ -q -m "not live_http"`, and str.split would hand pytest the
    # three fragments `-m`, `"not`, `live_http"` — a broken -m expression that
    # aborts the run, which this gate would then report as a refusal.
    pytest_args = shlex.split(baseline.pytest_args or 'tests/ -q -m "not live_http"')
    # Same default as finish_feature.py. The suite measures 741.96s on this
    # repo, so 900 sat close enough to the edge that a slow run would time out
    # and read as a refusal.
    timeout = int(os.environ.get("COMMANDER_BASELINE_TIMEOUT", "1800"))

    try:
        result = subprocess.run(
            # sys.executable, not "python3": the system python3 is 3.9 on
            # zeal-server and cannot even import this codebase. finish_feature.py
            # already does this.
            [sys.executable, "-m", "pytest"] + pytest_args,
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return False, f"baseline-delta check timed out after {timeout}s on {sprint_branch}"
    except Exception as exc:
        return False, f"baseline-delta check could not run: {exc}"

    failed_ids = parse_failed_test_ids(output)
    error_ids = parse_errored_test_ids(output)
    # Use the shared parser. A local `re.search(r"(\d+) failed", output)` takes
    # the FIRST match anywhere in the blob, and commander's suite runs pytest as
    # a subprocess in places, so their captured output carries counts of their
    # own. That exact inline form reported passed=5 failed=999 for a run that was
    # 7279/2377 (issue #2331); _parse_pytest_output reads the last real summary
    # line instead. The command run here is `pytest tests/ -q` — precisely the
    # invocation that misparsed.
    _counts = _parse_pytest_output(output)
    passed_now, failed_now, errored_now = (
        _counts.passed, _counts.failed, _counts.errors
    )

    check = check_against_baseline(
        failed_now=failed_now,
        failing_test_ids_now=failed_ids,
        baseline=baseline,
        passed_now=passed_now,
        collection_error=collection_failed(output),
        errored_now=errored_now,
        erroring_test_ids_now=error_ids,
    )
    return check.allowed, check.summary()


def _open_sprint_pr(run: DispatchRun, *, cwd: Path) -> Optional[int]:
    """Open a PR from sprint/sprint-N into develop (issue #2329).

    Runs the baseline-delta check (_gate_sprint_pr) against develop first so
    that neither merge type bypasses AC #2316 (AC4 of #2329).  Returns the PR
    number on success, None when the gate refuses or on any other failure.
    Failures are non-fatal: the sprint is done and the tickets have merged;
    only the sprint PR is missing.
    """
    branch = run.sprint_branch
    repo = run.repo
    if not branch or not repo:
        return None

    gate_ok, _gate_msg = _gate_sprint_pr(
        sprint_branch=branch, target="develop", repo=repo, cwd=cwd,
    )
    if not gate_ok:
        return None

    try:
        label = run.sprint_label
        result = subprocess.run(
            [
                "gh", "pr", "create",
                "--repo", repo,
                "--head", branch,
                "--base", "develop",
                "--title", f"Sprint {label} → develop",
                "--body", (
                    f"Merges all tickets from **{label}** into develop.\n\n"
                    f"Sprint branch: `{branch}`\n"
                    f"Dispatch run: `{run.run_id}`\n\n"
                    f"Review and merge when ready to bring the sprint into develop."
                ),
            ],
            capture_output=True, text=True, cwd=str(cwd), timeout=60,
        )
        if result.returncode == 0:
            # gh pr create prints the PR URL; extract the number from the tail.
            url = result.stdout.strip().splitlines()[-1].strip()
            m = __import__("re").search(r"/pull/(\d+)$", url)
            return int(m.group(1)) if m else None
    except Exception:
        pass
    return None


def execute_run(
    run: DispatchRun,
    *,
    repo_root: Path,
    cwd: Path,
    baseline_note: str = "run the suite and compare against the recorded baseline.",
    spawn: Optional[Callable[..., tuple[bool, str]]] = None,
    config: Optional[ProjectDispatchConfig] = None,
    verify: Optional[Callable[..., tuple[bool, str]]] = _default_verify,
) -> DispatchRun:
    """Run every ticket through coder then tester, in the order given.

    Stops on the first failed step and records which ticket failed. Dependent
    tickets are never attempted after a failure — continuing past one is how a
    broken ticket poisons the ones that build on it.

    When a sprint branch is active (``run.sprint_branch`` is set), opens a PR
    from ``sprint/sprint-N`` into ``develop`` after all tickets succeed (#2329).
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

            # Per-step prompt, worktree and model come from the project's
            # sprint.yaml. coder and tester have separate clones (#2325).
            step_cwd = config.worktree_for(step) if config else cwd
            step_prompt = (
                config.prompt_for(step, f"https://github.com/{run.repo}/issues/{issue}")
                if config else None
            )
            step_model = config.coder_model if (config and step == "coder") else None

            ok, detail = spawn(
                step, issue, run.repo,
                cwd=step_cwd,
                baseline_note=baseline_note,
                prompt=step_prompt,
                model=step_model,
            )

            # An agent that ran cleanly has not necessarily moved the ticket
            # (issue #2328). Check its verdict and the board before believing it.
            if ok and verify is not None:
                advanced, why = verify(step=step, issue=issue, repo=run.repo, report=detail)
                if not advanced:
                    ok = False
                    detail = f"agent completed but ticket did not advance: {why}\n\n{detail}"

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

    # Sprint-branch model: all tickets merged into sprint/sprint-N.  Open
    # the one PR that brings the sprint into develop (#2329).
    if run.sprint_branch:
        step_cwd = config.worktree_for("coder") if config else cwd
        pr_num = _open_sprint_pr(run, cwd=step_cwd)
        run.sprint_pr_number = pr_num

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
    config: Optional[ProjectDispatchConfig] = None,
    verify: Optional[Callable[..., tuple[bool, str]]] = _default_verify,
    background: bool = True,
    sprint_branch: Optional[str] = None,
) -> DispatchRun:
    """Create a run and start it. Returns the handle immediately when backgrounded.

    ``tickets`` is used exactly as given — this function does not sort, dedupe,
    or topologically order it.

    ``sprint_branch`` is set by the dispatch endpoint after creating
    ``sprint/sprint-N`` (#2329); when set, execute_run opens a PR from the
    sprint branch into develop after all tickets succeed.
    """
    run = DispatchRun(
        run_id=uuid.uuid4().hex[:12],
        sprint_label=sprint_label,
        tickets=list(tickets),
        repo=repo,
        started_at=_now(),
        sprint_branch=sprint_branch,
    )
    save_run(run, repo_root)

    if not background:
        return execute_run(
            run, repo_root=repo_root, cwd=cwd, baseline_note=baseline_note,
            spawn=spawn, config=config, verify=verify,
        )

    thread = threading.Thread(
        target=execute_run,
        args=(run,),
        kwargs={
            "repo_root": repo_root,
            "cwd": cwd,
            "baseline_note": baseline_note,
            "spawn": spawn,
            "config": config,
            "verify": verify,
        },
        daemon=True,
        name=f"dispatch-{run.run_id}",
    )
    thread.start()
    return run
