"""Tests for issue #712: Attach browser step screenshots to UAT test reports.

AC-1: agent_browser_runner saves a screenshot after each browser step to
      .commander/sprints/sprint-<N>/screenshots/issue-<N>/step-<k>.png (1-indexed k).
AC-2: Screenshots are capped at a max resolution (1280x800) before saving.
AC-3: Consecutive identical screenshots (pixel-hash match) are skipped; only the
      first of a duplicate run is kept.
AC-4: The test report comment embeds each screenshot inline using GitHub Markdown
      image syntax (![step-k](url)).
AC-5: The sprint summary lists each ticket's screenshots under its section
      (count + inline images or links).
AC-6: A ticket whose UAT run produced zero browser steps adds no screenshot
      section/image block to the report comment or sprint summary.
AC-7: The capture/attach flow fails gracefully (warning logged, no abort) on disk
      write or GitHub upload failure.
AC-8: Existing runs with no browser steps produce identical output (no regression).
"""
from __future__ import annotations

import logging
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.agent_browser_runner as abr

try:
    from PIL import Image  # noqa: F401  (only used for resolution-cap assertions)
    Image.new("RGB", (1, 1))  # force-load the C ext to detect broken installs
    _PIL = True
except Exception:  # Pillow missing or built for the wrong arch -> cap tests skip
    _PIL = False


# ── helpers ──────────────────────────────────────────────────────────────────

def _png_bytes(color, size=(200, 120)):
    """Return raw PNG bytes of a solid-color RGB image (stdlib-only encoder).

    Built without Pillow so the save/dedup/collect tests run on any machine; the
    output is a valid PNG that Pillow can decode for the resolution-cap tests.
    Deterministic: identical (color, size) -> identical bytes, which the no-Pillow
    code path relies on for byte-hash dedup.
    """
    w, h = size
    r, g, b = color
    row = bytes([r, g, b]) * w
    raw = bytearray()
    for _ in range(h):
        raw.append(0)            # filter type 0 (none) per scanline
        raw.extend(row)

    def _chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit, color type 2 (RGB)
    idat = zlib.compress(bytes(raw))
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


# ── AC-1: per-step path convention ───────────────────────────────────────────

def test_sprint_screenshot_path_convention(tmp_path):
    """AC-1: path is sprint-<N>/screenshots/issue-<N>/step-<k>.png, 1-indexed."""
    p = abr.sprint_screenshot_path(tmp_path, 54, 712, 1)
    assert p == tmp_path / "sprint-54" / "screenshots" / "issue-712" / "step-1.png"
    p3 = abr.sprint_screenshot_path(tmp_path, 54, 712, 3)
    assert p3.name == "step-3.png"


def test_recorder_saves_one_file_per_step(tmp_path):
    """AC-1: each distinct browser step writes step-1.png, step-2.png, ..."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    a = rec.capture(_png_bytes((255, 0, 0)))
    b = rec.capture(_png_bytes((0, 255, 0)))
    assert a.name == "step-1.png"
    assert b.name == "step-2.png"
    assert a.exists() and b.exists()
    assert [p.name for p in rec.saved] == ["step-1.png", "step-2.png"]


def test_recorder_accepts_path_source(tmp_path):
    """AC-1: source may be a path to a PNG already written by agent-browser."""
    src = tmp_path / "raw.png"
    src.write_bytes(_png_bytes((10, 20, 30)))
    rec = abr.ScreenshotRecorder(tmp_path / "out", 54, 712)
    saved = rec.capture(src)
    assert saved is not None and saved.exists()
    assert saved.name == "step-1.png"


# ── AC-2: resolution cap ─────────────────────────────────────────────────────

@pytest.mark.skipif(not _PIL, reason="Pillow required")
def test_screenshot_capped_at_max_resolution(tmp_path):
    """AC-2: an oversized screenshot is downscaled to fit within 1280x800."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    saved = rec.capture(_png_bytes((1, 2, 3), size=(4000, 3000)))
    with Image.open(saved) as img:
        w, h = img.size
    assert w <= 1280 and h <= 800
    # aspect ratio preserved (downscale, not crop/stretch)
    assert abs((w / h) - (4000 / 3000)) < 0.05


