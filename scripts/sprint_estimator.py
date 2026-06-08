#!/usr/bin/env python3
"""Sprint Estimator — Claude Code-driven effort estimation for all sprint backlog tickets.

Runs at the START of every sprint (after sprint branch creation, before per-ticket
dispatch) to estimate effort, files impacted, and risks for each backlog ticket.
Output is saved to <sprints_dir>/sprint-<N>-estimate.json and consumed by the
dashboard Sprint Mgmt board.

## JSON contract

The output file always matches this exact shape:

```json
{
  "sprint_label": "sprint-9",
  "generated_at": "2026-05-27T12:34:56Z",
  "total_minutes": 145,
  "estimates": {
    "114": {
      "number": 114,
      "title": "...",
      "size": "M",
      "minutes": 25,
      "files": ["server.py", "static/app.js"],
      "risks": ["touches caching layer"],
      "dependencies": []
    }
  },
  "skipped": [
    {"number": 121, "reason": "already labeled done"}
  ]
}
```

`total_minutes` equals the sum of all `minutes` in `estimates`.

## Graceful degradation

- claude CLI missing: warn, write empty estimate file, exit 0 (does NOT block sprint)
- subprocess timeout or invalid JSON: log warning, write partial/empty, return what
  we have (does NOT block sprint)

Usage (standalone):
    python3 scripts/sprint_estimator.py sprint-N
    python3 scripts/sprint_estimator.py sprint-N --repo owner/repo

Usage (via sprint_manager.py):
    from sprint_estimator import run_estimator
    result = run_estimator(sprint_label, repo_name, sprints_dir, cfg=None)

Run from the git root of the repository (NOT from scripts/).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── path setup ─────────────────────────────────────────────────────────────────

SCRIPTS_DIR   = Path(__file__).parent
REPO_ROOT     = SCRIPTS_DIR.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"
SPRINTS_DIR   = DASHBOARD_DIR / "sprints"

sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(DASHBOARD_DIR / ".env")

import github_client  # noqa: E402


# ── constants ──────────────────────────────────────────────────────────────────

ESTIMATOR_TIMEOUT_SEC = int(os.environ.get("ESTIMATOR_TIMEOUT_SEC", "300"))

# Labels that indicate a ticket should be skipped for estimation
SKIP_LABELS = {"done", "UAT-approved", "closed", "needs-rework", "estimated"}


# ── data structures ────────────────────────────────────────────────────────────

@dataclass
class IssueEstimate:
    """Estimate for a single issue."""
    number:       int
    title:        str
    size:         str                    # S | M | L | XL
    minutes:      int
    files:        list[str]              = field(default_factory=list)
    risks:        list[str]              = field(default_factory=list)
    dependencies: list[int]              = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "number":       self.number,
            "title":        self.title,
            "size":         self.size,
            "minutes":      self.minutes,
            "files":        self.files,
            "risks":        self.risks,
            "dependencies": self.dependencies,
        }


@dataclass
class EstimateResult:
    """Result of estimating all backlog issues for a sprint."""
    sprint_label:    str
    generated_at:    str
    total_minutes:   int                           = 0
    estimates:       dict[int, IssueEstimate]      = field(default_factory=dict)
    skipped:         list[dict]                    = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sprint_label":  self.sprint_label,
            "generated_at":  self.generated_at,
            "total_minutes": self.total_minutes,
            "estimates":     {
                str(num): est.to_dict()
                for num, est in self.estimates.items()
            },
            "skipped":       self.skipped,
        }


# ── agent prompt ───────────────────────────────────────────────────────────────

DEFAULT_ESTIMATOR_PROMPT = """\
You are the **Sprint Estimator** agent for the Commander project.

## Your job

Estimate effort, files impacted, and risks for EVERY issue listed below in ONE pass.

## Sprint context

Sprint label: {sprint_label}
Repository: {repo}

## Issues to estimate

{issues_json}

## For EACH issue, you must

1. Read the full issue body via: `gh issue view <N> --repo {repo} --json number,title,body,labels`
2. Scan the codebase for likely-impacted files using grep/find on AC keywords:
   - Look for file mentions in the issue body
   - Use `grep -r "<keyword>" --include="*.py" --include="*.js" --include="*.html" -l .`
     where <keyword> is a key function name, endpoint path, or class mentioned in AC
3. Estimate size and minutes:
   - S (< 1 hour): 1–45 minutes. Apply 20% buffer.
   - M (1–3 hours): 60–180 minutes. Apply 20% buffer.
   - L (3–8 hours): 180–480 minutes. Apply 20% buffer.
   - XL (> 8 hours): 480+ minutes. Apply 20% buffer.
   - Apply 30% buffer instead of 20% when AC is thin (< 3 checkbox items) or vague.
   - Include 2–5 likely-impacted files per ticket as relative paths (relative to repo root).
