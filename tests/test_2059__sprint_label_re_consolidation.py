"""Tests for issue #2059 — Consolidate the sprint-label regex.

AC4: Canonical SPRINT_LABEL_RE accepts sprint-1, sprint-121, sprint-121.2
     and rejects sprint-viz9003, sprint-, sprint-1.2.3.
AC5: No bare re.compile(r"^sprint-\\d...") literal exists outside
     sprint_label_re.py in production source (apps/, services/, scripts/).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

for _p in (str(DASHBOARD_DIR),):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ── AC4: canonical pattern behaviour ─────────────────────────────────────────

def test_canonical_re_accepts_sprint_1():
    from sprint_label_re import SPRINT_LABEL_RE
    assert SPRINT_LABEL_RE.match("sprint-1")


def test_canonical_re_accepts_sprint_121():
    from sprint_label_re import SPRINT_LABEL_RE
    assert SPRINT_LABEL_RE.match("sprint-121")


def test_canonical_re_accepts_sprint_121_2():
    from sprint_label_re import SPRINT_LABEL_RE
    assert SPRINT_LABEL_RE.match("sprint-121.2")


def test_canonical_re_rejects_sprint_viz9003():
    from sprint_label_re import SPRINT_LABEL_RE
    assert not SPRINT_LABEL_RE.match("sprint-viz9003")


def test_canonical_re_rejects_sprint_bare_dash():
    from sprint_label_re import SPRINT_LABEL_RE
    assert not SPRINT_LABEL_RE.match("sprint-")


def test_canonical_re_rejects_sprint_1_2_3():
    from sprint_label_re import SPRINT_LABEL_RE
    assert not SPRINT_LABEL_RE.match("sprint-1.2.3")


# ── AC5: drift guard ─────────────────────────────────────────────────────────

_CANONICAL_MODULE = DASHBOARD_DIR / "sprint_label_re.py"
_SCAN_DIRS = [DASHBOARD_DIR, REPO_ROOT / "services", REPO_ROOT / "scripts"]

# Literal strings to search for in source files.  Each represents the prefix
# of a sprint-label validation compile call (re or aliased _re / _compile).
# We look for the \d that follows ^sprint-, not a capture group (,  so
# extraction patterns like r"^sprint-(\d+)..." are deliberately not caught.
_BARE_COMPILE_NEEDLES = [
    're.compile(r"^sprint-\\d',   # double-quote form → re.compile(r"^sprint-\d
    "re.compile(r'^sprint-\\d",   # single-quote form → re.compile(r'^sprint-\d
    '_re.compile(r"^sprint-\\d',  # aliased form (double)
    "_re.compile(r'^sprint-\\d",  # aliased form (single)
]


def _find_bare_compile_sites() -> list[str]:
    violations = []
    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for py_file in sorted(scan_dir.rglob("*.py")):
            if py_file == _CANONICAL_MODULE:
                continue
            # Skip test files — they may inline the pattern for monkeypatching.
            if "test" in py_file.name:
                continue
            parts = py_file.parts
            if any(p in ("tests", "__pycache__") for p in parts):
                continue
            try:
                src = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if any(needle in src for needle in _BARE_COMPILE_NEEDLES):
                violations.append(str(py_file.relative_to(REPO_ROOT)))
    return violations


def test_no_bare_sprint_label_re_compile_outside_canonical():
    violations = _find_bare_compile_sites()
    assert not violations, (
        "Bare re.compile(r\"^sprint-\\d...\") literals found outside "
        "sprint_label_re.py — import SPRINT_LABEL_RE instead:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
