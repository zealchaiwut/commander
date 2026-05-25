#!/usr/bin/env python3
"""Sprint Manager — orchestrates coder and tester agents for sprint issues,
with a post-tester quality gate pipeline before auto-merging to develop.

Quality gates (pytest → lint → merge-preview) run after a tester subprocess
exits 0 and the issue has advanced to the UAT label. Any gate failure reverts
the issue to SIT with a detailed comment.

After sprint completion a rich executive summary is written to
~/commander/dashboard/sprints/sprint-<N>-summary-<YYYY-MM-DD>.md, a GitHub
issue is created for permanent record, and an optional interactive learnings
prompt is shown when stdout is a TTY.

Adds per-failure categorisation, hang detection, configurable alert channels,
a sprint summary report, restart/resume from state, and live dashboard progress.

Usage:
    python3 ~/commander/dashboard/scripts/sprint_manager.py <label> [options]

Examples:
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --skip-gates
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --gate-pytest=false
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --alert-mode dashboard-banner,file
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --resume
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --retry-failed
    python3 ~/commander/dashboard/scripts/sprint_manager.py sprint-5 --dry-run

Run from the git root of the repository (NOT from dashboard/).
"""
from __future__ import annotations

import argparse
import email.mime.text
import json
import os
import re
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import yaml  # PyYAML — already in requirements.txt
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

# ── path setup ────────────────────────────────────────────────────────────────

SCRIPTS_DIR   = Path(__file__).parent
DASHBOARD_DIR = SCRIPTS_DIR.parent

sys.path.insert(0, str(DASHBOARD_DIR))
from dotenv import load_dotenv
load_dotenv(DASHBOARD_DIR / ".env")
import github_client

# Import failure-parsing helpers from post_test_report (no circular deps)
try:
    from scripts.post_test_report import (  # type: ignore[import]
        parse_failures,
        build_failure_block,
        write_sidecar,
        sidecar_path,
    )
    _FAILURE_PARSING_AVAILABLE = True
except ImportError:
    _FAILURE_PARSING_AVAILABLE = False

# Repo root — one level above DASHBOARD_DIR (e.g. ~/commander/)
REPO_ROOT = DASHBOARD_DIR.parent

# Default paths — can be overridden via env vars or CLI for testing
WORKTESTER_ROOT      = Path(os.environ.get("WORKTESTER_ROOT",
                             Path.home() / "commander" / "work-tester"))
WORKTESTER_DASHBOARD = WORKTESTER_ROOT / "dashboard"
FINISH_FEATURE_SCRIPT = DASHBOARD_DIR / "scripts" / "finish_feature.py"
DASHBOARD_API_URL    = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")
SPRINTS_DIR          = DASHBOARD_DIR / "sprints"
ALERTS_DIR           = DASHBOARD_DIR / "alerts"


# ── SprintConfig dataclass + loader ──────────────────────────────────────────

@dataclass
class SprintConfig:
    """All runtime paths and settings for a sprint.

    Replaces the six module-level path constants.  When no config file is
    present the class is populated with the same env-var + hardcoded defaults
    that existed before this ticket, so backward compatibility is preserved.
    """
    repo_name:             Optional[str]  = None
    worktree_coder:        Path           = field(default_factory=lambda: Path.home() / "commander" / "work-coder")
    worktree_tester:       Path           = field(default_factory=lambda: WORKTESTER_ROOT)
    tester_app_subdir:     str            = "dashboard"
    scripts_dir:           Path           = field(default_factory=lambda: DASHBOARD_DIR / "scripts")
    logs_dir:              Path           = field(default_factory=lambda: DASHBOARD_DIR / "logs")
    sprints_dir:           Path           = field(default_factory=lambda: SPRINTS_DIR)
    alerts_dir:            Path           = field(default_factory=lambda: ALERTS_DIR)
    api_url:               str            = field(default_factory=lambda: DASHBOARD_API_URL)
    coder_prompt_template:  Optional[str] = None
    tester_prompt_template: Optional[str] = None
    # Port detection (issue #62)
    app_default_port:      Optional[int]  = None
    app_port_strategy:     str            = "prefer_default"

    @property
    def worktree_tester_app(self) -> Path:
        """Resolved path where tests/app lives inside the tester worktree."""
        if self.tester_app_subdir:
            return self.worktree_tester / self.tester_app_subdir
        return self.worktree_tester

    @property
    def finish_feature_script(self) -> Path:
        return self.scripts_dir / "finish_feature.py"


def _resolve_path(raw: str, base_dir: Path) -> Path:
    """Expand ~ and resolve relative paths against base_dir."""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


def load_config(path: Path) -> "SprintConfig":
    """Parse .commander/sprint.yaml and return a SprintConfig.

    Relative paths in paths.* are resolved relative to the YAML file's
    directory.  Raises SystemExit on validation errors.
    """
    if yaml is None:
        sys.exit("PyYAML is not installed. Install it with: pip install pyyaml")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        sys.exit(f"Cannot read config file {path}: {e}")

    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        sys.exit(f"YAML parse error in {path}: {e}")

    base_dir = path.parent  # directory containing sprint.yaml

    # ── required fields ───────────────────────────────────────────────────────
    missing = []
    repo_name = (data.get("repo_name") or "").strip()
    if not repo_name:
        missing.append("repo_name")

    wt = data.get("worktrees") or {}
    coder_raw  = (wt.get("coder") or "").strip()
    tester_raw = (wt.get("tester") or "").strip()
    if not coder_raw:
        missing.append("worktrees.coder")
    if not tester_raw:
        missing.append("worktrees.tester")

    if missing:
        sys.exit(
            f"Config file {path} is missing required field(s): "
            + ", ".join(missing)
        )

    worktree_coder  = _resolve_path(coder_raw, base_dir)
    worktree_tester = _resolve_path(tester_raw, base_dir)
    tester_app_subdir = (wt.get("tester_app_subdir") or "")

    # ── validate worktree paths ────────────────────────────────────────────────
    path_errors = []
    if not worktree_coder.exists():
        path_errors.append(f"worktrees.coder path does not exist: {worktree_coder}")
    if not worktree_tester.exists():
        path_errors.append(f"worktrees.tester path does not exist: {worktree_tester}")
    if path_errors:
        sys.exit("Config validation error:\n  " + "\n  ".join(path_errors))

    # ── optional paths ────────────────────────────────────────────────────────
    paths = data.get("paths") or {}

    scripts_raw = (paths.get("scripts_dir") or "").strip()
    scripts_dir = _resolve_path(scripts_raw, base_dir) if scripts_raw else DASHBOARD_DIR / "scripts"

    logs_raw  = (paths.get("logs_dir") or "").strip()
    logs_dir  = _resolve_path(logs_raw, base_dir) if logs_raw else base_dir / "logs"

    sprints_raw  = (paths.get("sprints_dir") or "").strip()
    sprints_dir  = _resolve_path(sprints_raw, base_dir) if sprints_raw else base_dir / "sprints"

    alerts_raw  = (paths.get("alerts_dir") or "").strip()
    alerts_dir  = _resolve_path(alerts_raw, base_dir) if alerts_raw else base_dir / "alerts"

    # ── dashboard section ─────────────────────────────────────────────────────
    dashboard = data.get("dashboard") or {}
    api_url   = (dashboard.get("api_url") or DASHBOARD_API_URL).strip()

    # ── agents section ────────────────────────────────────────────────────────
    agents = data.get("agents") or {}
    coder_prompt   = agents.get("coder_prompt_template") or None
    tester_prompt  = agents.get("tester_prompt_template") or None

    # ── app section (issue #62: per-project port detection) ───────────────────
    app_section = data.get("app") or {}
    app_default_port: Optional[int] = None
    app_port_strategy: str = "prefer_default"
    if app_section:
        raw_port = app_section.get("default_port")
        if raw_port is not None:
            try:
                app_default_port = int(raw_port)
            except (TypeError, ValueError):
                sys.exit(f"Config error: app.default_port must be an integer, got {raw_port!r}")
        raw_strategy = (app_section.get("port_strategy") or "prefer_default").strip()
        if raw_strategy not in ("prefer_default", "always_random"):
            sys.exit(
                f"Config error: app.port_strategy must be 'prefer_default' or 'always_random', "
                f"got {raw_strategy!r}"
            )
        app_port_strategy = raw_strategy

    return SprintConfig(
        repo_name             = repo_name,
        worktree_coder        = worktree_coder,
        worktree_tester       = worktree_tester,
        tester_app_subdir     = tester_app_subdir,
        scripts_dir           = scripts_dir,
        logs_dir              = logs_dir,
        sprints_dir           = sprints_dir,
        alerts_dir            = alerts_dir,
        api_url               = api_url,
        coder_prompt_template = coder_prompt,
        tester_prompt_template= tester_prompt,
        app_default_port      = app_default_port,
        app_port_strategy     = app_port_strategy,
    )


def discover_config(start_dir: Optional[Path] = None) -> Optional[Path]:
    """Walk up from start_dir looking for .commander/sprint.yaml.

    Returns the Path if found, None otherwise.
    """
    if start_dir is None:
        start_dir = Path.cwd()
    current = start_dir.resolve()
    while True:
        candidate = current / ".commander" / "sprint.yaml"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:  # reached filesystem root
            break
        current = parent
    return None