4. Flag risks (zero or more):
   - "DB migration" — any schema change, new table, new column
   - "public API change" — adds/removes/changes an endpoint
   - "thin AC — high uncertainty" — fewer than 3 AC checkbox items or vague requirements
   - "depends on #N" — explicitly depends on another issue
   - "new dependency" — adds a package to requirements.txt or package.json
   - "security-sensitive" — touches auth, tokens, permissions, secrets, input validation
   - "large diff" — estimated > 300 lines changed
   - "cross-subsystem" — touches both frontend and backend
5. Comment on EACH issue with the estimate:
   ```
   gh issue comment <N> --repo {repo} --body "**Estimate:** <size> · ~<minutes> min

   **Files likely impacted:** <file1>, <file2>
   **Risks:** <risk1>, <risk2> (or 'none')
   **Dependencies:** #N (or 'none')

   _Generated by sprint estimator_"
   ```
6. Apply the `estimated` label to EACH issue (create label first if missing):
   ```
   gh label create estimated --repo {repo} --color "e4e669" --force
   gh issue edit <N> --repo {repo} --add-label estimated
   ```
   ⚠️  LABEL SAFETY: Use ONLY `--add-label`. NEVER use `gh issue edit --label "..."` (the bare
   `--label` flag REPLACES ALL existing labels and will destroy sprint/size/type labels).

## Output

After processing ALL issues, output a SINGLE JSON object to stdout with this exact shape:

```json
{{
  "sprint_label": "{sprint_label}",
  "generated_at": "<ISO 8601 UTC timestamp>",
  "total_minutes": <sum of all minutes>,
  "estimates": {{
    "<issue_number_as_string>": {{
      "number": <int>,
      "title": "<string>",
      "size": "<S|M|L|XL>",
      "minutes": <int>,
      "files": ["<relative_path>", ...],
      "risks": ["<risk_string>", ...],
      "dependencies": [<int>, ...]
    }}
  }},
  "skipped": []
}}
```

IMPORTANT:
- Output ONLY the JSON object — no preamble, no explanation, no markdown wrapper.
- `total_minutes` MUST equal the sum of all `minutes` values in `estimates`.
- Issue numbers as string keys in `estimates` (e.g. "114", not 114).
- If an issue already has the `estimated` label, still include it in estimates (re-estimate).
- `skipped` should be empty — you are processing all issues passed to you.
"""


# ── issue fetching ─────────────────────────────────────────────────────────────

def _fetch_backlog_for_estimation(
    sprint_label: str,
    repo_name: Optional[str],
) -> tuple[list[dict], list[dict]]:
    """Fetch open backlog issues for estimation.

    Returns (issues_to_estimate, skipped_issues).

    Fetches all open issues with the sprint label; skips any already labeled
    done, UAT-approved, closed, needs-rework, or estimated.
    """
    r = github_client._r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo",  r,
                "--label", sprint_label,
                "--state", "open",
                "--json",  "number,title,labels,body",
                "--limit", "200",
            ],
            capture_output=True, text=True, check=True,
        )
        all_issues = json.loads(out.stdout)
    except Exception as e:
        print(f"[estimator] Warning: could not fetch issues — {e}", file=sys.stderr)
        return [], []

    to_estimate = []
    skipped = []
    for issue in all_issues:
        labels = {lbl["name"] for lbl in issue.get("labels", [])}
        matched = labels & SKIP_LABELS
        if matched:
            reason = f"already labeled {next(iter(matched))}"
            skipped.append({"number": issue["number"], "reason": reason})
        else:
            to_estimate.append(issue)

    return sorted(to_estimate, key=lambda i: i["number"]), skipped


# ── agent subprocess ───────────────────────────────────────────────────────────

def _spawn_estimator_agent(
    issues_json: str,
    repo: str,
    sprint_label: str,
    repo_path: Path,
) -> Optional[EstimateResult]:
    """Spawn a single claude CLI call to estimate all issues in one pass.

    Strips ANTHROPIC_API_KEY from subprocess env.
    Respects ESTIMATOR_TIMEOUT_SEC (default 300s).
    Returns a parsed EstimateResult on success, None on failure.
    """
    prompt = DEFAULT_ESTIMATOR_PROMPT.format(
        sprint_label=sprint_label,
        repo=repo,
        issues_json=issues_json,
    )

    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    try:
        proc = subprocess.run(
            [
                "claude",
                "--model", "claude-sonnet-4-6",
                "--dangerously-skip-permissions",
                "-p", prompt,
            ],
            capture_output=True,
            text=True,
            timeout=ESTIMATOR_TIMEOUT_SEC,
            env=env,
            cwd=str(repo_path),
        )
        stdout = proc.stdout.strip()
    except subprocess.TimeoutExpired:
        print(
            f"\n  [estimator] WARNING: estimator agent timed out after {ESTIMATOR_TIMEOUT_SEC}s",
            file=sys.stderr,
        )
        return None
    except FileNotFoundError:
        print(
            "[estimator] WARNING: claude CLI not found — skipping estimation",
            file=sys.stderr,
        )
        return None

    # Extract JSON object from output (model may add prose around it)
    json_match = re.search(r"\{.*\}", stdout, re.DOTALL)
    if not json_match:
        print(
            f"\n  [estimator] WARNING: estimator agent returned no JSON\n"
            f"              stdout: {stdout[:300]!r}",
            file=sys.stderr,
        )
        return None

    try:
        raw = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        print(
            f"\n  [estimator] WARNING: estimator JSON parse error ({exc})",
            file=sys.stderr,
        )
        return None

    return _parse_estimate_result(raw, sprint_label)


def _parse_estimate_result(raw: dict, sprint_label: str) -> EstimateResult:
    """Parse raw JSON dict into an EstimateResult dataclass."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    generated_at = raw.get("generated_at", now_utc)
    skipped = raw.get("skipped", [])

    estimates: dict[int, IssueEstimate] = {}
    raw_estimates = raw.get("estimates", {})
    for key, val in raw_estimates.items():
        try:
            num = int(key)
        except (ValueError, TypeError):
            continue
        if not isinstance(val, dict):
            continue
        est = IssueEstimate(
            number       = val.get("number", num),
            title        = val.get("title", ""),
            size         = val.get("size", "M"),
            minutes      = int(val.get("minutes", 60)),
            files        = val.get("files", []),
            risks        = val.get("risks", []),
            dependencies = val.get("dependencies", []),
        )
        estimates[num] = est

    total_minutes = sum(e.minutes for e in estimates.values())

    return EstimateResult(
        sprint_label  = raw.get("sprint_label", sprint_label),
        generated_at  = generated_at,
        total_minutes = total_minutes,
        estimates     = estimates,
        skipped       = skipped,
    )


