"""Verified New Sprint creation — orchestration, retry, and rollback (issue #857).

New Sprint creation used to fire each step (create GitHub label, apply the label
to tickets, write the plan file) and assume success, swallowing transient GitHub
failures silently. This module runs the three steps as a verified, ordered
sequence:

  1. create the GitHub sprint label   (retried once on a transient failure)
  2. apply the label to selected tickets
  3. write the sprint plan file

Each step is *verified* after it runs (not assumed from a clean return). On a
non-recoverable failure every change made by earlier steps is rolled back, and a
``SprintCreationError`` naming the failed step and the reason is raised so the
caller can surface it loudly.

The orchestration is dependency-injected via a ``deps`` object so it stays pure
and unit-testable. ``SprintCreateDeps`` is the production adapter binding the
real ``github_client`` and the plan-file writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class SprintCreationError(Exception):
    """A sprint-creation step failed after retry/rollback.

    Carries the human-readable failed step and reason so the dialog can show a
    message like ``Failed to create GitHub label — Rate limit exceeded``.
    """

    def __init__(self, step: str, reason: str, status_code: int = 502):
        self.step = step
        self.reason = reason
        self.status_code = status_code
        self.message = f"Failed to {step} — {reason}"
        super().__init__(self.message)


def _run_step(deps, step: str, action: Callable[[], None],
              verify: Callable[[], bool], *, retry: bool) -> None:
    """Run one step, verify it took effect, retry once on a transient failure.

    Raises ``SprintCreationError`` (named for ``step``) if the action fails
    non-transiently, if the single retry also fails, or if verification reports
    the change did not take effect.
    """
    attempts = 2 if retry else 1
    for attempt in range(attempts):
        try:
            action()
        except Exception as exc:  # noqa: BLE001 — classified below
            reason = deps.describe_error(exc)
            status = deps.status_for(exc)
            if retry and attempt + 1 < attempts and deps.is_transient(exc):
                continue  # transient — retry exactly once
            raise SprintCreationError(step, reason, status)
        # Action returned cleanly — confirm the change actually landed.
        try:
            ok = verify()
        except Exception as exc:  # noqa: BLE001
            raise SprintCreationError(step, deps.describe_error(exc), deps.status_for(exc))
        if ok:
            return
        # A clean return that didn't take effect is not a transient fault.
        raise SprintCreationError(
            step, "verification failed — the change did not take effect", 502)
    # Unreachable: the loop either returns or raises.
    raise SprintCreationError(step, "exhausted retries", 502)


def create_sprint_verified(deps) -> None:
    """Execute the three creation steps with verification, retry, and rollback.

    On success all three artifacts exist. On any non-recoverable failure the
    earlier steps are rolled back and ``SprintCreationError`` is raised.
    """
    label_created = False
    tickets_labeled = False
    try:
        _run_step(deps, "create GitHub label",
                  deps.create_label, deps.label_exists, retry=True)
        label_created = True

        if deps.tickets:
            _run_step(deps, "apply sprint label to tickets",
                      deps.apply_ticket_labels, deps.ticket_labels_applied,
                      retry=False)
            tickets_labeled = True

        _run_step(deps, "write sprint plan file",
                  deps.write_plan, deps.plan_written, retry=False)
    except SprintCreationError:
        # Roll back in reverse order; rollback is best-effort and idempotent.
        if tickets_labeled:
            try:
                deps.remove_ticket_labels()
            except Exception:  # noqa: BLE001 — never mask the original failure
                pass
        if label_created:
            try:
                deps.delete_label()
            except Exception:  # noqa: BLE001
                pass
        raise


# ---------------------------------------------------------------------------
# GitHub-error classification (operates on subprocess.CalledProcessError from
# the gh CLI wrapper in github_client).
# ---------------------------------------------------------------------------
_TRANSIENT_MARKERS = (
    "rate limit",
    "timeout",
    "timed out",
    "temporarily unavailable",
    "could not resolve host",
    "connection reset",
    "connection refused",
    "503",
    "502",
    "429",
)


def _gh_stderr(exc: Exception) -> str:
    stderr = getattr(exc, "stderr", None)
    if stderr:
        return str(stderr).strip()
    return str(exc).strip()


def _is_rate_limit(exc: Exception) -> bool:
    text = _gh_stderr(exc).lower()
    return "rate limit" in text or "429" in text


def _is_transient_gh(exc: Exception) -> bool:
    text = _gh_stderr(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _describe_gh_error(exc: Exception) -> str:
    if _is_rate_limit(exc):
        return "Rate limit exceeded — try again shortly"
    text = _gh_stderr(exc)
    first_line = text.splitlines()[0] if text else exc.__class__.__name__
    return first_line or "GitHub request failed"


@dataclass
class SprintCreateDeps:
    """Production adapter binding github_client + the plan-file writer.

    ``write_plan_fn`` performs the (best-effort) plan/goal/json writes; the
    authoritative "plan file" presence is reported by ``plan_written_fn``.
    """

    github_client: object
    repo: str
    sprint_num: int
    tickets: list[int]
    write_plan_fn: Callable[[], None]
    plan_written_fn: Callable[[], bool]

    @property
    def label_name(self) -> str:
        return f"sprint-{self.sprint_num}"

    # ── Step 1: create the sprint label ──────────────────────────────────
    def create_label(self) -> None:
        self.github_client.create_sprint_label_strict(self.sprint_num, repo_name=self.repo)

    def label_exists(self) -> bool:
        return self.github_client.sprint_label_exists(self.sprint_num, repo_name=self.repo)

    # ── Step 2: apply the label to selected tickets ──────────────────────
    def apply_ticket_labels(self) -> None:
        for issue in self.tickets:
            self.github_client.update_labels(
                issue, add=[self.label_name], remove=[], repo_name=self.repo)

    def ticket_labels_applied(self) -> bool:
        for issue in self.tickets:
            labels = {l.get("name") for l in
                      self.github_client.get_issue(issue, repo_name=self.repo).get("labels", [])}
            if self.label_name not in labels:
                return False
        return True

    def remove_ticket_labels(self) -> None:
        for issue in self.tickets:
            try:
                self.github_client.update_labels(
                    issue, add=[], remove=[self.label_name], repo_name=self.repo)
            except Exception:  # noqa: BLE001 — rollback is best-effort
                pass

    # ── Step 3: write the plan file ──────────────────────────────────────
    def write_plan(self) -> None:
        self.write_plan_fn()

    def plan_written(self) -> bool:
        return bool(self.plan_written_fn())

    # ── Rollback of the label ────────────────────────────────────────────
    def delete_label(self) -> None:
        self.github_client.delete_label(self.label_name, repo_name=self.repo)

    # ── Error classification ─────────────────────────────────────────────
    def is_transient(self, exc: Exception) -> bool:
        return _is_transient_gh(exc)

    def describe_error(self, exc: Exception) -> str:
        return _describe_gh_error(exc)

    def status_for(self, exc: Exception) -> int:
        return 429 if _is_rate_limit(exc) else 502
