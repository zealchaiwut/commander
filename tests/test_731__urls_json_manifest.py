"""Tests for issue #731: Write urls.json manifest so sprint summary embeds screenshots inline.

Context: follow-up on #712. ``upload_screenshots()`` builds a {filename: raw_url}
map but never persists it. The sprint-summary builder ``_load_screenshot_url_map()``
reads ``<screenshot_dir>/urls.json`` to embed inline images, but nothing writes that
file, so the executive summary always degrades to local-path links.

Acceptance criteria (from the suggested fix):
AC-1: On a successful upload, ``upload_screenshots()`` writes the returned
      {filename: raw_url} map to ``<screenshot_dir>/urls.json`` (the directory
      holding the step-<k>.png files).
AC-2: The manifest written by ``upload_screenshots()`` is readable by
      ``sprint_manager._load_screenshot_url_map()`` and round-trips the same map.
AC-3: On upload failure (returns {}), no misleading urls.json manifest is written.
AC-4: No regression — a successful upload still returns the {filename: raw_url} map.
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.sprint_manager.agent_browser_runner as abr


def _png_bytes(rgb=(1, 1, 1)) -> bytes:
    """Minimal valid 1x1 PNG so written screenshot files exist on disk."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes(rgb)
    idat = zlib.compress(raw)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _screenshot_dir(sprints_dir: Path, sprint_num, issue_num) -> Path:
    d = abr.sprint_screenshot_dir(sprints_dir, sprint_num, issue_num)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_upload_writes_urls_json_manifest(tmp_path):
    """AC-1: a successful upload persists the url map to <screenshot_dir>/urls.json."""
    d = _screenshot_dir(tmp_path, 54, 731)
    src = d / "step-1.png"
    src.write_bytes(_png_bytes())

    with patch.object(abr, "_push_screenshots_to_attachments", return_value=None):
        urls = abr.upload_screenshots([src], repo="owner/repo", issue_num=731, sprint_num=54)

    manifest = d / "urls.json"
    assert manifest.exists(), "urls.json should be written to the screenshot dir"
    assert json.loads(manifest.read_text(encoding="utf-8")) == urls


def test_manifest_round_trips_through_loader(tmp_path):
    """AC-2: the manifest written at upload time is read back by _load_screenshot_url_map."""
    import importlib
    import services.sprint_manager.sprint_manager as sm
    importlib.reload(sm)

    d = _screenshot_dir(tmp_path, 54, 731)
    src = d / "step-1.png"
    src.write_bytes(_png_bytes())

    with patch.object(abr, "_push_screenshots_to_attachments", return_value=None):
        urls = abr.upload_screenshots([src], repo="owner/repo", issue_num=731, sprint_num=54)

    loaded = sm._load_screenshot_url_map(tmp_path, 54, 731)
    assert loaded == urls
    assert loaded["step-1.png"].startswith("https://raw.githubusercontent.com/owner/repo/")


def test_failed_upload_writes_no_manifest(tmp_path):
    """AC-3: an upload failure returns {} and writes no misleading urls.json."""
    d = _screenshot_dir(tmp_path, 54, 731)
    src = d / "step-1.png"
    src.write_bytes(_png_bytes())

    with patch.object(abr, "_push_screenshots_to_attachments", side_effect=RuntimeError("boom")):
        urls = abr.upload_screenshots([src], repo="owner/repo", issue_num=731, sprint_num=54)

    assert urls == {}
    assert not (d / "urls.json").exists()


def test_upload_success_still_returns_url_map(tmp_path):
    """AC-4: no regression — successful upload still returns the {filename: raw_url} map."""
    d = _screenshot_dir(tmp_path, 54, 731)
    src = d / "step-1.png"
    src.write_bytes(_png_bytes())

    with patch.object(abr, "_push_screenshots_to_attachments", return_value=None):
        urls = abr.upload_screenshots([src], repo="owner/repo", issue_num=731, sprint_num=54)

    assert "step-1.png" in urls
    assert urls["step-1.png"].startswith("https://raw.githubusercontent.com/owner/repo/")