# ── atomic file write ──────────────────────────────────────────────────────────

def _write_estimate_file(result: EstimateResult, sprints_dir: Path) -> Path:
    """Write estimate JSON atomically (tmp → rename) and return the path."""
    sprints_dir.mkdir(parents=True, exist_ok=True)

    # Extract sprint number from label like "sprint-9" → "9"
    m = re.search(r"(\d+)", result.sprint_label)
    n = m.group(1) if m else result.sprint_label

    out_path = sprints_dir / f"sprint-{n}-estimate.json"
    tmp_path = out_path.with_suffix(".json.tmp")

    data = result.to_dict()
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(out_path)

    return out_path


# ── main entry point ───────────────────────────────────────────────────────────

def run_estimator(
    sprint_label: str,
    repo_name: Optional[str] = None,
    sprints_dir: Optional[Path] = None,
    cfg: Optional[object] = None,
) -> EstimateResult:
    """Run the sprint estimator and return an EstimateResult.

    This is the importable entry point used by sprint_manager.py.

    Arguments:
        sprint_label  -- e.g. "sprint-9"
        repo_name     -- "owner/repo" override (default: auto-detect from git remote)
        sprints_dir   -- where to write the output JSON
        cfg           -- optional SprintConfig (used to resolve paths)

    Graceful degradation:
    - claude CLI missing → warn, write empty file, return EstimateResult with estimates={}
    - timeout or invalid JSON → warn, write partial/empty, return what we have
    """
    effective_sprints_dir = sprints_dir or SPRINTS_DIR
    effective_repo = repo_name or (cfg.repo_name if cfg else None)
    repo = github_client._r(effective_repo)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Check for claude CLI early so we can degrade gracefully
    if not shutil.which("claude"):
        print(
            "[estimator] claude CLI not found — writing empty estimate file, proceeding with sprint",
            file=sys.stderr,
        )
        empty = EstimateResult(
            sprint_label  = sprint_label,
            generated_at  = now_utc,
            total_minutes = 0,
            estimates     = {},
            skipped       = [],
        )
        out_path = _write_estimate_file(empty, effective_sprints_dir)
        print(f"[estimator] Empty estimate written to {out_path}")
        return empty

    # Fetch backlog issues
    print(f"[estimator] Fetching backlog for {sprint_label!r} from {repo} ...")
    to_estimate, pre_skipped = _fetch_backlog_for_estimation(sprint_label, effective_repo)

    if not to_estimate:
        print(f"[estimator] No estimable issues found for {sprint_label!r}.")
        empty = EstimateResult(
            sprint_label  = sprint_label,
            generated_at  = now_utc,
            total_minutes = 0,
            estimates     = {},
            skipped       = pre_skipped,
        )
        out_path = _write_estimate_file(empty, effective_sprints_dir)
        print(f"[estimator] Estimate written to {out_path}")
        return empty

    print(f"[estimator] Estimating {len(to_estimate)} issue(s) for {sprint_label!r} ...")

    # Build compact issue list for the prompt
    issues_for_agent = [
        {
            "number": i["number"],
            "title":  i["title"],
            "body":   (i.get("body") or "")[:2000],
        }
        for i in to_estimate
    ]

    # Determine repo root for cwd in subprocess
    repo_root = REPO_ROOT

    # Spawn estimator agent
    result = _spawn_estimator_agent(
        json.dumps(issues_for_agent, indent=2),
        repo,
        sprint_label,
        repo_root,
    )

    if result is None:
        # Graceful degradation: agent failed — write partial/empty
        print("[estimator] WARNING: agent failed — writing partial estimate", file=sys.stderr)
        partial = EstimateResult(
            sprint_label  = sprint_label,
            generated_at  = now_utc,
            total_minutes = 0,
            estimates     = {},
            skipped       = pre_skipped,
        )
        out_path = _write_estimate_file(partial, effective_sprints_dir)
        print(f"[estimator] Partial estimate written to {out_path}")
        return partial

    # Merge pre-skipped into agent result
    if pre_skipped:
        result.skipped = pre_skipped + result.skipped

    # Write output file atomically
    out_path = _write_estimate_file(result, effective_sprints_dir)
    print(
        f"[estimator] Estimate complete: {len(result.estimates)} estimated, "
        f"{len(result.skipped)} skipped, {result.total_minutes} total minutes"
    )
    print(f"[estimator] Estimate written to {out_path}")

    return result


