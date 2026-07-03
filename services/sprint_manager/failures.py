"""Failure handling helpers for sprint_manager.

Contains: FailureCategory, record_failure, _build_failure_suffix,
_generate_gate_failure_analysis, _publish_gate_failure_analyses, and their
private supporting helpers extracted from sprint_manager.py (issue #1279).
sprint_manager.py re-imports and re-exports them so all existing call sites
remain unmodified.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# Replicate constants that the functions need; they resolve to the same values
# as sprint_manager.py because the repo layout is identical.
REPO_ROOT     = Path(__file__).parent.parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

# Make github_client (in apps/dashboard) importable AND ensure dashboard wins the
# `config` name over services/sprint_manager/config.py: github_client does
# `from config import ...`, and a test that prepends the package dir to sys.path
# otherwise shadows dashboard's config and breaks collection. Keep DASHBOARD_DIR
# at the front. (Live runs already resolve config to dashboard's, so this is a
# no-op there.)
while str(DASHBOARD_DIR) in sys.path:
    sys.path.remove(str(DASHBOARD_DIR))
sys.path.insert(0, str(DASHBOARD_DIR))

import github_client  # noqa: E402
from services.logging import log as structured_log  # noqa: E402

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig


# ── failure categories ────────────────────────────────────────────────────────

class FailureCategory:
    HANG             = "HANG"
    CRASH            = "CRASH"
    GATE_FAIL        = "GATE_FAIL"
    TESTER_REJECTED  = "TESTER_REJECTED"
    RETRY_EXHAUSTED  = "RETRY_EXHAUSTED"
    # Fine-grained logic failure categories (issue #239)
    CODER_NO_WORK    = "CODER_NO_WORK"
    MERGE_CONFLICT   = "MERGE_CONFLICT"
    LINT_FAIL        = "LINT_FAIL"
    PYTEST_FAIL      = "PYTEST_FAIL"
    # Merge sequencing issue — not a code quality problem, no coder requeue (issue #1414)
    REBASE_CONFLICT  = "REBASE_CONFLICT"
    # Environment / infra failure (e.g. pytest binary missing in the worktree) — not
    # a code defect, so intentionally kept OUT of _LOGIC_FAILURE_CATEGORIES (no coder
    # requeue, no needs-rework).
    ENV_ERROR        = "ENV_ERROR"


def _build_failure_suffix(issue_num: int, repo_root: Optional[Path] = None) -> str:
    """Read the JSON failure sidecar for issue_num and return a prompt suffix.

    Handles both the new unified schema (failure_class / detail) and the legacy
    gate schema (gate / failures list) written by write_sidecar.

    Returns an empty string when no sidecar exists.
    """
    effective_root = repo_root or REPO_ROOT
    # Resolve sidecar path independently of _FAILURE_PARSING_AVAILABLE
    sc_path = effective_root / ".commander" / "runtime" / f"last-failure-{issue_num}.json"

    if not sc_path.exists():
        sys.stdout.write(str(f"  [retry] failure sidecar not found at {sc_path} — using generic prompt") + "\n")
        return ""

    try:
        data = json.loads(sc_path.read_text(encoding="utf-8"))
    except Exception as e:
        structured_log.warn(
            "failure_sidecar_read_error",
            f"could not read failure sidecar: {e}",
            sidecar_path=str(sc_path),
            exc=str(e),
        )
        return ""

    failure_class    = data.get("failure_class")
    summary          = data.get("summary", "")
    detail           = data.get("detail", "")
    files_to_inspect = data.get("files_to_inspect", [])

    # Legacy schema: gate + structured failures list
    gate     = data.get("gate", "")
    failures = data.get("failures", [])

    lines: list[str] = []

    if failure_class:
        # New unified schema
        lines.append(f"\n\nPrevious failure class: {failure_class}.")
        if summary:
            lines.append(f"Summary: {summary}")
        if detail:
            lines.append(f"\nDetail:\n{detail}")
    elif failures:
        # Legacy gate schema
        lines.append(f"\n\nPrevious gate '{gate}' failed. Fix the following before re-submitting:")
        for f in failures:  # no cap — all failures surfaced
            loc   = f.get("location", "")
            ftype = f.get("type", "")
            msg   = f.get("issue", "")
            test  = f.get("test", "")
            entry = f"- {ftype} at {loc}: {msg}"
            if test:
                entry += f" (test: {test})"
            lines.append(entry)
    else:
        return ""

    if files_to_inspect:
        lines.append("\nFiles requiring changes:")
        for fi in files_to_inspect:
            lines.append(f"  {fi}")

    return "\n".join(lines)


def record_failure(
    issue_num: int,
    failure_class: str,
    detail: str,
    repo_root: Optional[Path] = None,
    summary: Optional[str] = None,
    files_to_inspect: Optional[list] = None,
    log_tail: Optional[list] = None,
) -> Optional[Path]:
    """Write a JSON failure sidecar for any failure class.

    Works independently of _FAILURE_PARSING_AVAILABLE — uses a direct path
    construction so it never depends on the optional post_test_report import.

    `log_tail` is a list of the last N lines of agent stdout/stderr, stored
    as a structured field for hang-redispatch context (issue #787).

    Returns the sidecar path on success, None on write error.
    """
    effective_root = repo_root or REPO_ROOT
    try:
        runtime_dir = effective_root / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc_path = runtime_dir / f"last-failure-{issue_num}.json"

        payload = {
            "issue":            issue_num,
            "failure_class":    failure_class,
            "summary":          summary or f"Issue #{issue_num}: {failure_class} failure",
            "detail":           detail,
            "files_to_inspect": files_to_inspect or [],
            "log_tail":         log_tail or [],
            "run_id":           os.environ.get("COMMANDER_RUN_ID"),
            "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        sc_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        sys.stdout.write(str(f"  [failure] Wrote sidecar ({failure_class}): {sc_path}") + "\n")
        sys.stdout.flush()
        structured_log.info(
            "sidecar_written", f"failure sidecar written for #{issue_num}",
            issue_num=issue_num, failure_class=failure_class, path=str(sc_path),
        )
        return sc_path
    except Exception as e:
        structured_log.error(
            "record_failure_error",
            f"failed to write failure sidecar for issue #{issue_num}: {e}",
            issue_num=issue_num,
            failure_class=failure_class,
            exc=str(e),
        )
        return None


# ── gate failure analysis (issue #701) ────────────────────────────────────────

def _gate_failures_log_path(cfg: Optional["SprintConfig"] = None) -> Path:
    logs_dir = cfg.logs_dir if cfg is not None else (DASHBOARD_DIR / "logs")
    return logs_dir / "gate-failures.md"


def _gate_failure_records_path(issue_num: int, repo_root: Optional[Path] = None) -> Path:
    effective_root = repo_root or REPO_ROOT
    return effective_root / ".commander" / "runtime" / f"gate-failure-records-{issue_num}.jsonl"


def _read_gate_failure_records(
    issue_num: int,
    repo_root: Optional[Path] = None,
) -> list[dict]:
    """Read all gate failure records for issue_num from the JSONL sidecar."""
    path = _gate_failure_records_path(issue_num, repo_root)
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        structured_log.warn(
            "gate_record_read_error",
            f"could not read gate failure records for #{issue_num}: {e}",
            issue_num=issue_num,
            exc=str(e),
        )
    return records


def _clear_gate_failure_records(
    issue_num: int,
    repo_root: Optional[Path] = None,
) -> None:
    """Delete the gate failure JSONL sidecar for issue_num if it exists."""
    path = _gate_failure_records_path(issue_num, repo_root)
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        structured_log.warn(
            "gate_record_clear_error",
            f"could not clear gate failure records for #{issue_num}: {e}",
            issue_num=issue_num,
            exc=str(e),
        )


def _extract_analysis_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from LLM analysis output."""
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*", "", cleaned)
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _post_gate_failure_analysis_comment(
    issue_num: int,
    gate_name: str,
    error_output: str,
    root_cause: str,
    prevention: str,
    repo_name: Optional[str] = None,
) -> None:
    """Post a structured ## Gate Failure Analysis comment to the GitHub issue."""
    comment = (
        f"## Gate Failure Analysis\n\n"
        f"### Gate & Error\n\n"
        f"**Gate:** {gate_name}\n\n"
        f"```\n{error_output}\n```\n\n"
        f"### Root Cause\n\n{root_cause}\n\n"
        f"### Prevention\n\n{prevention}\n"
    )
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        structured_log.warn(
            "gate_analysis_comment_error",
            f"failed to post Gate Failure Analysis for #{issue_num}: {e}",
            issue_num=issue_num,
            gate=gate_name,
            exc=str(e),
        )


def _append_gate_failure_to_sprint_log(
    issue_num: int,
    gate_name: str,
    error_output: str,
    root_cause: str,
    prevention: str,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Append a dated Gate Failure Analysis entry to the sprint gate failures log."""
    log_path = _gate_failures_log_path(cfg)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = (
        f"\n## Gate Failure Analysis — {timestamp} — Issue #{issue_num}\n\n"
        f"### Gate & Error\n\n"
        f"**Gate:** {gate_name}\n\n"
        f"```\n{error_output}\n```\n\n"
        f"### Root Cause\n\n{root_cause}\n\n"
        f"### Prevention\n\n{prevention}\n"
    )
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        structured_log.warn(
            "gate_log_append_error",
            f"failed to append Gate Failure Analysis to sprint log for #{issue_num}: {e}",
            issue_num=issue_num,
            gate=gate_name,
            exc=str(e),
        )


def _generate_gate_failure_analysis(
    gate_name: str,
    error_output: str,
    issue_num: int = 0,
    cfg: Optional["SprintConfig"] = None,
) -> dict:
    """Call claude -p (Haiku) to generate root cause + prevention for a gate failure.

    Returns {"root_cause": "...", "prevention": "..."}.
    Returns placeholder strings on any error so the sprint never blocks.
    """
    prompt = (
        f"A quality gate named '{gate_name}' failed with this error output:\n\n"
        f"```\n{error_output[:3000]}\n```\n\n"
        "Respond with a JSON object with exactly two keys:\n"
        '- "root_cause": one concise sentence explaining WHY the submitted code '
        "failed this gate (be specific, e.g. \"Type annotation missing on return "
        "value of `process_item`\")\n"
        '- "prevention": one or two concrete actionable steps the coder should '
        "take before next submission to pass this gate (e.g. \"Run `tsc --noEmit` "
        "locally before marking complete\")\n\n"
        "Output ONLY the JSON object, no other text."
    )
    # Respect the configured model (issue #708) — fall back to the hardcoded
    # default only when cfg is unavailable, so this stays consistent with the
    # per-agent model config added in #700.
    model = cfg.reviewer_model if cfg is not None else "claude-haiku-4-5-20251001"
    try:
        import os as _os  # noqa: PLC0415
        from services.sprint_manager.model_routing import apply_provider_env  # noqa: PLC0415
        _env = _os.environ.copy()
        model = apply_provider_env(
            _env, model, cfg=cfg, repo=_os.environ.get("COMMANDER_PROJECT"),
        )
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", model,
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=_env,
        )
        if result.returncode == 0:
            data = _extract_analysis_json(result.stdout)
            if data and "root_cause" in data and "prevention" in data:
                return {"root_cause": str(data["root_cause"]), "prevention": str(data["prevention"])}
    except Exception as e:
        structured_log.warn(
            "gate_analysis_llm_error",
            f"LLM gate analysis failed for #{issue_num} ({gate_name}): {e}",
            issue_num=issue_num,
            gate=gate_name,
            exc=str(e),
        )
    return {
        "root_cause": f"Code did not satisfy the {gate_name} gate requirements.",
        "prevention": (
            f"Review the {gate_name} gate error output and fix all reported issues "
            "before resubmitting."
        ),
    }


def _publish_gate_failure_analyses(
    issue_num: int,
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Post Gate Failure Analysis comment + sprint log entry for each recorded gate failure.

    Called after the fix-loop is exhausted (all retries consumed or early-abort).
    Reads gate failure records accumulated by _write_gate_failure_record (called
    from _revert_to_sit on each gate failure), processes each independently
    (AC-7: no merging), then clears the records.
    """
    # Lazy import so that existing monkeypatches on sm.* continue to work in
    # tests (same pattern as events.py; avoids circular-import at load time).
    import services.sprint_manager.sprint_manager as _sm  # noqa: PLC0415
    records = _sm._read_gate_failure_records(issue_num)
    if not records:
        return
    for record in records:
        gate_name = record.get("gate_name", "unknown")
        error_output = record.get("output", "")
        analysis = _sm._generate_gate_failure_analysis(
            gate_name, error_output, issue_num=issue_num, cfg=cfg
        )
        _sm._post_gate_failure_analysis_comment(
            issue_num,
            gate_name,
            error_output,
            analysis["root_cause"],
            analysis["prevention"],
            repo_name=repo_name,
        )
        _sm._append_gate_failure_to_sprint_log(
            issue_num,
            gate_name,
            error_output,
            analysis["root_cause"],
            analysis["prevention"],
            cfg=cfg,
        )
    _sm._clear_gate_failure_records(issue_num)
