"""Tester AC verification for issue #712: Attach browser step screenshots to UAT test reports.

This feature is backend/script logic in services/sprint_manager/agent_browser_runner.py
(screenshot capture, resolution cap, dedup, markdown embedding, graceful failure)
plus a sprint-summary integration in sprint_manager.py. It exposes no HTTP surface,
so these tests exercise the module directly rather than hitting a UAT server.

One test function per acceptance criterion.
"""
from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.agent_browser_runner as abr

try:
    from PIL import Image
    Image.new("RGB", (1, 1))
    _PIL = True
except Exception:  # pragma: no cover
    _PIL = False


def _png(color=(10, 20, 30), size=(200, 120)) -> bytes:
    """Return raw PNG bytes of a solid-color RGB image (Pillow-encoded)."""
    if not _PIL:
        pytest.skip("Pillow not installed — cannot build PNG fixture")
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# --- Acceptance Criteria ---

def test_attach_browser_step_screenshots__saves_step_k_png_at_sprint_path(tmp_path):
    # AC-1: recorder saves step-<k>.png (1-indexed) under
    # <sprints>/sprint-<N>/screenshots/issue-<N>/
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    p1 = rec.capture(_png((1, 2, 3)))
    p2 = rec.capture(_png((9, 8, 7)))
    expected_dir = abr.sprint_screenshot_dir(tmp_path, 54, 712)
    assert expected_dir == tmp_path / "sprint-54" / "screenshots" / "issue-712"
    assert p1 == expected_dir / "step-1.png"
    assert p2 == expected_dir / "step-2.png"
    assert p1.exists() and p2.exists()


def test_attach_browser_step_screenshots__capped_at_max_resolution(tmp_path):
    # AC-2: screenshots downscaled to <= 1280x800 before saving.
    if not _PIL:
        pytest.skip("manual — resolution cap requires Pillow")
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    dest = rec.capture(_png((4, 5, 6), size=(4000, 3000)))
    with Image.open(dest) as img:
        w, h = img.size
    assert w <= 1280 and h <= 800, f"not capped: {w}x{h}"


def test_attach_browser_step_screenshots__consecutive_duplicates_skipped(tmp_path):
    # AC-3: consecutive identical frames skipped; only first kept.
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    same = _png((100, 100, 100))
    a = rec.capture(same)
    b = rec.capture(same)
    assert a is not None and a.exists()
    assert b is None, "consecutive duplicate should be skipped"
    assert len(rec.saved) == 1
    # step counter still advances so later distinct frames keep their index
    assert rec.step_count == 2


def test_attach_browser_step_screenshots__report_embeds_inline_image_syntax(tmp_path):
    # AC-4: report section embeds each screenshot as ![step-k](url).
    shots = [
        {"step": 1, "filename": "step-1.png", "path": tmp_path / "step-1.png",
         "url": "https://raw.githubusercontent.com/o/r/attachments/x/step-1.png"},
        {"step": 2, "filename": "step-2.png", "path": tmp_path / "step-2.png",
         "url": "https://raw.githubusercontent.com/o/r/attachments/x/step-2.png"},
    ]
    section = abr.build_screenshot_section(shots)
    assert "![step-1](https://raw.githubusercontent.com/o/r/attachments/x/step-1.png)" in section
    assert "![step-2](https://raw.githubusercontent.com/o/r/attachments/x/step-2.png)" in section


def test_attach_browser_step_screenshots__sprint_summary_lists_count_and_images(tmp_path):
    # AC-5: section lists the ticket's screenshots with a count + inline images.
    d = abr.sprint_screenshot_dir(tmp_path, 54, 712)
    d.mkdir(parents=True)
    (d / "step-1.png").write_bytes(_png((1, 1, 1)))
    (d / "step-2.png").write_bytes(_png((2, 2, 2)))
    url_map = {"step-1.png": "http://x/1.png", "step-2.png": "http://x/2.png"}
    shots = abr.collect_issue_screenshots(tmp_path, 54, 712, url_map=url_map)
    assert [s["step"] for s in shots] == [1, 2]
    section = abr.build_screenshot_section(
        shots, heading="Issue #712 — screenshots", heading_level=3)
    assert "(2)" in section.splitlines()[0]  # count in heading
    assert "![step-1](http://x/1.png)" in section


def test_attach_browser_step_screenshots__zero_browser_steps_no_section(tmp_path):
    # AC-6: no screenshot dir / no shots => empty list and empty section.
    shots = abr.collect_issue_screenshots(tmp_path, 54, 999)
    assert shots == []
    assert abr.build_screenshot_section(shots) == ""


def test_attach_browser_step_screenshots__graceful_on_disk_and_upload_failure(tmp_path, caplog):
    # AC-7: disk-write failure and GitHub-upload failure both degrade gracefully
    # (warning logged, no exception, no abort).
    rec = abr.ScreenshotRecorder(tmp_path, 54, 712)
    with caplog.at_level(logging.WARNING):
        with patch.object(Path, "write_bytes", side_effect=OSError("read-only fs")):
            result = rec.capture(_png((7, 7, 7)))
    assert result is None
    assert rec.saved == []
    assert any("write failed" in r.message.lower() or "write failed" in r.getMessage().lower()
               for r in caplog.records)

    # upload failure -> empty url map, no raise
    real = tmp_path / "step-1.png"
    real.write_bytes(_png((8, 8, 8)))
    with caplog.at_level(logging.WARNING):
        with patch.object(abr, "_push_screenshots_to_attachments",
                          side_effect=RuntimeError("push failed")):
            urls = abr.upload_screenshots([real], "o/r", 712, 54)
    assert urls == {}


def test_attach_browser_step_screenshots__no_regression_without_sprints_dir(tmp_path):
    # AC-8: build_screenshot_section returns nothing for legacy/empty input, and
    # the sprint-summary integration adds no Screenshots section when no ticket
    # has browser screenshots (identical output to today).
    assert abr.build_screenshot_section([]) == ""
    # collect over a clean tree yields nothing => integration emits no block
    assert abr.collect_issue_screenshots(tmp_path, 54, 712) == []
