"""Optional live-browser UAT driver backed by the `agent-browser` CLI.

Sprint UAT steps that target a web UI normally fall back to MANUAL because the
tester has no automated browser. This module wires `agent-browser` (a native
CLI that drives real Chrome) as an *optional* capability:

agent-browser (0.27.x) is a **low-level command CLI for AI agents** — ``open``,
``click``, ``type``, ``find``, ``get``, ``is``, ``screenshot``, ``snapshot``, … —
with no high-level "run this task" command. So the Tester agent *drives* it
directly (see tester.md Step 6 + ``agent-browser skills get core``); this module
provides the shared primitives:

* :func:`is_available` — True only when the binary works (``--version``) and
  Chrome was downloaded (``agent-browser install``).
* :func:`run_cli` — run one ``agent-browser`` command, returning
  ``(rc, stdout, stderr)``; never raises (missing binary / timeout degrade).
* :func:`classify_uat_step` — route a UAT step to ``browser`` / ``http`` /
  ``manual``.

When the binary is absent (or Chrome is not set up) the Tester gates on
:func:`is_available` and falls back to MANUAL. Importing has no side-effects —
the binary is only invoked when these functions are called.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Pillow is used to cap screenshot resolution (AC-2) and compute a pixel-hash
# for duplicate detection (AC-3). It is an optional import: if absent the
# screenshot flow degrades to storing the raw bytes and hashing them directly,
# so importing this module never fails on a machine without Pillow.
try:
    from PIL import Image
    _PIL_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when Pillow is missing
    Image = None  # type: ignore
    _PIL_AVAILABLE = False

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

#: Where ``agent-browser install`` downloads Chrome-for-Testing.
_CHROME_DIR = Path(os.environ.get("AGENT_BROWSER_HOME", str(Path.home() / ".agent-browser"))) / "browsers"


def _chrome_ready() -> bool:
    """Return True when the agent-browser CLI works and Chrome is set up.

    The shipped CLI (0.27.x) has **no** ``status`` command — it is a low-level
    command CLI. Readiness = the binary responds to ``--version`` (exit 0) AND a
    Chrome build was downloaded under ``~/.agent-browser/browsers/`` (by
    ``agent-browser install``).
    """
    try:
        result = subprocess.run(
            [BINARY, "--version"],
            capture_output=True,
            text=True,
            timeout=_STATUS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    try:
        return _CHROME_DIR.is_dir() and any(_CHROME_DIR.glob("chrome-*"))
    except OSError:
        return False


def is_available() -> bool:
    """True only when ``agent-browser`` is on PATH **and** Chrome is set up."""
    if shutil.which(BINARY) is None:
        return False
    return _chrome_ready()


def is_step_automatable(step_text: str) -> bool:
    """False for steps that require physical hardware / native devices."""
    return _NON_BROWSER_RE.search(step_text or "") is None


# ── tester Step-6 routing (issue #710) ──────────────────────────────────────

#: Keywords whose presence marks a UAT step as a browser interaction (AC-1).
_BROWSER_KEYWORDS = ("open", "navigate", "click", "see", "expect", "page")
_BROWSER_KW_RE = re.compile(
    r"\b(" + "|".join(_BROWSER_KEYWORDS) + r")\b", re.IGNORECASE
)

#: Indicators that a step is an HTTP/API check that httpx can drive directly.
_HTTP_KW_RE = re.compile(
    r"\b(GET|POST|PUT|PATCH|DELETE|endpoint|api|request|responds?|returns?|"
    r"status\s*code|http\s*\d{3})\b",
    re.IGNORECASE,
)


def is_browser_step(step_text: str, agent_testable: bool = False) -> bool:
    """True when a UAT step should be attempted via :func:`run_browser_step`.

    Issue #710 AC-1: a step is browser-testable when the BA flagged it
    ``agent-testable`` OR its text describes a browser interaction (keywords:
    open, navigate, click, see, expect, page).
    """
    if agent_testable:
        return True
    return _BROWSER_KW_RE.search(step_text or "") is not None


def classify_uat_step(step_text: str, agent_testable: bool = False) -> str:
    """Route a UAT step to ``"browser"``, ``"http"`` or ``"manual"`` (Step 6).

    * ``"browser"`` — agent-testable flag set or browser-interaction keywords
      present → attempt via :func:`run_browser_step` (AC-1).
    * ``"http"`` — no browser keywords but HTTP/API indicators present → drive
      with httpx unchanged (AC-5).
    * ``"manual"`` — neither → a human must check (visual/layout/etc.).
    """
    if is_browser_step(step_text, agent_testable):
        return "browser"
    if _HTTP_KW_RE.search(step_text or ""):
        return "http"
    return "manual"


def report_status(result: dict) -> str:
    """Map a :func:`run_browser_step` result to a test-report status token.

    ``"pass"`` → ``"PASS"``, ``"fail"`` → ``"FAIL"`` (AC-2). ``"uncovered"`` →
    ``"MANUAL"`` — the only condition under which a browser step is reported
    MANUAL (AC-4). Anything unexpected degrades to ``"MANUAL"``.
    """
    status = result.get("status")
    if status == "pass":
        return "PASS"
    if status == "fail":
        return "FAIL"
    return "MANUAL"


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


def run_cli(args: list[str], timeout: float = _STEP_TIMEOUT_S) -> tuple[int, str, str]:
    """Run a single ``agent-browser`` command and return ``(rc, stdout, stderr)``.

    agent-browser (0.27.x) is a **low-level** command CLI — ``open``, ``click``,
    ``type``, ``find``, ``get``, ``is``, ``screenshot``, ``snapshot``, … — built
    for an AI agent to drive (see ``agent-browser skills get core``). There is no
    high-level "run this task" command. The Tester agent composes these commands
    per UAT step (tester.md Step 6); this is the thin executor they share.

    Never raises: a missing binary / timeout maps to ``(-1, "", <message>)`` so
    the caller can degrade to MANUAL rather than crash the run.
    """
    try:
        r = subprocess.run(
            [BINARY, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"agent-browser timed out after {timeout:.0f}s"
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", f"agent-browser execution error: {exc}"


# ── per-step screenshot capture for sprint UAT (issue #712) ──────────────────

#: Maximum resolution a screenshot is downscaled to before saving (AC-2). Images
#: smaller than this are left untouched (cap, never upscale).
MAX_SCREENSHOT_RESOLUTION = (1280, 800)

#: Long-lived branch screenshots are committed to for inline GitHub embedding.
_ATTACHMENTS_BRANCH = "attachments"


def sprint_screenshot_dir(sprints_dir, sprint_num, issue_num) -> Path:
    """Directory holding a ticket's UAT screenshots (AC-1).

    Layout: ``<sprints_dir>/sprint-<N>/screenshots/issue-<N>/``. ``sprints_dir``
    is the project's ``.commander/sprints`` directory.
    """
    return (
        Path(sprints_dir)
        / f"sprint-{sprint_num}"
        / "screenshots"
        / f"issue-{issue_num}"
    )


def sprint_screenshot_path(sprints_dir, sprint_num, issue_num, step_k) -> Path:
    """Path of the ``step-<k>.png`` screenshot for a browser step (1-indexed)."""
    return sprint_screenshot_dir(sprints_dir, sprint_num, issue_num) / f"step-{step_k}.png"


def _step_num(path: Path) -> int:
    """Extract the integer step index from a ``step-<k>.png`` filename."""
    m = re.search(r"step-(\d+)\.png$", path.name)
    return int(m.group(1)) if m else 0


class ScreenshotRecorder:
    """Save per-step UAT screenshots, capping resolution and skipping dupes.

    One recorder drives one ticket's UAT run. :meth:`capture` is called once per
    browser step in order; it writes ``step-<k>.png`` (1-indexed ``k``) under the
    ticket's screenshot directory. Consecutive identical frames (pixel-hash
    match) are skipped so only the first of a duplicate run is kept (AC-3). Every
    failure mode (bad source, disk write error) is swallowed with a WARNING so a
    capture never aborts the test run (AC-7).
    """

    def __init__(self, sprints_dir, sprint_num, issue_num,
                 max_resolution: tuple[int, int] = MAX_SCREENSHOT_RESOLUTION):
        self.dir = sprint_screenshot_dir(sprints_dir, sprint_num, issue_num)
        self.max_resolution = max_resolution
        self._step = 0
        self._last_hash: Optional[str] = None
        self.saved: list[Path] = []

    @property
    def step_count(self) -> int:
        """Number of browser steps seen so far (including skipped duplicates)."""
        return self._step

    def capture(self, source) -> Optional[Path]:
        """Save a screenshot for the next browser step.

        ``source`` is either a path (``str``/``Path``) to a PNG that
        ``agent-browser`` already wrote, or raw PNG ``bytes``. Returns the written
        ``Path``, or ``None`` if the frame was a consecutive duplicate (AC-3) or
        any error occurred (AC-7). Never raises.
        """
        self._step += 1
        k = self._step
        try:
            png_bytes, pixel_hash = self._normalize(source)
        except Exception as exc:
            log.warning("screenshot capture failed for step %d (issue dir %s): %s",
                        k, self.dir, exc)
            return None

        if self._last_hash is not None and pixel_hash == self._last_hash:
            log.debug("screenshot step %d identical to previous — skipped", k)
            return None

        dest = self.dir / f"step-{k}.png"
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(png_bytes)
        except OSError as exc:
            log.warning("screenshot write failed for %s: %s", dest, exc)
            return None

        self._last_hash = pixel_hash
        self.saved.append(dest)
        return dest

    def _normalize(self, source) -> tuple[bytes, str]:
        """Return (png_bytes, pixel_hash) for a capped, re-encoded screenshot."""
        if isinstance(source, (str, Path)):
            raw = Path(source).read_bytes()
        elif isinstance(source, (bytes, bytearray)):
            raw = bytes(source)
        else:
            raise TypeError(f"unsupported screenshot source: {type(source)!r}")

        if not _PIL_AVAILABLE:
            # No Pillow — store raw, hash the bytes. No resolution cap available.
            return raw, hashlib.sha256(raw).hexdigest()

        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            # thumbnail() only shrinks, preserving aspect ratio (AC-2).
            img.thumbnail(self.max_resolution)
            pixel_hash = hashlib.sha256(img.tobytes()).hexdigest()
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue(), pixel_hash


def collect_issue_screenshots(sprints_dir, sprint_num, issue_num,
                              url_map: Optional[dict] = None) -> list[dict]:
    """Return a ticket's saved screenshots, ordered by step (AC-5).

    Each entry is ``{"step", "filename", "path", "url"}``. ``url`` is taken from
    ``url_map`` (filename -> raw URL) when provided, else ``None``. Returns an
    empty list when the ticket has no screenshot directory (AC-6).
    """
    d = sprint_screenshot_dir(sprints_dir, sprint_num, issue_num)
    if not d.is_dir():
        return []
    shots: list[dict] = []
    for p in sorted(d.glob("step-*.png"), key=_step_num):
        shots.append({
            "step": _step_num(p),
            "filename": p.name,
            "path": p,
            "url": (url_map or {}).get(p.name),
        })
    return shots


def build_screenshot_section(screenshots: list[dict], *,
                             heading: str = "Screenshots",
                             heading_level: int = 3) -> str:
    """Build a markdown section embedding each screenshot inline (AC-4/AC-5).

    With a ``url`` present, a screenshot embeds as ``![step-k](url)``. Without one
    (e.g. the GitHub upload failed) it degrades to a link line so the section is
    still useful (AC-7). Returns an empty string when ``screenshots`` is empty so
    no heading/section is emitted for tickets with no browser steps (AC-6).
    """
    if not screenshots:
        return ""
    hashes = "#" * max(1, heading_level)
    lines = [f"{hashes} {heading} ({len(screenshots)})", ""]
    for s in screenshots:
        label = f"step-{s['step']}"
        url = s.get("url")
        if url:
            lines.append(f"![{label}]({url})")
        else:
            lines.append(f"- {label}: `{s.get('path')}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def upload_screenshots(paths, repo: str, issue_num: int, sprint_num) -> dict:
    """Commit screenshots to the attachments branch, returning {filename: raw_url}.

    Best-effort (AC-7): on any failure a WARNING is logged and ``{}`` is returned
    so report posting continues without inline images rather than aborting.
    """
    paths = [Path(p) for p in paths if Path(p).exists()]
    if not paths:
        return {}
    try:
        _push_screenshots_to_attachments(paths, repo, issue_num, sprint_num)
    except Exception as exc:
        log.warning("screenshot upload to GitHub failed for issue #%s: %s", issue_num, exc)
        return {}

    url_map: dict = {}
    for p in paths:
        dest = f"references/issue-{issue_num}/screenshots/sprint-{sprint_num}/{p.name}"
        url_map[p.name] = (
            f"https://raw.githubusercontent.com/{repo}/{_ATTACHMENTS_BRANCH}/{dest}"
        )

    # Persist the map next to the screenshots so the sprint-summary builder
    # (sprint_manager._load_screenshot_url_map) can resolve raw URLs and embed
    # inline images instead of degrading to local-path links (issue #731). The
    # step-<k>.png files all live in one ticket screenshot dir, so write the
    # manifest to their common parent. Best-effort — a write failure must not
    # abort report posting, mirroring the upload's own graceful contract.
    try:
        manifest = paths[0].parent / "urls.json"
        manifest.write_text(json.dumps(url_map, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("writing urls.json manifest failed for issue #%s: %s", issue_num, exc)

    return url_map


def _push_screenshots_to_attachments(paths: list[Path], repo: str,
                                     issue_num: int, sprint_num) -> None:
    """Commit screenshot files to the orphan attachments branch and push.

    Uses git plumbing against a shallow temp clone so it never touches the
    working tree. Raises on failure; the caller (:func:`upload_screenshots`)
    converts that into a graceful warning.
    """
    remote_url = f"https://github.com/{repo}.git"
    with tempfile.TemporaryDirectory() as tmpdir:
        clone = subprocess.run(
            ["git", "clone", "--depth=1", "--branch", _ATTACHMENTS_BRANCH,
             remote_url, tmpdir],
            capture_output=True, text=True,
        )
        if clone.returncode != 0:
            raise RuntimeError(f"clone of attachments branch failed: {clone.stderr.strip()}")

        dest_dir = Path(tmpdir) / "references" / f"issue-{issue_num}" / "screenshots" / f"sprint-{sprint_num}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for p in paths:
            (dest_dir / p.name).write_bytes(p.read_bytes())

        subprocess.run(["git", "add", "-A"], cwd=tmpdir, check=True,
                       capture_output=True, text=True)
        commit = subprocess.run(
            ["git", "commit", "-m",
             f"chore(screenshots): UAT screenshots for issue #{issue_num} (sprint {sprint_num})"],
            cwd=tmpdir, capture_output=True, text=True,
        )
        # An empty commit (nothing changed) is not an error worth raising on.
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
            raise RuntimeError(f"commit failed: {commit.stderr.strip()}")

        push = subprocess.run(
            ["git", "push", "origin", _ATTACHMENTS_BRANCH],
            cwd=tmpdir, capture_output=True, text=True,
        )
        if push.returncode != 0:
            raise RuntimeError(f"push to attachments branch failed: {push.stderr.strip()}")
