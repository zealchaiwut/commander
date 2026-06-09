"""Optional live-browser UAT driver backed by the `agent-browser` CLI.

Sprint UAT steps that target a web UI normally fall back to MANUAL because the
tester has no automated browser. This module wires `agent-browser` (a native
CLI that drives real Chrome) as an *optional* capability:

* When `agent-browser` is installed and Chrome has been set up
  (`agent-browser install`), :func:`run_browser_step` drives the browser,
  performs the described interaction, asserts the outcome, and returns a
  screenshot path.
* When the binary is absent (or Chrome is not set up), it degrades gracefully:
  a single WARNING is logged and the step is reported as ``"uncovered"`` so the
  caller falls back to MANUAL.

Importing this module has no side-effects — the binary is only invoked when
:func:`is_available` or :func:`run_browser_step` is called.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

#: Name of the CLI binary we shell out to.
BINARY = "agent-browser"

#: Directory screenshots are written to. Overridable for tests / sandboxing.
SCREENSHOT_DIR = Path(
    os.environ.get("AGENT_BROWSER_SCREENSHOT_DIR", "/tmp/agent-browser-uat")
)

#: How long a single browser step may run before we give up.
_STEP_TIMEOUT_S = 300
#: How long the readiness probe may run.
_STATUS_TIMEOUT_S = 30

# Steps that describe physical hardware / native-device interaction cannot be
# automated by a browser. We detect them so the caller falls back to MANUAL
# instead of attempting (and failing) a browser run.
_NON_BROWSER_PATTERNS = [
    r"physical (android|ios|device|phone|tablet|hardware)",
    r"\breal (device|phone|iphone|ipad|android)\b",
    r"\bandroid device\b",
    r"\bios device\b",
    r"\biphone\b",
    r"\bipad\b",
    r"\bhardware\b",
    r"\bmobile device\b",
    r"\bphysical\b",
    r"\busb\b",
    r"\bbluetooth\b",
    r"\bnfc\b",
    r"\bappium\b",
    r"\bxcuitest\b",
    r"\bnative app\b",
    r"\bemulator\b",
    r"\bsimulator\b",
    r"\bdongle\b",
]
_NON_BROWSER_RE = re.compile("|".join(_NON_BROWSER_PATTERNS), re.IGNORECASE)


# ── capability detection ────────────────────────────────────────────────────

def _chrome_ready() -> bool:
    """Return True when ``agent-browser install`` has been run (Chrome set up).

    Probes ``agent-browser status --json``. A non-zero exit, a missing binary,
    or a ``chrome_installed: false`` flag all mean "not ready". A zero exit with
    non-JSON output is treated as ready (older CLI versions without --json).
    """
    try:
        result = subprocess.run(
            [BINARY, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return True
    return bool(data.get("chrome_installed", True))


def is_available() -> bool:
    """True only when ``agent-browser`` is on PATH **and** Chrome is set up."""
    if shutil.which(BINARY) is None:
        return False
    return _chrome_ready()


def is_step_automatable(step_text: str) -> bool:
    """False for steps that require physical hardware / native devices."""
    return _NON_BROWSER_RE.search(step_text or "") is None


# ── caller helpers (sprint tester orchestrator) ─────────────────────────────

def is_manual_fallback(result: dict) -> bool:
    """True when a step result should be reported as MANUAL by the caller."""
    return result.get("status") == "uncovered"


def counts_as_failure(result: dict) -> bool:
    """True only for an actual failed assertion — never for MANUAL fallbacks."""
    return result.get("status") == "fail"


# ── execution ───────────────────────────────────────────────────────────────

def _new_screenshot_path() -> Path:
    """Return a unique screenshot path under :data:`SCREENSHOT_DIR`."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return SCREENSHOT_DIR / f"uat-{ts}.png"


def _uncovered(detail: str) -> dict:
    return {"status": "uncovered", "detail": detail, "screenshot_path": None}


def run_browser_step(step_text: str, base_url: str) -> dict:
    """Execute a single UAT step against a live browser.

    Returns a dict with keys:
        status          "pass" | "fail" | "uncovered"
        detail          human-readable explanation (always a str)
        screenshot_path path to a .png on disk, or None

    Never raises: any failure to drive the browser is reported as ``"fail"``,
    and absence of the tool (or a non-automatable step) as ``"uncovered"`` so
    the caller can fall back to MANUAL.
    """
    if not is_available():
        log.warning(
            "agent-browser not installed; UAT browser step falls back to MANUAL: %s",
            step_text,
        )
        return _uncovered("agent-browser not installed")

    if not is_step_automatable(step_text):
        return _uncovered("step requires physical device/hardware — not browser-automatable")

    screenshot_path = _new_screenshot_path()
    cmd = [
        BINARY, "run",
        "--url", base_url,
        "--task", step_text,
        "--screenshot", str(screenshot_path),
        "--json",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_STEP_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return {"status": "fail", "detail": "agent-browser timed out", "screenshot_path": None}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "fail", "detail": f"agent-browser execution error: {exc}", "screenshot_path": None}

    shot = str(screenshot_path) if screenshot_path.exists() else None
    status, detail = _parse_result(result)
    return {"status": status, "detail": detail, "screenshot_path": shot}


def _parse_result(result) -> tuple[str, str]:
    """Map an ``agent-browser run --json`` result to (status, detail)."""
    raw = (result.stdout or "").strip()
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        if result.returncode == 0:
            return "pass", raw or "browser step succeeded"
        return "fail", (result.stderr or raw or "browser step failed").strip()

    detail = data.get("detail") or data.get("message") or ""
    success = data.get("success")
    if success is None:
        success = result.returncode == 0
    if success:
        return "pass", detail or "browser step succeeded"
    return "fail", detail or (result.stderr or "browser step failed").strip()