def _default_config() -> "SprintConfig":
    """Build a SprintConfig from env-vars + hardcoded defaults (backward compat)."""
    return SprintConfig(
        repo_name         = None,  # will use github_client.repo()
        worktree_coder    = Path.home() / "commander" / "work-coder",
        worktree_tester   = WORKTESTER_ROOT,
        tester_app_subdir = "dashboard",
        scripts_dir       = DASHBOARD_DIR / "scripts",
        logs_dir          = DASHBOARD_DIR / "logs",
        sprints_dir       = SPRINTS_DIR,
        alerts_dir        = ALERTS_DIR,
        api_url           = DASHBOARD_API_URL,
    )


# Hang detection constants (in seconds)
HANG_WARN_SECS  = 30 * 60   # 30 minutes
HANG_KILL_SECS  = 60 * 60   # 60 minutes
HANG_CHECK_SECS = 5  * 60   # check every 5 minutes


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sprint_number(label: str) -> Optional[int]:
    m = re.search(r"(\d+)", label)
    return int(m.group(1)) if m else None


def _state_path(
    sprint_number: Optional[int],
    sprint_label: str,
    cfg: Optional["SprintConfig"] = None,
) -> Path:
    sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    n = sprint_number if sprint_number is not None else sprint_label
    return sprints_dir / f"sprint-{n}-state.json"


def _summary_path(
    sprint_number: Optional[int],
    sprint_label: str,
    cfg: Optional["SprintConfig"] = None,
) -> Path:
    sprints_dir = cfg.sprints_dir if cfg is not None else SPRINTS_DIR
    sprints_dir.mkdir(parents=True, exist_ok=True)
    n   = sprint_number if sprint_number is not None else sprint_label
    day = datetime.now().strftime("%Y-%m-%d")
    return sprints_dir / f"sprint-{n}-summary-{day}.md"


# ── failure categories ────────────────────────────────────────────────────────

class FailureCategory:
    HANG             = "HANG"
    CRASH            = "CRASH"
    GATE_FAIL        = "GATE_FAIL"
    TESTER_REJECTED  = "TESTER_REJECTED"
    RETRY_EXHAUSTED  = "RETRY_EXHAUSTED"


# ── alert modes ───────────────────────────────────────────────────────────────

class AlertMode:
    DASHBOARD_BANNER = "dashboard-banner"
    EMAIL            = "email"
    DISCORD          = "discord"
    FILE             = "file"
    NONE             = "none"

    ALL_MODES = {DASHBOARD_BANNER, EMAIL, DISCORD, FILE, NONE}


# ── data structures ───────────────────────────────────────────────────────────

@dataclass
class IssueState:
    number:      int
    title:       str
    status:      str              = "pending"   # pending | done | skipped
    skip_reason: Optional[str]   = None
    category:    Optional[str]   = None         # FailureCategory value
    tokens_in:   int             = 0
    tokens_out:  int             = 0

    def to_dict(self) -> dict:
        return {
            "number":      self.number,
            "title":       self.title,
            "status":      self.status,
            "skip_reason": self.skip_reason,
            "category":    self.category,
            "tokens_in":   self.tokens_in,
            "tokens_out":  self.tokens_out,
        }

    @staticmethod
    def from_dict(d: dict) -> "IssueState":
        return IssueState(
            number      = d["number"],
            title       = d["title"],
            status      = d.get("status", "pending"),
            skip_reason = d.get("skip_reason"),
            category    = d.get("category"),
            tokens_in   = d.get("tokens_in", 0),
            tokens_out  = d.get("tokens_out", 0),
        )


@dataclass
class SprintState:
    sprint_label:     str
    sprint_number:    Optional[int]
    issues:           list[IssueState]  = field(default_factory=list)
    start_timestamp:  str              = ""
    total_tokens_in:  int              = 0
    total_tokens_out: int              = 0
    wall_clock_secs:  float            = 0.0

    def to_dict(self) -> dict:
        return {
            "sprint_label":     self.sprint_label,
            "sprint_number":    self.sprint_number,
            "issues":           [i.to_dict() for i in self.issues],
            "start_timestamp":  self.start_timestamp,
            "total_tokens_in":  self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "wall_clock_secs":  self.wall_clock_secs,
        }

    @staticmethod
    def from_dict(d: dict) -> "SprintState":
        s = SprintState(
            sprint_label     = d["sprint_label"],
            sprint_number    = d.get("sprint_number"),
            start_timestamp  = d.get("start_timestamp", ""),
            total_tokens_in  = d.get("total_tokens_in", 0),
            total_tokens_out = d.get("total_tokens_out", 0),
            wall_clock_secs  = d.get("wall_clock_secs", 0.0),
        )
        s.issues = [IssueState.from_dict(i) for i in d.get("issues", [])]
        return s

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))


@dataclass
class GateResult:
    gate:    str
    passed:  bool
    skipped: bool = False
    output:  str  = ""

    @property
    def symbol(self) -> str:
        if self.skipped:
            return "skipped"
        return "PASS" if self.passed else "FAIL"


@dataclass
class SprintSummary:
    processed: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ── subprocess helpers ────────────────────────────────────────────────────────

def _run(*cmd, cwd: Optional[Path] = None, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, check=check, cwd=cwd)
    return r.stdout.strip()


