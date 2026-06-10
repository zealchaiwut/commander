#!/usr/bin/env python3
"""Post a structured test report comment to a GitHub issue.

Also parses pytest / ruff failure output and emits:
  - A structured Failure Summary block appended to the comment (when --gate-output
    and --gate are provided).
  - A machine-readable JSON sidecar at
    <repo-root>/.commander/runtime/last-failure-<issue>.json

Usage:
    # Basic (existing behaviour — report file only):
    python3 scripts/post_test_report.py --issue 42 --report-file /tmp/report.md

    # With failure context (gate path):
    python3 scripts/post_test_report.py --issue 42 --report-file /tmp/report.md \\
        --gate pytest --gate-output /tmp/pytest-output.txt

    python3 scripts/post_test_report.py --issue 42 --report-file /tmp/report.md \\
        --repo owner/repo --gate lint --gate-output /tmp/ruff.txt
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_DASHBOARD_DIR = Path(__file__).parent.parent / "apps" / "dashboard"
sys.path.insert(0, str(_DASHBOARD_DIR))

from dotenv import load_dotenv
load_dotenv(_DASHBOARD_DIR / ".env")

import github_client

# ── repo root (one level up from this script: scripts/ → root) ──
_REPO_ROOT = Path(__file__).parent.parent


# ── failure parsing ───────────────────────────────────────────────────────────

def parse_pytest_failures(output: str) -> list[dict]:
    """Parse pytest -v / --tb=short output and return failure dicts.

    Each dict has keys: type, location, issue, file, line.
    """
    failures = []

    # Pattern 1 — FAILED line: "FAILED tests/test_foo.py::test_bar - AssertionError: ..."
    failed_re = re.compile(
        r"^FAILED\s+([\w./\\-]+\.py)::([\w\[\].-]+)\s+-\s+(.+)$",
        re.MULTILINE,
    )
    # Pattern 2 — file:line assertion block inside short traceback:
    #   "tests/test_foo.py:42: AssertionError"
    assert_re = re.compile(
        r"^([\w./\\-]+\.py):(\d+):\s+([\w]+(?:Error|Exception|assert\w*).*?)$",
        re.MULTILINE,
    )

    seen: set[str] = set()

    for m in failed_re.finditer(output):
        file_path = m.group(1)
        test_name = m.group(2)
        message   = m.group(3).strip()
        # Extract line number from assert_re if we can
        line_num = ""
        for am in assert_re.finditer(output):
            if am.group(1) == file_path:
                line_num = am.group(2)
                break

        location = f"{file_path}:{line_num}" if line_num else file_path
        key = f"{location}:{message[:60]}"
        if key in seen:
            continue
        seen.add(key)

        # Derive error type from message prefix
        error_type = "AssertionError"
        type_m = re.match(r"^([\w]+(?:Error|Exception))", message)
        if type_m:
            error_type = type_m.group(1)

        failures.append({
            "type":     error_type,
            "location": location,
            "issue":    message[:120],
            "file":     file_path,
            "line":     int(line_num) if line_num else None,
            "test":     test_name,
        })

    # Fallback — plain "file:line: ErrorType" lines not already captured
    for m in assert_re.finditer(output):
        file_path = m.group(1)
        line_num  = m.group(2)
        error_type = m.group(3).split(":")[0].strip()
        location  = f"{file_path}:{line_num}"
        key = f"{location}:{error_type}"
        if key in seen:
            continue
        seen.add(key)
        full_line = m.group(3).strip()
        failures.append({
            "type":     error_type,
            "location": location,
            "issue":    full_line[:120],
            "file":     file_path,
            "line":     int(line_num),
            "test":     None,
        })

    return failures


def parse_ruff_failures(output: str) -> list[dict]:
    """Parse ruff check output and return failure dicts.

    Ruff lines look like:
      server.py:42:5: E741 Ambiguous variable name `l`
    """
    failures = []
    ruff_re = re.compile(
        r"^([\w./\\-]+\.py):(\d+):\d+:\s+([\w]+)\s+(.+)$",
        re.MULTILINE,
    )
    seen: set[str] = set()
    for m in ruff_re.finditer(output):
        file_path  = m.group(1)
        line_num   = m.group(2)
        rule_code  = m.group(3)
        description = m.group(4).strip()
        location   = f"{file_path}:{line_num}"
        key        = f"{location}:{rule_code}"
        if key in seen:
            continue
        seen.add(key)
        failures.append({
            "type":     f"LintError {rule_code}",
            "location": location,
            "issue":    description[:120],
            "file":     file_path,
            "line":     int(line_num),
            "test":     None,
        })
    return failures


def parse_failures(gate: str, output: str) -> list[dict]:
    """Dispatch to the right parser based on gate name."""
    if gate == "pytest":
        return parse_pytest_failures(output)
    if gate in ("lint", "ruff"):
        return parse_ruff_failures(output)
    return []


# ── sidecar helpers ───────────────────────────────────────────────────────────

def sidecar_path(issue_num: int, repo_root: Path = _REPO_ROOT) -> Path:
    """Return path for the JSON failure sidecar file."""
    runtime_dir = repo_root / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir / f"last-failure-{issue_num}.json"


def write_sidecar(
    issue_num: int,
    gate: str,
    failures: list[dict],
    repo_root: Path = _REPO_ROOT,
) -> Path:
    """Write JSON sidecar and return its path."""
    files_to_inspect = sorted({
        f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
        for f in failures
        if f.get("file")
    })
    payload = {
        "issue":            issue_num,
        "gate":             gate,
        "run_id":           os.environ.get("COMMANDER_RUN_ID"),
        "timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "failures":         [
            {
                "type":     f["type"],
                "location": f["location"],
                "issue":    f["issue"],
                "file":     f["file"],
                "line":     f.get("line"),
            }
            for f in failures
        ],
        "files_to_inspect": files_to_inspect,
    }
    path = sidecar_path(issue_num, repo_root=repo_root)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


# ── markdown block builder ────────────────────────────────────────────────────

def build_failure_block(gate: str, failures: list[dict]) -> str:
    """Return a markdown block with Failure Summary, Recommended Fix, Files to Inspect."""
    lines = ["\n\n## Failure Summary\n"]
    lines.append("| Type | Location | Issue |")
    lines.append("|------|----------|-------|")
    for f in failures:
        lines.append(f"| {f['type']} | {f['location']} | {f['issue']} |")

    if not failures:
        lines.append("| (no structured failures parsed) | — | — |")

    # Recommended Fix prose
    lines.append("\n## Recommended Fix\n")
    if failures:
        unique_files = sorted({f["file"] for f in failures if f.get("file")})
        file_list = ", ".join(unique_files) if unique_files else "the files listed above"
        lines.append(
            f"Inspect {file_list} at the line numbers shown in the table above. "
            f"Fix the {gate} failures before re-submitting."
        )
    else:
        lines.append(f"Review the {gate} output above and fix all reported errors.")

    # Files to Inspect bulleted list
    lines.append("\n## Files to Inspect\n")
    file_entries = sorted({
        f"{f['file']}:{f['line']}" if f.get("line") else f["file"]
        for f in failures
        if f.get("file")
    })
    if file_entries:
        for entry in file_entries:
            lines.append(f"- `{entry}`")
    else:
        lines.append("- (no specific file locations parsed)")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Post a test report to a GitHub issue")
    p.add_argument("--issue",        type=int, required=True,  help="Issue number")
    p.add_argument("--report-file",  required=True,             help="Path to markdown report file")
    p.add_argument("--repo",         default=None,              help="owner/repo (auto-detected if omitted)")
    p.add_argument("--gate",         default=None,              help="Gate name (pytest|lint) for failure parsing")
    p.add_argument("--gate-output",  default=None,              help="Path to file containing gate stdout/stderr")
    p.add_argument("--repo-root",    default=None,              help="Repo root for JSON sidecar (auto-detected if omitted)")
    args = p.parse_args()

    report_path = Path(args.report_file)
    if not report_path.exists():
        sys.stderr.write(str(f"Error: report file not found: {report_path}") + "\n")
        sys.exit(1)

    body = report_path.read_text().strip()
    if not body:
        sys.stderr.write(str("Error: report file is empty") + "\n")
        sys.exit(1)

    # Parse failures and append structured block if gate info provided
    failures: list[dict] = []
    if args.gate and args.gate_output:
        gate_output_path = Path(args.gate_output)
        if not gate_output_path.exists():
            sys.stderr.write(str(f"Warning: gate output file not found: {gate_output_path}") + "\n")
        else:
            gate_text = gate_output_path.read_text(errors="replace")
            failures = parse_failures(args.gate, gate_text)

        # Append structured failure block to comment body
        body = body + build_failure_block(args.gate, failures)

        # Write JSON sidecar
        repo_root = Path(args.repo_root) if args.repo_root else _REPO_ROOT
        sidecar = write_sidecar(args.issue, args.gate, failures, repo_root=repo_root)
        sys.stdout.write(str(f"  Wrote failure sidecar: {sidecar}") + "\n")

    github_client.add_comment(args.issue, body, repo_name=args.repo)
    sys.stdout.write(str(f"Posted test report to issue #{args.issue}") + "\n")


if __name__ == "__main__":
    main()