@pytest.mark.skipif(not _PIL, reason="Pillow required")
def test_small_screenshot_not_upscaled(tmp_path):
    """AC-2: an already-small screenshot keeps its dimensions (cap, not resize-up)."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    saved = rec.capture(_png_bytes((9, 9, 9), size=(320, 200)))
    with Image.open(saved) as img:
        assert img.size == (320, 200)


# ── AC-3: consecutive duplicate dedup ────────────────────────────────────────

def test_consecutive_identical_screenshots_skipped(tmp_path):
    """AC-3: duplicate consecutive frames are skipped; only the first is kept."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    red = _png_bytes((200, 0, 0))
    first = rec.capture(red)
    dup1 = rec.capture(red)
    dup2 = rec.capture(red)
    assert first is not None and first.name == "step-1.png"
    assert dup1 is None and dup2 is None
    # only the first file exists on disk
    files = sorted(p.name for p in (tmp_path / "sprint-54" / "screenshots" / "issue-712").glob("step-*.png"))
    assert files == ["step-1.png"]
    assert [p.name for p in rec.saved] == ["step-1.png"]


def test_non_consecutive_repeat_is_kept(tmp_path):
    """AC-3: only *consecutive* duplicates are skipped (A, B, A keeps all three)."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    a1 = rec.capture(_png_bytes((200, 0, 0)))
    b = rec.capture(_png_bytes((0, 0, 200)))
    a2 = rec.capture(_png_bytes((200, 0, 0)))
    assert a1 is not None and b is not None and a2 is not None
    assert [p.name for p in rec.saved] == ["step-1.png", "step-2.png", "step-3.png"]


# ── AC-7: graceful failure ───────────────────────────────────────────────────

def test_disk_write_failure_is_graceful(tmp_path, caplog):
    """AC-7: a disk write error logs a warning and returns None, never raises."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    with caplog.at_level(logging.WARNING):
        with patch.object(abr.Path, "write_bytes", side_effect=OSError("read-only fs")):
            result = rec.capture(_png_bytes((5, 5, 5)))
    assert result is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.skipif(not _PIL, reason="bad-image detection needs a working image decoder")
def test_bad_source_is_graceful(tmp_path, caplog):
    """AC-7: a non-image / unreadable source logs a warning and returns None."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    with caplog.at_level(logging.WARNING):
        result = rec.capture(b"not a real png")
    assert result is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_upload_failure_is_graceful(tmp_path, caplog):
    """AC-7: a GitHub upload failure logs a warning and returns {} (no raise)."""
    src = tmp_path / "step-1.png"
    src.write_bytes(_png_bytes((1, 1, 1)))
    with caplog.at_level(logging.WARNING):
        with patch.object(abr, "_push_screenshots_to_attachments", side_effect=RuntimeError("boom")):
            urls = abr.upload_screenshots([src], repo="owner/repo", issue_num=712, sprint_num=54)
    assert urls == {}
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_upload_success_returns_raw_url_map(tmp_path):
    """AC-4 source: a successful upload returns {filename: raw github url}."""
    src = tmp_path / "step-1.png"
    src.write_bytes(_png_bytes((1, 1, 1)))
    with patch.object(abr, "_push_screenshots_to_attachments", return_value=None):
        urls = abr.upload_screenshots([src], repo="owner/repo", issue_num=712, sprint_num=54)
    assert "step-1.png" in urls
    assert urls["step-1.png"].startswith("https://raw.githubusercontent.com/owner/repo/")
    assert "step-1.png" in urls["step-1.png"]


# ── AC-4 / AC-5 / AC-6: markdown section builder ─────────────────────────────

def test_build_screenshot_section_uses_image_syntax_with_urls():
    """AC-4: with URLs present, each screenshot embeds inline as ![step-k](url)."""
    shots = [
        {"step": 1, "filename": "step-1.png", "path": Path("/x/step-1.png"),
         "url": "https://raw.example/step-1.png"},
        {"step": 2, "filename": "step-2.png", "path": Path("/x/step-2.png"),
         "url": "https://raw.example/step-2.png"},
    ]
    md = abr.build_screenshot_section(shots)
    assert "![step-1](https://raw.example/step-1.png)" in md
    assert "![step-2](https://raw.example/step-2.png)" in md
    # count surfaced (AC-5)
    assert "2" in md


def test_build_screenshot_section_empty_when_no_shots():
    """AC-6: zero browser steps -> empty string, no section/heading added."""
    assert abr.build_screenshot_section([]) == ""


def test_build_screenshot_section_falls_back_to_links_without_urls():
    """AC-5/AC-7: when upload failed (no urls), the section still lists each step."""
    shots = [{"step": 1, "filename": "step-1.png",
              "path": Path("/x/step-1.png"), "url": None}]
    md = abr.build_screenshot_section(shots)
    assert md != ""
    assert "step-1" in md
    # no broken image embed when there is no url
    assert "![step-1](None)" not in md


# ── AC-5 / AC-6: collect from disk ───────────────────────────────────────────

def test_collect_issue_screenshots_orders_by_step(tmp_path):
    """AC-5: collect returns saved screenshots ordered by step index."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    rec.capture(_png_bytes((1, 0, 0)))
    rec.capture(_png_bytes((0, 1, 0)))
    rec.capture(_png_bytes((0, 0, 1)))
    shots = abr.collect_issue_screenshots(tmp_path, 54, 712)
    assert [s["step"] for s in shots] == [1, 2, 3]
    assert [s["filename"] for s in shots] == ["step-1.png", "step-2.png", "step-3.png"]


