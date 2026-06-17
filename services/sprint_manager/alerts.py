"""Alert channel logic for sprint_manager.

Extracted from sprint_manager.py (issue #1271) — pure move, no logic changes.
Contains: AlertMode, dispatch_alerts, _alert_dashboard_banner, _alert_email,
_alert_discord, _alert_ntfy, _alert_file, HangDetector.
"""
from __future__ import annotations

import email.mime.text
import json
import os
import smtplib
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.sprint_manager.config import SprintConfig

from services.logging import log as structured_log  # noqa: E402

# ── path constants ─────────────────────────────────────────────────────────────
# This file lives at services/sprint_manager/alerts.py
# Repo root is three levels up: alerts.py → sprint_manager/ → services/ → root
_REPO_ROOT = Path(__file__).parent.parent.parent
_DASHBOARD_DIR = _REPO_ROOT / "apps" / "dashboard"

_DASHBOARD_API_URL = os.environ.get("DASHBOARD_API_URL", "http://localhost:8000")
ALERTS_DIR = _DASHBOARD_DIR / "alerts"

# ── hang detection constants ───────────────────────────────────────────────────
HANG_WARN_SECS  = 30 * 60   # 30 minutes
HANG_KILL_SECS  = 60 * 60   # 60 minutes
HANG_CHECK_SECS = 5  * 60   # check every 5 minutes


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── alert modes ────────────────────────────────────────────────────────────────

class AlertMode:
    DASHBOARD_BANNER = "dashboard-banner"
    EMAIL            = "email"
    DISCORD          = "discord"
    FILE             = "file"
    NTFY             = "ntfy"
    NONE             = "none"

    ALL_MODES = {DASHBOARD_BANNER, EMAIL, DISCORD, FILE, NTFY, NONE}


# ── alert dispatch ─────────────────────────────────────────────────────────────

def dispatch_alerts(
    alert_modes: list[str],
    title: str,
    body: str,
    issue_num: Optional[int] = None,
    category: Optional[str] = None,
    cfg: Optional["SprintConfig"] = None,
    repo: Optional[str] = None,
    sprint_label: Optional[str] = None,
) -> None:
    """Dispatch an alert through all configured channels."""
    api_url    = cfg.api_url    if cfg is not None else None
    alerts_dir = cfg.alerts_dir if cfg is not None else None
    eff_sprint_label = sprint_label
    # Look up helpers via the sprint_manager module when it is loaded under that
    # name (tests import it bare) so that @patch("sprint_manager._alert_X") works.
    import sys as _sys
    _sm = _sys.modules.get("sprint_manager")

    def _fn(name: str):
        if _sm is not None and hasattr(_sm, name):
            return getattr(_sm, name)
        return globals()[name]

    for mode in alert_modes:
        if mode == AlertMode.NONE:
            continue
        try:
            if mode == AlertMode.DASHBOARD_BANNER:
                _fn("_alert_dashboard_banner")(title, body, issue_num, category, api_url=api_url, repo=repo)
            elif mode == AlertMode.EMAIL:
                _fn("_alert_email")(title, body)
            elif mode == AlertMode.DISCORD:
                _fn("_alert_discord")(title, body)
            elif mode == AlertMode.FILE:
                _fn("_alert_file")(title, body, alerts_dir=alerts_dir)
            elif mode == AlertMode.NTFY:
                _fn("_alert_ntfy")(title, body, category, sprint_label=eff_sprint_label)
        except Exception as e:
            structured_log.error("alert_dispatch_error", f"[alert:{mode}] error: {e}", mode=mode, exc=str(e))


def _alert_dashboard_banner(
    title: str,
    body: str,
    issue_num: Optional[int],
    category: Optional[str],
    api_url: Optional[str] = None,
    repo: Optional[str] = None,
) -> None:
    base = api_url or _DASHBOARD_API_URL
    payload = json.dumps({
        "title":      title,
        "body":       body,
        "issue_num":  issue_num,
        "category":   category,
        "repo":       repo,
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


def _alert_ntfy(
    title: str,
    body: str,
    category: Optional[str] = None,
    sprint_label: Optional[str] = None,
) -> None:
    topic_url = os.environ.get("NTFY_TOPIC_URL", "")
    if not topic_url:
        return
    priority = "4" if category in ("failure", "needs-rework") else "3"
    payload  = body.encode()
    headers: dict = {
        "Title":    title,
        "Priority": priority,
        "Tags":     category or "sprint",
    }
    # Deep-link into Run Browser when DASHBOARD_URL is configured (issue #783).
    dashboard_url = os.environ.get("DASHBOARD_URL", "").rstrip("/")
    if dashboard_url and sprint_label:
        headers["Click"] = f"{dashboard_url}/run-browser?sprint={sprint_label}"
    req = urllib.request.Request(
        topic_url,
        data=payload,
        headers=headers,
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


# ── hang detection ─────────────────────────────────────────────────────────────

@dataclass
class HangDetector:
    issue_num: int
    log_path:  Optional[Path]
    proc:      subprocess.Popen
    max_total_secs: Optional[int] = None  # hard wall-clock cap (None = disabled)
    agent_role: Optional[str] = None      # for dispatch_killed logging (coder/tester)
    attempt:    Optional[int] = None      # dispatch attempt number, for logging

    _start_time:   float = field(default_factory=time.monotonic, init=False)
    _last_size:    int   = field(default=0, init=False)
    _last_change:  float = field(default_factory=time.monotonic, init=False)
    _warned:       bool  = field(default=False, init=False)
    _stop_event:   threading.Event = field(default_factory=threading.Event, init=False)
    _thread:       Optional[threading.Thread] = field(default=None, init=False)
    _killed:       bool  = field(default=False, init=False)

    def start(self) -> None:
        self._start_time  = time.monotonic()
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

            # Hard wall-clock timeout (used by documenter for ≤5 min budget)
            if self.max_total_secs is not None:
                total_elapsed = time.monotonic() - self._start_time
                if total_elapsed >= self.max_total_secs:
                    structured_log.error("subprocess_timeout", f"subprocess exceeded {self.max_total_secs}s wall-clock limit — KILLING", issue_num=self.issue_num, timeout_secs=self.max_total_secs)
                    structured_log.error(
                        "dispatch_killed", f"#{self.issue_num} killed (timeout)",
                        issue_num=self.issue_num, agent_role=self.agent_role,
                        attempt=self.attempt, reason="timeout",
                        timeout_secs=self.max_total_secs,
                    )
                    try:
                        self.proc.kill()
                    except ProcessLookupError:
                        pass
                    self._killed = True
                    self._stop_event.set()
                    return

            size = self._log_size()
            if size != self._last_size:
                self._last_size   = size
                self._last_change = time.monotonic()
                self._warned      = False
                continue

            idle = time.monotonic() - self._last_change
            if idle >= HANG_KILL_SECS:
                structured_log.error("subprocess_killed", f"no log activity for {idle/60:.0f} min — KILLING subprocess", issue_num=self.issue_num, idle_minutes=round(idle / 60))
                structured_log.error(
                    "dispatch_killed", f"#{self.issue_num} killed (idle-silence)",
                    issue_num=self.issue_num, agent_role=self.agent_role,
                    attempt=self.attempt, reason="idle-silence",
                    idle_minutes=round(idle / 60),
                )
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
                self._killed = True
                self._stop_event.set()
            elif idle >= HANG_WARN_SECS and not self._warned:
                structured_log.warn("hang_detected", f"no log activity for {idle/60:.0f} min", issue_num=self.issue_num, idle_minutes=round(idle / 60))
                self._warned = True