def _try(*cmd, cwd: Optional[Path] = None) -> tuple[bool, str, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode == 0, r.stdout.strip(), r.stderr.strip()


def _run_timed(*cmd, cwd: Optional[Path] = None) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout, r.stderr


# ── port detection (issue #62) ───────────────────────────────────────────────

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
        print(f"  [port] chosen port: {chosen_port} "
              f"(preferred: {cfg.app_default_port}, strategy: {cfg.app_port_strategy})")
        return chosen_port
    except (subprocess.CalledProcessError, ValueError) as e:
        print(f"  Warning: find_port.py failed ({e}) -- skipping port detection", file=sys.stderr)
        return None


def _write_runtime_port(worktree_coder: Path, port: int) -> None:
    """Write chosen port to <coder_worktree>/.commander/runtime/port.

    AC-6: Creates parent dirs as needed.
    """
    runtime_dir = worktree_coder / ".commander" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    port_file = runtime_dir / "port"
    port_file.write_text(str(port), encoding="utf-8")
    print(f"  [port] wrote {port} to {port_file}")


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


# ── dashboard integration ─────────────────────────────────────────────────────

def _post_agent_event(
    tool_name: str,
    agent_id: str = "sprint-manager",
    api_url: Optional[str] = None,
) -> None:
    """POST to /api/agent-event to update the dashboard agent card."""
    base = api_url or DASHBOARD_API_URL
    try:
        payload = json.dumps({
            "agent_id":  agent_id,
            "tool_name": tool_name,
            "timestamp": time.time(),
        }).encode()
        req = urllib.request.Request(
            f"{base}/api/agent-event",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        # Fail silently — dashboard may not be running
        pass


def _post_sprint_status(state: "SprintState", api_url: Optional[str] = None) -> None:
    """POST the current sprint state to /api/sprint-status."""
    base = api_url or DASHBOARD_API_URL
    try:
        payload = json.dumps(state.to_dict()).encode()
        req = urllib.request.Request(
            f"{base}/api/sprint-status",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


# ── alert dispatch ────────────────────────────────────────────────────────────

def dispatch_alerts(
    alert_modes: list[str],
    title: str,
    body: str,
    issue_num: Optional[int] = None,
    category: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Dispatch an alert through all configured channels."""
    api_url    = cfg.api_url    if cfg is not None else None
    alerts_dir = cfg.alerts_dir if cfg is not None else None
    for mode in alert_modes:
        if mode == AlertMode.NONE:
            continue
        try:
            if mode == AlertMode.DASHBOARD_BANNER:
                _alert_dashboard_banner(title, body, issue_num, category, api_url=api_url)
            elif mode == AlertMode.EMAIL:
                _alert_email(title, body)
            elif mode == AlertMode.DISCORD:
                _alert_discord(title, body)
            elif mode == AlertMode.FILE:
                _alert_file(title, body, alerts_dir=alerts_dir)
        except Exception as e:
            print(f"  [alert:{mode}] error — {e}", file=sys.stderr)


def _alert_dashboard_banner(
    title: str,
    body: str,
    issue_num: Optional[int],
    category: Optional[str],
    api_url: Optional[str] = None,
) -> None:
    base = api_url or DASHBOARD_API_URL
    payload = json.dumps({
        "title":      title,
        "body":       body,
        "issue_num":  issue_num,
        "category":   category,
        "timestamp":  _utcnow(),
    }).encode()
    req = urllib.request.Request(
        f"{base}/api/alerts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=3)


def _alert_email(title: str, body: str) -> None:
    host  = os.environ.get("SMTP_HOST", "")
    port  = os.environ.get("SMTP_PORT", "")
    user  = os.environ.get("SMTP_USER", "")
    pw    = os.environ.get("SMTP_PASS", "")
    to    = os.environ.get("ALERT_EMAIL_TO", "")
    if not all([host, port, user, pw, to]):
        return  # silently skip if any var missing
    msg = email.mime.text.MIMEText(body, "plain")
    msg["Subject"] = f"[Sprint Manager] {title}"
    msg["From"]    = user
    msg["To"]      = to
    with smtplib.SMTP(host, int(port)) as srv:
        srv.starttls()
        srv.login(user, pw)
        srv.sendmail(user, [to], msg.as_string())


def _alert_discord(title: str, body: str) -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return  # silently skip
    content = f"**{title}**\n{body}"
    payload = json.dumps({"content": content[:2000]}).encode()
    req = urllib.request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)


def _alert_file(title: str, body: str, alerts_dir: Optional[Path] = None) -> None:
    d = alerts_dir if alerts_dir is not None else ALERTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    today    = datetime.now().strftime("%Y-%m-%d")
    log_path = d / f"{today}.log"
    ts       = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry    = f"[{ts}] {title}\n{body}\n{'─' * 60}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry)


# ── hang detection ────────────────────────────────────────────────────────────

@dataclass
class HangDetector:
    issue_num: int
    log_path:  Optional[Path]
    proc:      subprocess.Popen

    _last_size:    int   = field(default=0, init=False)
    _last_change:  float = field(default_factory=time.monotonic, init=False)
    _warned:       bool  = field(default=False, init=False)
    _stop_event:   threading.Event = field(default_factory=threading.Event, init=False)
    _thread:       Optional[threading.Thread] = field(default=None, init=False)
    _killed:       bool  = field(default=False, init=False)

    def start(self) -> None:
        self._last_change = time.monotonic()
        self._last_size   = self._log_size()
        self._thread      = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    @property
    def killed(self) -> bool:
        return self._killed

    def _log_size(self) -> int:
        if self.log_path and self.log_path.exists():
            return self.log_path.stat().st_size
        return 0

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=HANG_CHECK_SECS)
            if self._stop_event.is_set():
                break
            size = self._log_size()
            if size != self._last_size:
                self._last_size   = size
                self._last_change = time.monotonic()
                self._warned      = False
                continue

            idle = time.monotonic() - self._last_change
            if idle >= HANG_KILL_SECS:
                print(f"  [hang-detect] issue #{self.issue_num}: no log activity for "
                      f"{idle/60:.0f} min — KILLING subprocess", flush=True)
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
                self._killed = True
                self._stop_event.set()
            elif idle >= HANG_WARN_SECS and not self._warned:
                print(f"  WARN [hang-detect] issue #{self.issue_num}: no log activity for "
                      f"{idle/60:.0f} min", flush=True)
                self._warned = True


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _r(repo_name: Optional[str]) -> str:
    return repo_name or github_client.repo()


def _get_issue_labels(issue_num: int, repo_name: Optional[str] = None) -> set[str]:
    """Re-fetch current labels for an issue via gh CLI."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            ["gh", "issue", "view", str(issue_num), "--repo", r, "--json", "labels"],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        return {lbl["name"] for lbl in data.get("labels", [])}
    except Exception:
        return set()


def _add_blocked_label(issue_num: int, reason: str, repo_name: Optional[str] = None) -> None:
    try:
        github_client.update_labels(issue_num, add=["blocked"], repo_name=repo_name)
        github_client.add_comment(
            issue_num,
            f"Issue blocked by sprint manager (HANG): {reason}",
            repo_name=repo_name,
        )
    except Exception as e:
        print(f"  Warning: failed to update GitHub blocked label — {e}", file=sys.stderr)


def _find_feature_branch(issue_num: int) -> Optional[str]:
    """Return feature/<N>-* branch name, checking local then remote."""
    ok, out, _ = _try("git", "branch", "--list", f"feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().lstrip("* ")
    ok, out, _ = _try("git", "branch", "-r", "--list", f"origin/feature/{issue_num}-*")
    if ok and out.strip():
        return out.strip().splitlines()[0].strip().removeprefix("origin/")
    return None


# ── quality gates ─────────────────────────────────────────────────────────────

def _revert_to_sit(issue_num: int, gate_name: str, output: str,
                   repo_name: Optional[str] = None,
                   repo_root: Optional[Path] = None) -> None:
    """Label the issue SIT and post a structured failure comment.

    When failure-parsing helpers are available:
    - Appends a ## Failure Summary table, ## Recommended Fix prose, and
      ## Files to Inspect bulleted list to the GitHub comment.
    - Writes a machine-readable JSON sidecar at
      <repo-root>/.commander/runtime/last-failure-<issue>.json
    """
    truncated = output[:2000] if len(output) > 2000 else output
    comment = (
        f"Quality gate failed: **{gate_name}**\n"
        f"Issue reverted to SIT for re-inspection.\n\n"
        f"**{gate_name}** output:\n```\n{truncated}\n```"
    )

    if _FAILURE_PARSING_AVAILABLE:
        try:
            effective_root = repo_root or REPO_ROOT
            failures = parse_failures(gate_name, output)
            comment += build_failure_block(gate_name, failures)
            sidecar = write_sidecar(issue_num, gate_name, failures,
                                    repo_root=effective_root)
            print(f"  Wrote failure sidecar: {sidecar}")
        except Exception as e:
            print(f"  Warning: failure parsing/sidecar failed — {e}", file=sys.stderr)

    try:
        github_client.update_labels(
            issue_num,
            add=["SIT"],
            remove=["UAT", "in-progress"],
            repo_name=repo_name,
        )
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        print(f"  Warning: failed to update GitHub — {e}", file=sys.stderr)


def _post_success_comment(issue_num: int, results: list[GateResult],
                          repo_name: Optional[str] = None) -> None:
    gate_lines = "\n".join(
        f"- **{r.gate}**: {r.symbol}" for r in results
    )
    comment = (
        f"Quality gates passed. Auto-merged to develop.\n\n"
        f"Gates:\n{gate_lines}\n\nAwaiting human UAT approval."
    )
    try:
        github_client.add_comment(issue_num, comment, repo_name=repo_name)
    except Exception as e:
        print(f"  Warning: failed to post success comment — {e}", file=sys.stderr)


def _gate_pytest(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> GateResult:
    """Gate 1 — run pytest -x inside the tester worktree dashboard.

    gate_scope='changed' (default): only run test files changed relative to
    base_branch. gate_scope='full': run full pytest suite (legacy behaviour).
    """
    if skip:
        print("  [gate:pytest] skipped")
        return GateResult(gate="pytest", passed=True, skipped=True)

    _post_agent_event("gate:pytest")

    # Detect pytest binary
    ok, pytest_path, _ = _try("which", "pytest")
    if not ok:
        # Try inside dashboard venv
        venv_pytest = worktester_dashboard / ".." / "venv" / "bin" / "pytest"
        if venv_pytest.exists():
            pytest_bin = str(venv_pytest.resolve())
        else:
            output = "pytest binary not found on PATH and no venv/bin/pytest found."
            print(f"  [gate:pytest] FAIL -- {output}")
            return GateResult(gate="pytest", passed=False, output=output)
    else:
        pytest_bin = pytest_path

    # Determine which test files to run based on gate_scope
    if gate_scope == "full":
        print("  [gate:pytest] running pytest -x (full scope) ...")
        rc, stdout, stderr = _run_timed(pytest_bin, "-x", cwd=worktester_dashboard)
    else:
        # changed scope: only run test files changed relative to base_branch
        changed = _changed_py_files(base_branch, cwd=worktester_dashboard)
        test_files = [f for f in changed if f.startswith("tests/")]
        if not test_files:
            print("  [gate:pytest] no test files changed — skipped")
            return GateResult(gate="pytest", passed=True, output="no test files changed")
        print(f"  [gate:pytest] checking {len(test_files)} file(s): {', '.join(test_files)}")
        rc, stdout, stderr = _run_timed(pytest_bin, "-x", *test_files, cwd=worktester_dashboard)

    combined = stdout + stderr
    if rc == 0:
        print("  [gate:pytest] PASS")
        return GateResult(gate="pytest", passed=True, output=combined)
    else:
        print(f"  [gate:pytest] FAIL (exit {rc})")
        _revert_to_sit(issue_num, "pytest", combined, repo_name=repo_name)
        return GateResult(gate="pytest", passed=False, output=combined)


def _gate_lint(
    issue_num: int,
    worktester_dashboard: Path,
    skip: bool,
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> GateResult:
    """Gate 2 -- run ruff check inside the tester worktree dashboard.

    gate_scope='changed' (default): only lint .py files changed relative to
    base_branch. gate_scope='full': run ruff check . (legacy behaviour).
    """
    if skip:
        print("  [gate:lint] skipped")
        return GateResult(gate="lint", passed=True, skipped=True)

    _post_agent_event("gate:lint")

    # ruff is optional -- if not found, log warning and treat as passed
    ok_ruff, ruff_path, _ = _try("which", "ruff")
    if not ok_ruff:
        # Try inside dashboard venv
        venv_ruff = worktester_dashboard / ".." / "venv" / "bin" / "ruff"
        if venv_ruff.exists():
            ruff_bin = str(venv_ruff.resolve())
        else:
            print("  [gate:lint] WARNING -- ruff not found; treating as passed")
            return GateResult(gate="lint", passed=True, skipped=False,
                              output="ruff not found -- skipped with warning")
    else:
        ruff_bin = ruff_path

    # Determine which files to lint based on gate_scope
    if gate_scope == "full":
        print("  [gate:lint] running ruff check . (full scope) ...")
        rc, stdout, stderr = _run_timed(ruff_bin, "check", ".", cwd=worktester_dashboard)
    else:
        # changed scope: only lint .py files changed relative to base_branch
        py_files = _changed_py_files(base_branch, cwd=worktester_dashboard)
        if not py_files:
            print("  [gate:lint] no Python files changed — skipped")
            return GateResult(gate="lint", passed=True, output="no Python files changed")
        print(f"  [gate:lint] checking {len(py_files)} file(s): {', '.join(py_files)}")
        rc, stdout, stderr = _run_timed(ruff_bin, "check", *py_files, cwd=worktester_dashboard)

    combined = stdout + stderr
    if rc == 0:
        print("  [gate:lint] PASS")
        return GateResult(gate="lint", passed=True, output=combined)
    else:
        print(f"  [gate:lint] FAIL (exit {rc})")
        _revert_to_sit(issue_num, "lint", combined, repo_name=repo_name)
        return GateResult(gate="lint", passed=False, output=combined)


def _gate_merge_preview(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    skip: bool,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
) -> GateResult:
    """Gate 3 -- simulate merge in worktester root without committing."""
    if skip:
        print("  [gate:merge-preview] skipped")
        return GateResult(gate="merge-preview", passed=True, skipped=True)

    _post_agent_event("gate:merge-preview")
    print(f"  [gate:merge-preview] simulating merge of {feature_branch} into {target_branch} ...")

    merge_ok = False
    combined = ""

    try:
        # Fetch + update target branch
        _run("git", "fetch", "origin", cwd=worktester_root, check=False)

        # Ensure target branch exists locally
        ok, _, _ = _try("git", "show-ref", "--verify", "--quiet",
                         f"refs/heads/{target_branch}", cwd=worktester_root)
        if ok:
            _run("git", "checkout", target_branch, cwd=worktester_root, check=False)
            _run("git", "pull", "origin", target_branch, cwd=worktester_root, check=False)
        else:
            _run("git", "checkout", "--track", f"origin/{target_branch}",
                 cwd=worktester_root, check=False)

        # Attempt dry-run merge
        rc, stdout, stderr = _run_timed(
            "git", "merge", "--no-commit", "--no-ff", feature_branch,
            cwd=worktester_root,
        )
        combined = stdout + stderr
        merge_ok = (rc == 0)

        if merge_ok:
            print("  [gate:merge-preview] PASS -- no conflicts")
        else:
            print(f"  [gate:merge-preview] FAIL -- conflicts detected merging into {target_branch}")
    finally:
        # Always abort to leave working tree clean
        _run("git", "merge", "--abort", cwd=worktester_root, check=False)

    if not merge_ok:
        _revert_to_sit(issue_num, "merge-preview", combined, repo_name=repo_name)
        return GateResult(gate="merge-preview", passed=False, output=combined)

    return GateResult(gate="merge-preview", passed=True, output=combined)


def _run_quality_gates(
    issue_num: int,
    feature_branch: str,
    worktester_root: Path,
    worktester_dashboard: Path,
    skip_all: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> list[GateResult]:
    """Run the three quality gates sequentially. Returns list of GateResult.

    Stops early on first failure (remaining gates are not run).
    If skip_all is True, all gates are skipped.

    base_branch: branch to diff against when gate_scope='changed' (default: 'develop').
    gate_scope: 'changed' (default) scopes gates to changed files only;
                'full' restores legacy full-codebase behaviour.
    """
    results: list[GateResult] = []

    # Gate 1 -- pytest
    r1 = _gate_pytest(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_pytest),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )
    results.append(r1)
    if not r1.passed:
        return results

    # Gate 2 -- lint
    r2 = _gate_lint(
        issue_num,
        worktester_dashboard,
        skip=(skip_all or not gate_lint),
        repo_name=repo_name,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )
    results.append(r2)
    if not r2.passed:
        return results

    # Gate 3 -- merge-preview
    r3 = _gate_merge_preview(
        issue_num,
        feature_branch,
        worktester_root,
        skip=(skip_all or not gate_merge_preview),
        target_branch=target_branch,
        repo_name=repo_name,
    )
    results.append(r3)

    return results


def _create_sprint_branch(sprint_branch: str) -> None:
    """Create sprint/<label> off develop and push to origin (idempotent)."""
    # Check if branch already exists on remote
    ok, _, _ = _try("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{sprint_branch}")
    if ok:
        print(f"  Sprint branch {sprint_branch!r} already exists on origin — skipping creation.")
        return

    # Check if branch already exists locally
    ok, _, _ = _try("git", "show-ref", "--verify", "--quiet", f"refs/heads/{sprint_branch}")
    if ok:
        print(f"  Sprint branch {sprint_branch!r} already exists locally — pushing to origin.")
        _run("git", "push", "-u", "origin", sprint_branch)
        return

    print(f"  Creating sprint branch {sprint_branch!r} off develop…")
    # Fetch latest develop
    _run("git", "fetch", "origin")
    # Get develop SHA from remote to avoid checking out develop (which may be in a worktree)
    ok, develop_sha, _ = _try("git", "rev-parse", "origin/develop")
    if not ok or not develop_sha:
        # fallback: try local develop
        ok, develop_sha, _ = _try("git", "rev-parse", "develop")
    if not ok or not develop_sha:
        print("  Warning: could not resolve develop SHA — using HEAD for sprint branch", file=sys.stderr)
        develop_sha = "HEAD"
    _run("git", "branch", sprint_branch, develop_sha)
    _run("git", "push", "-u", "origin", sprint_branch)
    print(f"  Sprint branch {sprint_branch!r} created and pushed.")


def _call_finish_feature(
    issue_num: int,
    worktester_root: Optional[Path] = None,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
) -> None:
    """Call finish_feature.py as a subprocess from the worktester root."""
    if cfg is not None:
        finish_script = cfg.finish_feature_script
        wt_root = worktester_root or cfg.worktree_tester
    else:
        finish_script = FINISH_FEATURE_SCRIPT
        wt_root = worktester_root or WORKTESTER_ROOT

    cmd = [
        sys.executable, str(finish_script),
        "--issue", str(issue_num),
        "--target-branch", target_branch,
    ]
    if repo_name:
        cmd += ["--repo", repo_name]

    print(f"  Calling finish_feature.py --issue {issue_num} --target-branch {target_branch} ...")
    result = subprocess.run(cmd, cwd=str(wt_root), capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        print(f"  Warning: finish_feature.py exited {result.returncode}", file=sys.stderr)
        if result.stderr:
            print(f"  {result.stderr.rstrip()}", file=sys.stderr)
    else:
        print("  finish_feature.py completed successfully")


# ── post-tester hook ──────────────────────────────────────────────────────────

def handle_post_tester(
    issue_num: int,
    tester_exit_code: int,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    worktester_root: Optional[Path] = None,
    worktester_dashboard: Optional[Path] = None,
    target_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    base_branch: str = "develop",
    gate_scope: str = "changed",
) -> tuple[bool, str, Optional[str]]:
    """Called after a tester subprocess exits.

    Returns (merged: bool, summary_line: str, failure_category: Optional[str]).

    AC-1: Gates only run if tester exited 0 AND label is exactly UAT.

    base_branch: branch to diff against when gate_scope='changed' (default: 'develop').
    gate_scope: 'changed' (default) scopes gates to changed files only;
                'full' restores legacy full-codebase behaviour.
    """
    # Resolve paths: prefer cfg, then explicit args, then globals
    if cfg is not None:
        wt_root      = worktester_root      or cfg.worktree_tester
        wt_dashboard = worktester_dashboard or cfg.worktree_tester_app
        eff_repo     = repo_name            or cfg.repo_name
        api_url      = cfg.api_url
    else:
        wt_root      = worktester_root      or WORKTESTER_ROOT
        wt_dashboard = worktester_dashboard or WORKTESTER_DASHBOARD
        eff_repo     = repo_name
        api_url      = None

    if tester_exit_code != 0:
        return (False,
                f"Issue #{issue_num}: tester exited {tester_exit_code}, skipping gates",
                FailureCategory.CRASH)

    # Re-fetch current labels (AC-1)
    labels = _get_issue_labels(issue_num, repo_name=eff_repo)
    if "UAT" not in labels:
        current = ", ".join(sorted(labels)) or "(none)"
        print(f"  Issue #{issue_num}: tester exited 0 but label is [{current}], not UAT -- skipping gates")
        return (False,
                f"Issue #{issue_num}: tester exited 0 but not UAT -- no merge",
                FailureCategory.TESTER_REJECTED)

    print(f"\nIssue #{issue_num} is UAT -- running quality gates...")

    # Find the feature branch
    feature_branch = _find_feature_branch(issue_num)
    if not feature_branch:
        msg = f"Issue #{issue_num}: feature branch not found -- cannot run merge-preview gate"
        print(f"  Warning: {msg}")
        # Use a placeholder so the other gates can still run
        feature_branch = f"feature/{issue_num}-unknown"

    if skip_gates:
        print("  --skip-gates active -- skipping all quality gates, proceeding to merge")
        _call_finish_feature(issue_num, wt_root, target_branch=target_branch, repo_name=eff_repo, cfg=cfg)
        _post_agent_event("gate:merging", api_url=api_url)
        all_skipped = [
            GateResult(gate="pytest",        passed=True, skipped=True),
            GateResult(gate="lint",          passed=True, skipped=True),
            GateResult(gate="merge-preview", passed=True, skipped=True),
        ]
        _post_success_comment(issue_num, all_skipped, repo_name=eff_repo)
        return True, f"Issue #{issue_num}: all gates skipped, merged into {target_branch}", None

    results = _run_quality_gates(
        issue_num=issue_num,
        feature_branch=feature_branch,
        worktester_root=wt_root,
        worktester_dashboard=wt_dashboard,
        skip_all=False,
        gate_pytest=gate_pytest,
        gate_lint=gate_lint,
        gate_merge_preview=gate_merge_preview,
        target_branch=target_branch,
        repo_name=eff_repo,
        base_branch=base_branch,
        gate_scope=gate_scope,
    )

    # Check if all gates passed
    all_passed = all(r.passed for r in results)

    if all_passed:
        _post_agent_event("gate:merging", api_url=api_url)
        print(f"  All gates passed -- calling finish_feature.py for issue #{issue_num}")
        _call_finish_feature(issue_num, wt_root, target_branch=target_branch, repo_name=eff_repo, cfg=cfg)
        _post_success_comment(issue_num, results, repo_name=eff_repo)
        return True, f"Issue #{issue_num}: all gates passed, merged into {target_branch}", None
    else:
        failed = next((r for r in results if not r.passed), None)
        gate_name = failed.gate if failed else "unknown"
        return (False,
                f"Issue #{issue_num}: gate failed ({gate_name})",
                FailureCategory.GATE_FAIL)


# ── agent dispatch helpers ────────────────────────────────────────────────────

def _build_failure_suffix(issue_num: int, repo_root: Optional[Path] = None) -> str:
    """Read the JSON failure sidecar for issue_num and return a prompt suffix.

    Returns an empty string when:
    - failure-parsing helpers are not available
    - the sidecar does not exist (backward-compat: no error, just generic prompt)

    Logs a note when the sidecar is not found.
    """
    if not _FAILURE_PARSING_AVAILABLE:
        return ""

    effective_root = repo_root or REPO_ROOT
    sc_path = sidecar_path(issue_num, repo_root=effective_root)

    if not sc_path.exists():
        print(f"  [retry] failure sidecar not found at {sc_path} — using generic prompt")
        return ""

    try:
        data = json.loads(sc_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  Warning: could not read failure sidecar {sc_path}: {e}", file=sys.stderr)
        return ""

    gate      = data.get("gate", "unknown")
    failures  = data.get("failures", [])
    files_to_inspect = data.get("files_to_inspect", [])

    if not failures:
        return ""

    lines = [
        f"\n\nPrevious gate '{gate}' failed. Fix the following before re-submitting:"
    ]
    for f in failures[:10]:  # cap at 10 to keep prompt concise
        loc   = f.get("location", "")
        ftype = f.get("type", "")
        msg   = f.get("issue", "")
        test  = f.get("test", "")
        entry = f"- {ftype} at {loc}: {msg}"
        if test:
            entry += f" (test: {test})"
        lines.append(entry)

    if files_to_inspect:
        lines.append("\nFiles requiring changes:")
        for fi in files_to_inspect[:10]:
            lines.append(f"  {fi}")

    return "\n".join(lines)


def _issue_log_path(issue_num: int, cfg: Optional["SprintConfig"] = None) -> Path:
    logs_dir = cfg.logs_dir if cfg is not None else (DASHBOARD_DIR / "logs")
    return logs_dir / f"sprint-issue-{issue_num}.log"


def _dispatch_coder(
    issue_num: int,
    alert_modes: list[str],
    sprint_branch: str = "develop",
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    chosen_port: Optional[int] = None,
) -> tuple[bool, Optional[str]]:
    """Dispatch a coder agent for the issue.  Returns (ok, failure_category)."""
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    api_url  = cfg.api_url if cfg else None
    cwd_path = cfg.worktree_coder if cfg else WORKTESTER_DASHBOARD

    print(f"  Dispatching coder for issue #{issue_num} ...")
    _post_agent_event(f"coder:issue-{issue_num}", api_url=api_url)

    log_path = _issue_log_path(issue_num, cfg=cfg)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Build prompt
    if cfg and cfg.coder_prompt_template:
        issue_url = f"https://github.com/{_r(eff_repo)}/issues/{issue_num}"
        prompt = cfg.coder_prompt_template.format(issue_url=issue_url)
    else:
        prompt = (
            f"Read the issue at https://github.com/{_r(eff_repo)}/issues/{issue_num} "
            "and implement it following the project's branching workflow. "
            "Use the BA/coder/tester workflow defined in CLAUDE.md."
        )

    # Inject failure context from sidecar if available (AC: sprint manager reads sidecar)
    failure_suffix = _build_failure_suffix(issue_num)
    if failure_suffix:
        prompt = prompt + failure_suffix

    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        prompt + (
            f" IMPORTANT: pass --base-branch {sprint_branch} to start_feature.py"
            f" so that the feature branch is based off {sprint_branch!r} instead of develop."
            if sprint_branch not in ("develop",)
            and not (cfg and cfg.coder_prompt_template)
            else ""
        ),
    ]

    # AC-7: inject COMMANDER_APP_PORT if a port was chosen
    proc_env: Optional[dict] = None
    if chosen_port is not None:
        proc_env = {**os.environ, "COMMANDER_APP_PORT": str(chosen_port)}
        print(f"  [port] COMMANDER_APP_PORT={chosen_port} injected into coder env")

    try:
        with log_path.open("w") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=proc_env,
            )
    except FileNotFoundError:
        # claude CLI not available -- treat as stub success for testing
        print("  [coder] claude CLI not found -- stub success")
        return True, None

    detector = HangDetector(issue_num=issue_num, log_path=log_path, proc=proc)
    detector.start()
    rc = proc.wait()
    detector.stop()

    if detector.killed:
        reason = f"No log activity for {HANG_KILL_SECS//60} minutes"
        _add_blocked_label(issue_num, reason, repo_name=eff_repo)
        dispatch_alerts(
            alert_modes,
            title=f"Issue #{issue_num}: HANG detected",
            body=f"The coder subprocess produced no output for {HANG_KILL_SECS//60} minutes and was killed.",
            issue_num=issue_num,
            category=FailureCategory.HANG,
            cfg=cfg,
        )
        return False, FailureCategory.HANG

    if rc != 0:
        return False, FailureCategory.CRASH

    return True, None


def _dispatch_tester(
    issue_num: int,
    alert_modes: list[str],
    repo_name: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    chosen_port: Optional[int] = None,
) -> tuple[int, Optional[str]]:
    """Dispatch a tester agent.  Returns (exit_code, failure_category_if_hang)."""
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    api_url  = cfg.api_url if cfg else None
    cwd_path = cfg.worktree_tester_app if cfg else WORKTESTER_DASHBOARD

    print(f"  Dispatching tester for issue #{issue_num} ...")
    _post_agent_event(f"tester:issue-{issue_num}", api_url=api_url)

    log_path = _issue_log_path(issue_num, cfg=cfg)

    # Build prompt
    if cfg and cfg.tester_prompt_template:
        issue_url = f"https://github.com/{_r(eff_repo)}/issues/{issue_num}"
        prompt = cfg.tester_prompt_template.format(issue_url=issue_url)
    else:
        prompt = (
            f"Read the issue at https://github.com/{_r(eff_repo)}/issues/{issue_num} "
            "and verify it as a tester following the project's testing workflow. "
            "Use the BA/coder/tester workflow defined in CLAUDE.md."
        )

    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p",
        prompt,
    ]

    # AC-7: inject COMMANDER_APP_PORT if a port was chosen
    proc_env: Optional[dict] = None
    if chosen_port is not None:
        proc_env = {**os.environ, "COMMANDER_APP_PORT": str(chosen_port)}
        print(f"  [port] COMMANDER_APP_PORT={chosen_port} injected into tester env")

    try:
        with log_path.open("a") as log_f:
            proc = subprocess.Popen(
                cmd,
                stdout=log_f,
                stderr=log_f,
                cwd=str(cwd_path),
                env=proc_env,
            )
    except FileNotFoundError:
        print("  [tester] claude CLI not found -- stub success")
        return 0, None

    detector = HangDetector(issue_num=issue_num, log_path=log_path, proc=proc)
    detector.start()
    rc = proc.wait()
    detector.stop()

    if detector.killed:
        reason = f"Tester: no log activity for {HANG_KILL_SECS//60} minutes"
        _add_blocked_label(issue_num, reason, repo_name=eff_repo)
        dispatch_alerts(
            alert_modes,
            title=f"Issue #{issue_num}: HANG detected in tester",
            body=f"The tester subprocess produced no output for {HANG_KILL_SECS//60} minutes.",
            issue_num=issue_num,
            category=FailureCategory.HANG,
            cfg=cfg,
        )
        return -1, FailureCategory.HANG

    return rc, None


# ── sprint summary report (AC-1, AC-2, AC-3) ─────────────────────────────────

LEARNINGS_STUB = (
    "_TODO: replace this stub with your retrospective notes._\n\n"
    "What went well? What should we do differently next sprint?"
)


def _follow_up_action(category: Optional[str]) -> str:
    mapping = {
        FailureCategory.HANG:            "Investigate subprocess logs; check for infinite loops or network waits. Retry manually.",
        FailureCategory.CRASH:           "Examine the coder/tester log for the exception. Fix the underlying issue and retry.",
        FailureCategory.GATE_FAIL:       "Review the gate output in the GitHub comment. Fix failing tests or linting errors.",
        FailureCategory.TESTER_REJECTED: "The tester did not advance the issue to UAT. Review the tester log and re-run tester.",
        FailureCategory.RETRY_EXHAUSTED: "Max retries reached. Manually investigate and fix the issue.",
    }
    return mapping.get(category or "", "Review the issue manually.")


def generate_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
    sprint_branch: Optional[str] = None,
) -> str:
    """Generate a richly-formatted executive summary markdown string."""
    n = state.sprint_number if state.sprint_number is not None else state.sprint_label

    start_ts = state.start_timestamp or _utcnow()
    end_ts   = _utcnow()

    h, rem   = divmod(int(elapsed_secs), 3600)
    m_int, s = divmod(rem, 60)
    duration_str = f"{h}h {m_int}m {s}s"

    completed = [i for i in state.issues if i.status == "done"]
    skipped   = [i for i in state.issues if i.status == "skipped"]
    pending   = [i for i in state.issues if i.status == "pending"]

    total_tokens     = state.total_tokens_in + state.total_tokens_out
    avg_ticket_secs  = (elapsed_secs / len(completed)) if completed else 0
    avg_h, avg_r     = divmod(int(avg_ticket_secs), 3600)
    avg_m, avg_s     = divmod(avg_r, 60)
    avg_ticket_str   = f"{avg_h}h {avg_m}m {avg_s}s" if completed else "--"

    tester_rejections = sum(
        1 for i in state.issues
        if i.category == FailureCategory.TESTER_REJECTED
    )
    merge_conflicts = sum(
        1 for i in state.issues
        if i.category == FailureCategory.GATE_FAIL
        and "conflict" in (i.skip_reason or "").lower()
    )
    gate_total    = len([i for i in state.issues if i.status in ("done", "skipped")])
    gate_passed   = len(completed)
    gate_pass_rate = round(gate_passed / gate_total * 100, 1) if gate_total else 0.0

    r = _r(repo_name)
    sprint_filter_url = f"https://github.com/{r}/issues?q=label%3A{state.sprint_label}"

    lines: list[str] = []

    # -- Header section --
    attempted = len(completed) + len(skipped)
    lines += [
        f"## Sprint {n} -- {end_reason}",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Sprint number | {n} |",
        f"| Start | {start_ts} |",
        f"| End | {end_ts} |",
        f"| Duration | {duration_str} |",
        f"| End reason | {end_reason} |",
        f"| Attempted | {attempted} |",
        f"| Completed | {len(completed)} |",
        f"| Skipped | {len(skipped)} |",
        f"| Failed | {len(skipped)} |",
        "",
    ]

    # -- What Shipped --
    lines += [
        "## What Shipped",
        "",
        "| Issue # | Title | Time taken | Outcome | Size |",
        "|---|---|---|---|---|",
    ]
    if completed:
        for issue in completed:
            lines.append(f"| #{issue.number} | {issue.title} | -- | UAT-approved / closed | -- |")
    else:
        lines.append("| -- | No issues shipped | -- | -- | -- |")
    lines.append("")

    # -- What Didn't Ship --
    lines += [
        "## What Didn't Ship",
        "",
        "| Issue # | Title | Failure category | Reason |",
        "|---|---|---|---|",
    ]
    if skipped:
        for issue in skipped:
            cat    = issue.category or "unknown"
            reason = (issue.skip_reason or "no reason recorded").replace("|", "/")
            lines.append(f"| #{issue.number} | {issue.title} | {cat} | {reason} |")
    else:
        lines.append("| -- | All issues shipped | -- | -- |")
    lines.append("")

    # -- Suggested follow-up actions --
    if skipped:
        lines += ["## Suggested Follow-up Actions", ""]
        for issue in skipped:
            action = _follow_up_action(issue.category)
            lines.append(f"- **#{issue.number} {issue.title}** ({issue.category or 'unknown'}): {action}")
        lines.append("")

    # -- Stats --
    lines += [
        "## Stats",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total Tokens | {total_tokens} |",
        f"| Avg ticket time | {avg_ticket_str} |",
        f"| Quality-gate pass rate | {gate_pass_rate}% |",
        f"| Tester rejections | {tester_rejections} |",
        f"| Merge conflicts | {merge_conflicts} |",
        "",
    ]

    # -- Carried Over --
    lines += ["## Carried Over", ""]
    carried_items: list[str] = []
    for issue in pending:
        carried_items.append(f"- #{issue.number} {issue.title} -- candidate for next sprint")
    for issue in (open_issues or []):
        num   = issue.get("number", "?")
        title = issue.get("title", "")
        carried_items.append(f"- #{num} {title} -- candidate for next sprint")
    if carried_items:
        lines.extend(carried_items)
    else:
        lines.append("No issues carried over.")
    lines.append("")

    # -- Key Learnings --
    lines += [
        "## Key Learnings",
        "",
        LEARNINGS_STUB,
        "",
    ]

    # -- Links --
    all_links: list[str] = [
        f"- [Sprint {n} issues on GitHub]({sprint_filter_url})",
    ]
    for issue in state.issues:
        link = f"https://github.com/{r}/issues/{issue.number}"
        all_links.append(f"- [Issue #{issue.number} -- {issue.title[:50]}]({link})")

    lines += ["## Links", ""]
    if len(all_links) > 3:
        lines.append("<details>")
        lines.append(f"<summary>{len(all_links)} links -- click to expand</summary>")
        lines.append("")
        lines.extend(all_links)
        lines.append("")
        lines.append("</details>")
    else:
        lines.extend(all_links)
    lines.append("")

    # -- Next Step (sprint branch PR instructions) --
    effective_sprint_branch = sprint_branch or f"sprint/{state.sprint_label}"
    r = _r(repo_name)
    lines += [
        "## Next Step",
        "",
        f"The sprint branch `{effective_sprint_branch}` is ready for review.",
        "When UAT is complete, open a PR to promote it to `develop`:",
        "",
        f"```bash",
        f"gh pr create --base develop --head {effective_sprint_branch} --repo {r}",
        f"```",
        "",
    ]

    # -- Footer --
    lines.append(f"_Generated by sprint-manager v1.0 on {_utcnow()}_")

    return "\n".join(lines)


def _ensure_github_labels(labels: list[str], repo_name: Optional[str] = None) -> None:
    """Create GitHub labels if they don't exist (best-effort, AC-2)."""
    r = _r(repo_name)
    for label in labels:
        try:
            subprocess.run(
                ["gh", "label", "create", label, "--repo", r, "--force"],
                capture_output=True, text=True, check=False,
            )
        except Exception:
            pass


def create_summary_github_issue(
    content: str,
    sprint_number: Optional[int],
    sprint_label: str,
    repo_name: Optional[str] = None,
) -> tuple[Optional[int], Optional[str]]:
    """AC-2: Create a GitHub issue with the summary markdown as the body."""
    n      = sprint_number if sprint_number is not None else sprint_label
    title  = f"Sprint {n} Executive Summary"
    labels = ["docs", f"sprint-{n}"]

    _ensure_github_labels(labels, repo_name=repo_name)

    try:
        issue_num, url = github_client.create_issue(
            title=title, body=content, labels=labels, repo_name=repo_name
        )
        print(f"  Summary GitHub issue created: {url}")
        return issue_num, url
    except Exception as e:
        print(f"  Warning: failed to create summary GitHub issue -- {e}", file=sys.stderr)
        return None, None


def _prompt_learnings(
    content: str,
    path: Path,
    sprint_number: Optional[int],
    sprint_label: str,
    summary_issue_num: Optional[int],
    repo_name: Optional[str] = None,
) -> str:
    """AC-3: Interactive learnings prompt."""
    n = sprint_number if sprint_number is not None else sprint_label
    if not sys.stdout.isatty():
        return content  # non-interactive: leave stub in place

    try:
        answer = input(f"\nSprint {n} done. Want to add learnings to the summary? (y/n) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return content

    if answer != "y":
        return content

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tmp_path = Path(tf.name)
        tf.write(LEARNINGS_STUB + "\n")

    editor = os.environ.get("EDITOR", "nano")
    try:
        subprocess.run([editor, str(tmp_path)], check=False)
        new_learnings = tmp_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        print(f"  Warning: editor failed -- {e}", file=sys.stderr)
        new_learnings = ""
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    if not new_learnings or new_learnings == LEARNINGS_STUB.strip():
        print("  No learnings entered; keeping placeholder.")
        return content

    updated = content.replace(LEARNINGS_STUB, new_learnings)

    path.write_text(updated, encoding="utf-8")
    print(f"  Key Learnings updated in {path}")

    if summary_issue_num is not None:
        r = _r(repo_name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False,
                                         encoding="utf-8") as tf2:
            tmp2 = Path(tf2.name)
            tf2.write(updated)
        try:
            subprocess.run(
                ["gh", "issue", "edit", str(summary_issue_num),
                 "--repo", r, "--body-file", str(tmp2)],
                capture_output=True, text=True, check=False,
            )
            print(f"  GitHub issue #{summary_issue_num} body updated with learnings.")
        except Exception as e:
            print(f"  Warning: failed to update GitHub issue body -- {e}", file=sys.stderr)
        finally:
            try:
                tmp2.unlink()
            except Exception:
                pass

    return updated


def write_sprint_summary(
    state: SprintState,
    elapsed_secs: float,
    alert_modes: Optional[list[str]] = None,
    end_reason: str = "complete",
    open_issues: Optional[list[dict]] = None,
    repo_name: Optional[str] = None,
    sprint_branch: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
) -> Path:
    """Write summary file, create GitHub issue, prompt for learnings (AC-1/2/3)."""
    eff_repo = repo_name or (cfg.repo_name if cfg else None)
    content = generate_sprint_summary(
        state,
        elapsed_secs,
        end_reason=end_reason,
        open_issues=open_issues,
        repo_name=eff_repo,
        sprint_branch=sprint_branch,
    )
    path = _summary_path(state.sprint_number, state.sprint_label, cfg=cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  Sprint summary written to {path}")

    # Dispatch via all configured alert channels (issue #24)
    if alert_modes:
        title = f"Sprint {state.sprint_label} summary"
        dispatch_alerts(alert_modes, title=title, body=content[:2000], cfg=cfg)

    # AC-2: Create GitHub issue (best-effort)
    try:
        summary_issue_num, summary_issue_url = create_summary_github_issue(
            content=content,
            sprint_number=state.sprint_number,
            sprint_label=state.sprint_label,
            repo_name=eff_repo,
        )
    except Exception as exc:
        print(f"  Warning: create_summary_github_issue raised -- {exc}", file=sys.stderr)
        summary_issue_num, summary_issue_url = None, None

    # Store summary_issue_url in state JSON
    if summary_issue_url:
        state_path = _state_path(state.sprint_number, state.sprint_label, cfg=cfg)
        try:
            if state_path.exists():
                state_dict = json.loads(state_path.read_text())
            else:
                state_dict = state.to_dict()
            state_dict["summary_issue_url"] = summary_issue_url
            state_path.write_text(json.dumps(state_dict, indent=2))
        except Exception as e:
            print(
                f"  Warning: could not update state file with summary_issue_url -- {e}",
                file=sys.stderr,
            )

    # AC-3: Interactive learnings prompt
    _prompt_learnings(
        content=content,
        path=path,
        sprint_number=state.sprint_number,
        sprint_label=state.sprint_label,
        summary_issue_num=summary_issue_num,
        repo_name=eff_repo,
    )

    return path


# -- GitHub issue listing --

def _classify(labels: set[str]) -> str:
    if "UAT-approved" in labels:
        return "done"
    if "UAT" in labels:
        return "uat"
    if "SIT" in labels:
        return "sit"
    if "in-progress" in labels:
        return "in-progress"
    return "backlog"


def list_backlog_issues(label: str, repo_name: Optional[str] = None) -> list[dict]:
    """Return open issues with the given label, in backlog, sorted by number."""
    r = _r(repo_name)
    try:
        out = subprocess.run(
            [
                "gh", "issue", "list",
                "--repo", r,
                "--label", label,
                "--state", "open",
                "--json", "number,title,labels",
                "--limit", "200",
            ],
            capture_output=True, text=True, check=True,
        )
        issues = json.loads(out.stdout)
        # Only return issues in backlog state
        result = []
        for issue in issues:
            labels_set = {lbl["name"] for lbl in issue.get("labels", [])}
            if _classify(labels_set) == "backlog":
                result.append(issue)
        return sorted(result, key=lambda i: i["number"])
    except Exception as e:
        print(f"Warning: could not list issues -- {e}", file=sys.stderr)
        return []


# ── sprint loop ────────────────────────────────────────────────────────────────

def run_sprint(
    label: str,
    skip_gates: bool,
    gate_pytest: bool,
    gate_lint: bool,
    gate_merge_preview: bool,
    alert_modes: Optional[list[str]] = None,
    repo_name: Optional[str] = None,
    dry_run: bool = False,
    resume: bool = False,
    retry_failed: bool = False,
    target_branch: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    preflight_approved: Optional[list[int]] = None,
    gate_scope: str = "changed",
) -> tuple[SprintSummary, SprintState]:
    """Main sprint loop -- processes backlog issues sequentially.

    Returns (SprintSummary, SprintState).
    Supports resume/retry_failed from persisted state.

    target_branch: branch to merge feature branches into. Defaults to
    sprint/<label>. Pass 'develop' to restore legacy behaviour.

    preflight_approved: optional list of issue numbers approved by the pre-flight
    review. When provided, only issues in this list are dispatched; others are
    skipped with reason 'preflight-skipped'.

    gate_scope: 'changed' (default) scopes pytest/lint gates to files changed
    relative to the base branch; 'full' restores legacy full-codebase behaviour.
    """
    if alert_modes is None:
        alert_modes = [AlertMode.DASHBOARD_BANNER]

    # Effective repo: explicit arg > config > github_client
    eff_repo   = repo_name or (cfg.repo_name if cfg else None)
    api_url    = cfg.api_url if cfg else None

    summary    = SprintSummary()
    sprint_num = _sprint_number(label)
    state_path = _state_path(sprint_num, label, cfg=cfg)

    # Log config info when running against a second repo
    if cfg and cfg.repo_name:
        print(f"\n=== SprintConfig ===")
        print(f"  repo:         {cfg.repo_name}")
        print(f"  coder-wt:     {cfg.worktree_coder}")
        print(f"  tester-wt:    {cfg.worktree_tester}")
        print(f"  tester-app:   {cfg.worktree_tester_app}")
        print(f"  logs-dir:     {cfg.logs_dir}")
        print(f"  sprints-dir:  {cfg.sprints_dir}")
        print(f"  api-url:      {cfg.api_url}")

    # Determine the sprint branch name and effective merge target
    sprint_branch = f"sprint/{label}"
    if target_branch is None:
        target_branch = sprint_branch

    # Load or build state
    if (resume or retry_failed) and state_path.exists():
        print(f"  Loading existing sprint state from {state_path}")
        state = SprintState.from_dict(json.loads(state_path.read_text()))
    else:
        raw_issues = list_backlog_issues(label, repo_name=eff_repo)
        if not raw_issues:
            print("No backlog issues found for this label.")
            state = SprintState(
                sprint_label  = label,
                sprint_number = sprint_num,
                start_timestamp = _utcnow(),
            )
            return summary, state

        state = SprintState(
            sprint_label    = label,
            sprint_number   = sprint_num,
            start_timestamp = _utcnow(),
            issues=[
                IssueState(number=i["number"], title=i["title"])
                for i in raw_issues
            ],
        )

    print(f"\n=== Sprint Manager: label={label} ===")
    print(f"Target branch: {target_branch}")
    print(f"Found {len(state.issues)} issue(s): {[i.number for i in state.issues]}")

    # AC-1: Create sprint branch off develop (idempotent)
    if target_branch == sprint_branch:
        if dry_run:
            print(f"  [dry-run] would create sprint branch {sprint_branch!r} off develop")
        else:
            _create_sprint_branch(sprint_branch)
    else:
        print(f"  Using custom target branch {target_branch!r} — sprint branch creation skipped.")

    start_time = time.monotonic()

    for issue_state in state.issues:
        num   = issue_state.number
        title = issue_state.title

        # Resume: skip already-done/skipped
        if resume and issue_state.status in ("done", "skipped"):
            print(f"\n--- Issue #{num}: {title} --- [SKIP: already {issue_state.status}]")
            summary.processed.append(f"#{num}")
            if issue_state.status == "done":
                summary.merged.append(f"#{num}")
            else:
                summary.skipped.append(f"#{num} ({issue_state.skip_reason or 'skipped'})")
            continue

        # retry_failed: skip only done issues
        if retry_failed and issue_state.status == "done":
            print(f"\n--- Issue #{num}: {title} --- [SKIP: already done]")
            summary.processed.append(f"#{num}")
            summary.merged.append(f"#{num}")
            continue

        print(f"\n--- Issue #{num}: {title} ---")
        summary.processed.append(f"#{num}")

        # Preflight filter: skip issues not approved by pre-flight review
        if preflight_approved is not None and num not in preflight_approved:
            print("  [preflight] skipped by pre-flight review")
            issue_state.status      = "skipped"
            issue_state.skip_reason = "preflight-skipped"
            summary.skipped.append(f"#{num} (preflight-skipped)")
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)
            continue

        if dry_run:
            print("  [dry-run] would dispatch coder + tester")
            issue_state.status = "skipped"
            issue_state.skip_reason = "dry-run"
            summary.skipped.append(f"#{num} (dry-run)")
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)
            continue

        # -- Port detection (issue #62, AC-5/6/7/8) --
        chosen_port: Optional[int] = None
        if cfg is not None and cfg.app_default_port is not None:
            chosen_port = _detect_port(cfg)
            if chosen_port is not None:
                _write_runtime_port(cfg.worktree_coder, chosen_port)

        # -- Dispatch coder --
        coder_ok, coder_category = _dispatch_coder(
            num, alert_modes, sprint_branch=target_branch, repo_name=eff_repo, cfg=cfg,
            chosen_port=chosen_port,
        )

        if not coder_ok:
            category = coder_category or FailureCategory.CRASH
            reason   = f"Coder failed with category {category}"
            print(f"  Coder failed for #{num} ({category}) -- skipping to next issue")
            issue_state.status      = "skipped"
            issue_state.skip_reason = reason
            issue_state.category    = category
            summary.skipped.append(f"#{num} (coder failed)")
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{num} skipped: {category}",
                body=reason,
                issue_num=num,
                category=category,
                cfg=cfg,
            )
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)
            continue

        # -- Dispatch tester --
        tester_rc, hang_category = _dispatch_tester(
            num, alert_modes, repo_name=eff_repo, cfg=cfg,
            chosen_port=chosen_port,
        )

        if hang_category == FailureCategory.HANG:
            issue_state.status      = "skipped"
            issue_state.skip_reason = "Tester HANG detected"
            issue_state.category    = FailureCategory.HANG
            summary.skipped.append(f"#{num} (tester hang)")
            state.save(state_path)
            _post_sprint_status(state, api_url=api_url)
            continue

        # -- Post-tester gates --
        merged, summary_line, gate_category = handle_post_tester(
            issue_num          = num,
            tester_exit_code   = tester_rc,
            skip_gates         = skip_gates,
            gate_pytest        = gate_pytest,
            gate_lint          = gate_lint,
            gate_merge_preview = gate_merge_preview,
            target_branch      = target_branch,
            repo_name          = eff_repo,
            cfg                = cfg,
            base_branch        = target_branch or "develop",
            gate_scope         = gate_scope,
        )
        print(f"  {summary_line}")

        if merged:
            issue_state.status = "done"
            summary.merged.append(f"#{num}")
        else:
            category = gate_category or FailureCategory.CRASH
            issue_state.status      = "skipped"
            issue_state.skip_reason = summary_line
            issue_state.category    = category
            if "gate failed" in summary_line:
                summary.gate_failures.append(summary_line)
            else:
                summary.skipped.append(f"#{num} ({category})")
            dispatch_alerts(
                alert_modes,
                title=f"Issue #{num} skipped: {category}",
                body=summary_line,
                issue_num=num,
                category=category,
                cfg=cfg,
            )

        elapsed = time.monotonic() - start_time
        state.wall_clock_secs = elapsed
        state.save(state_path)
        _post_sprint_status(state, api_url=api_url)

    # Final elapsed time
    state.wall_clock_secs = time.monotonic() - start_time
    state.save(state_path)

    return summary, state


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(
        description="Sprint Manager -- orchestrate coder+tester agents with gates, "
                    "failure categorisation, hang detection, and alert channels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("label", help="GitHub label identifying the sprint (e.g. sprint-5)")
    p.add_argument("--repo", default=None, help="owner/repo override")

    # Config file (AC-4)
    p.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "Path to .commander/sprint.yaml config file.  "
            "When provided, all path/repo settings are read from it.  "
            "Incompatible with env-var fallback path."
        ),
    )

    # Gate control flags
    p.add_argument(
        "--skip-gates",
        action="store_true",
        default=False,
        help="Skip all quality gates and force auto-merge after tester passes",
    )
    p.add_argument(
        "--gate-pytest",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable pytest gate (default: enabled)",
    )
    p.add_argument(
        "--gate-lint",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable lint gate (default: enabled)",
    )
    p.add_argument(
        "--gate-merge-preview",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable/disable merge-preview gate (default: enabled)",
    )

    # Sprint control flags
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List issues but do not dispatch agents",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume from existing state file, skipping done/skipped issues",
    )
    p.add_argument(
        "--retry-failed",
        action="store_true",
        default=False,
        help="Re-dispatch only skipped/failed issues from existing state",
    )
    p.add_argument(
        "--target-branch",
        default=None,
        help=(
            "Branch to merge feature branches into. "
            "Defaults to sprint/<label>. "
            "Pass 'develop' to restore legacy behaviour."
        ),
    )

    # Gate scope (AC-9)
    p.add_argument(
        "--gate-scope",
        default="changed",
        choices=["changed", "full"],
        help=(
            "Scope for pytest and lint gates. "
            "'changed' (default): only check files changed relative to the base branch. "
            "'full': run pytest -x and ruff check . against the whole codebase (legacy behaviour)."
        ),
    )

    # Alert modes (AC-3)
    p.add_argument(
        "--alert-mode",
        default=AlertMode.DASHBOARD_BANNER,
        help=(
            "Comma-separated alert modes: "
            "dashboard-banner, email, discord, file, none  (default: dashboard-banner)"
        ),
    )

    # Pre-flight review
    p.add_argument(
        "--preflight",
        action="store_true",
        default=False,
        help=(
            "Run sprint_review.py pre-flight BA check before dispatching any issues. "
            "Aborts the sprint run (exit 1) if the user quits from the prompt."
        ),
    )

    args = p.parse_args()

    # ── Config resolution (AC-4 + AC-5 + AC-6) ───────────────────────────────
    cfg: Optional[SprintConfig] = None

    if args.config:
        # Explicit --config flag (AC-4)
        config_path = Path(args.config).expanduser().resolve()
        if not config_path.exists():
            p.error(f"Config file not found: {config_path}")
        print(f"  Using config: {config_path}")
        cfg = load_config(config_path)
    else:
        # Auto-discovery: walk up from CWD (AC-5)
        discovered = discover_config()
        if discovered:
            print(f"  Auto-discovered config: {discovered}")
            cfg = load_config(discovered)
        else:
            # Backward-compatible default (AC-6)
            cfg = None

    raw_modes   = [m.strip() for m in args.alert_mode.split(",") if m.strip()]
    alert_modes = []
    for m in raw_modes:
        if m not in AlertMode.ALL_MODES:
            p.error(f"Unknown alert mode: {m!r}. Valid: {', '.join(sorted(AlertMode.ALL_MODES))}")
        alert_modes.append(m)

    if not alert_modes:
        alert_modes = [AlertMode.DASHBOARD_BANNER]

    # --repo flag overrides config (explicit always wins)
    eff_repo = args.repo or (cfg.repo_name if cfg else None)

    # ── Pre-flight review (AC-1 of issue #33) ────────────────────────────────
    preflight_approved: Optional[list] = None  # None = no preflight, list = approved numbers
    if args.preflight:
        from sprint_review import run_preflight  # lazy import — only needed with --preflight
        sprints_dir = cfg.sprints_dir if cfg else SPRINTS_DIR
        _all_results, approved = run_preflight(
            sprint_label = args.label,
            repo_name    = eff_repo,
            sprints_dir  = sprints_dir,
            interactive  = True,
        )
        # run_preflight exits(1) if user chose Q — if we reach here, proceed
        preflight_approved = [r.number for r in approved]
        print(f"[preflight] Approved {len(preflight_approved)} issue(s) for this run.")

    summary, state = run_sprint(
        label                = args.label,
        skip_gates           = args.skip_gates,
        gate_pytest          = args.gate_pytest,
        gate_lint            = args.gate_lint,
        gate_merge_preview   = args.gate_merge_preview,
        alert_modes          = alert_modes,
        repo_name            = eff_repo,
        dry_run              = args.dry_run,
        resume               = args.resume,
        retry_failed         = args.retry_failed,
        target_branch        = args.target_branch,
        cfg                  = cfg,
        preflight_approved   = preflight_approved,
        gate_scope           = args.gate_scope,
    )

    # Derive sprint_branch for summary (mirrors run_sprint logic)
    sprint_branch = f"sprint/{args.label}"
    effective_target = args.target_branch or sprint_branch

    # AC-1/2/3: write extended summary, create GitHub issue, prompt for learnings
    if state.issues:
        end_reason   = "complete" if not summary.skipped else "stopped"
        summary_path = write_sprint_summary(
            state         = state,
            elapsed_secs  = state.wall_clock_secs,
            alert_modes   = alert_modes,
            end_reason    = end_reason,
            repo_name     = eff_repo,
            cfg           = cfg,
            sprint_branch = effective_target,
        )
    else:
        summary_path = None

    print("\n=== Sprint Summary ===")
    print(f"Processed: {', '.join(summary.processed) or 'none'}")
    print(f"Merged:    {', '.join(summary.merged) or 'none'}")
    if summary.gate_failures:
        print("Gate failures:")
        for line in summary.gate_failures:
            print(f"  {line}")
    if summary.skipped:
        print(f"Skipped:   {', '.join(summary.skipped)}")
    if summary_path:
        print(f"Summary:   {summary_path}")


if __name__ == "__main__":
    main()