def test_collect_issue_screenshots_empty_when_no_dir(tmp_path):
    """AC-6: a ticket with no screenshot dir yields an empty list (no section)."""
    assert abr.collect_issue_screenshots(tmp_path, 54, 999) == []


def test_collect_applies_url_map(tmp_path):
    """AC-4: a provided url map is attached to each collected screenshot."""
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    rec.capture(_png_bytes((1, 0, 0)))
    shots = abr.collect_issue_screenshots(
        tmp_path, 54, 712, url_map={"step-1.png": "https://raw.example/step-1.png"}
    )
    assert shots[0]["url"] == "https://raw.example/step-1.png"


# ── AC-5 / AC-6 / AC-8: sprint summary integration ───────────────────────────

def _import_sprint_manager():
    import importlib
    import services.sprint_manager.sprint_manager as sm
    importlib.reload(sm)
    return sm


def test_sprint_summary_includes_screenshots_section(tmp_path, monkeypatch):
    """AC-5: a completed ticket with screenshots gets a section in the summary."""
    sm = _import_sprint_manager()
    # Lay down screenshots for issue 712 under a fake sprints dir.
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    rec.capture(_png_bytes((1, 0, 0)))
    rec.capture(_png_bytes((0, 1, 0)))

    issue = sm.IssueState(number=712, title="Browser ticket", status="done")
    state = sm.SprintState(sprint_label="sprint-54", sprint_number=54, issues=[issue])

    out = sm.generate_sprint_summary(state, elapsed_secs=60, sprints_dir=tmp_path)
    assert "Screenshots" in out
    assert "#712" in out
    # count surfaced
    assert "2" in out


def test_sprint_summary_no_section_without_screenshots(tmp_path):
    """AC-6/AC-8: a ticket with no browser steps adds no screenshot section."""
    sm = _import_sprint_manager()
    issue = sm.IssueState(number=800, title="API-only ticket", status="done")
    state = sm.SprintState(sprint_label="sprint-54", sprint_number=54, issues=[issue])
    out = sm.generate_sprint_summary(state, elapsed_secs=60, sprints_dir=tmp_path)
    assert "## Screenshots" not in out


def test_sprint_summary_unchanged_signature_default(tmp_path):
    """AC-8: generate_sprint_summary still callable without the new arg (no regression)."""
    sm = _import_sprint_manager()
    issue = sm.IssueState(number=801, title="Legacy ticket", status="done")
    state = sm.SprintState(sprint_label="sprint-54", sprint_number=54, issues=[issue])
    # No sprints_dir passed — must not raise and must not add a screenshot section.
    out = sm.generate_sprint_summary(state, elapsed_secs=60)
    assert "## Screenshots" not in out
    assert "Sprint 54" in out
