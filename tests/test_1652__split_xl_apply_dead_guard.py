"""Tests for issue #1652 — dead if-ok guard after HTTPException raise in split_xl_apply.

The `if result.get("ok"):` conditional at line 196 of sprint_history.py is unreachable
dead code: the preceding `if not result.get("ok"): raise HTTPException(500, ...)` block
ensures only the ok=True case ever reaches that line. This ticket removes the dead guard
and makes the success-path code unconditional.

AC coverage:
  AC1: The dead `if result.get("ok"):` guard is removed — success-path code runs
       unconditionally after the failure guard.
  AC2: The HTTPException(500, ...) raise path remains intact and unchanged.
  AC3: The success-path logic executes unconditionally after the failure guard.
  AC4: No functional behavior changes — only the dead conditional wrapper is removed.
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))

from apps.dashboard.routers import sprint_history, split_xl_service  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def _ok_result():
    return {
        "ok": True,
        "closed": 100,
        "children": [{"number": 5001, "url": "u", "title": "Child one"}],
    }


def _body():
    return sprint_history.SplitXlApplyBody(
        project="o/r",
        children=[{"title": "Child one", "body": "## Acceptance Criteria\n- a"}],
    )


# ── AC1: dead guard is absent from the source ─────────────────────────────────


def test_ac1_no_dead_ok_guard_in_split_xl_apply_source():
    """AC1: the dead `if result.get("ok"):` guard must not exist in split_xl_apply."""
    src = inspect.getsource(sprint_history.split_xl_apply)
    # The only allowable `if result.get("ok")` patterns are negated guards.
    # A bare positive guard (`if result.get("ok"):`) after the raise is dead code.
    lines = src.splitlines()
    positive_guards = [
        ln for ln in lines
        if "if result.get(\"ok\")" in ln and "not" not in ln
    ]
    assert positive_guards == [], (
        "Dead positive guard found — remove `if result.get(\"ok\"): ` "
        "and make the success block unconditional. Offending lines:\n"
        + "\n".join(positive_guards)
    )


# ── AC2: failure path raises HTTP 500 unchanged ───────────────────────────────


def test_ac2_failure_path_raises_http500(monkeypatch):
    """AC2: a not-ok result still raises HTTPException(500, ...) with structured detail."""
    from fastapi import HTTPException

    fail_result = {
        "ok": False,
        "partial": True,
        "error": "Split failed: children [5001] created.",
        "created_numbers": [5001],
        "compensation": {
            "attempted": True,
            "closed": [5001],
            "failed": [],
            "succeeded": True,
            "manual_cleanup_required": False,
        },
    }
    monkeypatch.setattr(split_xl_service, "split_apply", lambda *a, **k: fail_result)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(sprint_history.split_xl_apply("sprint-99.1", 100, _body()))

    assert exc_info.value.status_code == 500
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == fail_result["error"]
    assert detail["partial"] is True
    assert detail["created"] == [5001]
    assert detail["compensation"] == fail_result["compensation"]


# ── AC3: success-path executes unconditionally after the failure guard ─────────


def test_ac3_success_path_executes_unconditionally(monkeypatch):
    """AC3: broadcast is called and result returned even without conditional ok check."""
    monkeypatch.setattr(split_xl_service, "split_apply", lambda *a, **k: _ok_result())

    broadcast_calls = []

    async def fake_broadcast(msg):
        broadcast_calls.append(msg)

    fake_srv = MagicMock()
    fake_srv.broadcast = fake_broadcast

    import builtins
    real_import = builtins.__import__

    def patched_import(name, *args, **kwargs):
        if name == "server":
            return fake_srv
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", patched_import)

    result = asyncio.run(sprint_history.split_xl_apply("sprint-99.1", 100, _body()))

    assert result["ok"] is True
    assert result["closed"] == 100
    assert len(broadcast_calls) == 1
    broadcast_msg = broadcast_calls[0]
    assert broadcast_msg["type"] == "update"
    assert broadcast_msg["event"]["event_type"] == "sprint_split"


# ── AC4: no functional behavior change on success ─────────────────────────────


def test_ac4_success_returns_full_result(monkeypatch):
    """AC4: successful split_apply result is returned as-is, no keys stripped or added."""
    ok = _ok_result()
    monkeypatch.setattr(split_xl_service, "split_apply", lambda *a, **k: dict(ok))

    import builtins
    real_import = builtins.__import__

    def _no_server(name, *args, **kwargs):
        if name == "server":
            raise ImportError("no server in test context")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_server)

    result = asyncio.run(sprint_history.split_xl_apply("sprint-99.1", 100, _body()))

    assert result == ok


def test_ac4_broadcast_failure_does_not_suppress_result(monkeypatch):
    """AC4: if broadcast raises, the result is still returned (exception is swallowed)."""
    monkeypatch.setattr(split_xl_service, "split_apply", lambda *a, **k: _ok_result())

    import builtins
    real_import = builtins.__import__

    fake_srv = MagicMock()
    fake_srv.broadcast = AsyncMock(side_effect=RuntimeError("SSE down"))

    def _bad_server(name, *args, **kwargs):
        if name == "server":
            return fake_srv
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _bad_server)

    result = asyncio.run(sprint_history.split_xl_apply("sprint-99.1", 100, _body()))
    assert result["ok"] is True