# ── CLI helpers ────────────────────────────────────────────────────────────────

def _resolve_sprints_dir(cfg_sprints_dir: Path, commander_dir: Path) -> Path:
    """Return the correct sprints directory, correcting double .commander paths.

    Same logic as sprint_review.py: when the resolved path contains a
    '.commander/.commander' segment, fall back to commander_dir / sprints.
    """
    resolved = cfg_sprints_dir.resolve()
    parts = resolved.parts
    for i, part in enumerate(parts):
        if part == ".commander" and i + 1 < len(parts) and parts[i + 1] == ".commander":
            canonical = commander_dir / "sprints"
            print(
                f"  [estimator] Corrected double .commander path: "
                f"{resolved} → {canonical}",
                file=sys.stderr,
            )
            return canonical
    return resolved


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Sprint Estimator: estimate effort, files, and risks "
            "for all backlog tickets in a sprint using Claude Code."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "sprint_label",
        metavar="<sprint-label>",
        help="GitHub sprint label to estimate (e.g. sprint-9)",
    )
    p.add_argument(
        "--repo",
        default=None,
        metavar="owner/repo",
        help="GitHub repo override (default: auto-detect from git remote)",
    )
    p.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "Path to .commander/sprint.yaml config file. "
            "When provided, sprints_dir and repo are read from it."
        ),
    )

    args = p.parse_args()

    sprints_dir: Optional[Path] = None
    repo_name: Optional[str] = args.repo

    # Lazy import to avoid circular dependency
    try:
        from sprint_manager import discover_config, load_config  # noqa: PLC0415
        _sm_available = True
    except Exception:
        _sm_available = False

    def _try_load_config(path: Path) -> Optional[object]:
        try:
            return load_config(path)  # type: ignore[possibly-undefined]
        except SystemExit as e:
            print(
                f"  Warning: could not load config {path} — {e}; "
                "using commander default sprints directory.",
                file=sys.stderr,
            )
            return None

    if _sm_available:
        if args.config:
            config_path = Path(args.config).expanduser().resolve()
            if not config_path.exists():
                p.error(f"Config file not found: {config_path}")
            print(f"  Using config: {config_path}")
            cfg = _try_load_config(config_path)
            if cfg is not None:
                sprints_dir = _resolve_sprints_dir(cfg.sprints_dir, config_path.parent)
                if not repo_name:
                    repo_name = cfg.repo_name
        else:
            discovered = discover_config()  # type: ignore[possibly-undefined]
            if discovered:
                print(f"  Auto-discovered config: {discovered}")
                cfg = _try_load_config(discovered)
                if cfg is not None:
                    sprints_dir = _resolve_sprints_dir(cfg.sprints_dir, discovered.parent)
                    if not repo_name:
                        repo_name = cfg.repo_name

    run_estimator(
        sprint_label = args.sprint_label,
        repo_name    = repo_name,
        sprints_dir  = sprints_dir,
    )


if __name__ == "__main__":
    main()
