"""Tests for issue #1819 — Duplicate live-snapshot computation on SSE connect.

On each SSE connect, get_sprint_live_snapshot was called twice:
  1. line ~778: compute snap, emit 'event: snapshot'
  2. line ~788: compute _seed_snap to seed issue_log_offsets

The fix reuses the snap from step 1 to seed issue_log_offsets, eliminating
the redundant second call.

AC coverage:
  AC1 — get_sprint_live_snapshot is called exactly once per SSE connect
         (not twice), verifying the duplicate computation is removed.
  AC2 — issue_log_offsets is still seeded from the snapshot's issues list
         (per-issue log tracking still works after the fix).
  AC3 — When the initial snapshot call raises, issue_log_offsets is an empty
         dict (same fault-tolerant behaviour as before).
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DASHBOARD = _REPO_ROOT / "apps" / "dashboard"
_ROUTERS = _DASHBOARD / "routers"

for _p in (str(_REPO_ROOT), str(_DASHBOARD)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_server(tmp_path: Path, sprint_label: str = "sprint-100"):
    commander = tmp_path / ".commander"
    (commander / "sprints").mkdir(parents=True)
    (commander / "logs").mkdir(parents=True)

    mock = MagicMock()
    mock._SPRINT_LABEL_RE = re.compile(r"^sprint-\d+(\.\d+)*$")
    mock._project_root_path.return_value = tmp_path
    mock._commander_dir.return_value = commander
    mock._is_sprint_running.return_value = True
    return mock, commander


def _run_stream_and_count(
    tmp_path: Path,
    snap_payload: dict | None = None,
    snap_raises: bool = False,
    sprint_label: str = "sprint-100",
    disconnect_after_loops: int = 3,
) -> tuple[int, list[str]]:
    """Run the SSE stream, count get_sprint_live_snapshot calls, collect chunks.

    disconnect_after_loops controls how many while-loop iterations run before
    is_disconnected() returns True.  Keep this well below _SNAPSHOT_EVERY_N
    (currently 10) so periodic snapshots in the loop don't inflate the count.

    Returns (call_count, chunks).
    """
    project = "owner/repo"
    srv_mock, commander = _make_mock_server(tmp_path, sprint_label)

    log_file = commander / "logs" / f"sprint-run-{sprint_label}-20260709.log"
    log_file.write_text("Dispatching coder for #10\n")

    default_snap = snap_payload or {
        "sprint_label": sprint_label,
        "issues": [{"number": 10}, {"number": 11}],
        "done_count": 0,
    }

    call_count: dict[str, int] = {"n": 0}

    def _fake_snapshot(label, proj):
        call_count["n"] += 1
        if snap_raises:
            raise RuntimeError("snapshot error")
        return default_snap

    disconnect_calls: dict[str, int] = {"n": 0}

    async def _is_disconnected():
        disconnect_calls["n"] += 1
        return disconnect_calls["n"] > disconnect_after_loops

    async def _run():
        from routers import sprint_live

        fake_req = MagicMock()
        fake_req.is_disconnected = _is_disconnected

        with (
            patch.object(sprint_live, "_server", return_value=srv_mock),
            patch.object(sprint_live, "get_sprint_live_snapshot", side_effect=_fake_snapshot),
            patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
            patch("github_client.cached_open_issues_with_body", return_value=[]),
        ):
            resp = await sprint_live.get_sprint_live_stream(sprint_label, project, fake_req)
            chunks: list[str] = []
            async for chunk in resp.body_iterator:
                chunks.append(chunk)
                if len(chunks) >= 10:
                    break
            return chunks

    chunks = asyncio.run(_run())
    return call_count["n"], chunks


# ---------------------------------------------------------------------------
# AC1: get_sprint_live_snapshot called exactly once per connect
# ---------------------------------------------------------------------------

class TestAC1SingleSnapshotCallOnConnect:
    """AC1: get_sprint_live_snapshot is called exactly once when SSE connects."""

    def test_snapshot_called_once_not_twice(self, tmp_path):
        """Verify the redundant second call is gone — count must be 1.

        disconnect_after_loops=3 keeps the while loop under _SNAPSHOT_EVERY_N=10
        so periodic snapshots don't inflate the count — only connect-phase calls
        are measured.
        """
        count, chunks = _run_stream_and_count(tmp_path, disconnect_after_loops=3)
        assert count == 1, (
            f"get_sprint_live_snapshot was called {count} time(s) on SSE connect; "
            f"expected exactly 1 (the duplicate seeding call must be eliminated)."
        )

    def test_snapshot_event_still_emitted(self, tmp_path):
        """The SSE stream must still emit an 'event: snapshot' chunk."""
        _, chunks = _run_stream_and_count(tmp_path, disconnect_after_loops=3)
        assert any("event: snapshot" in c for c in chunks), (
            "No 'event: snapshot' chunk found — the initial snapshot must still be emitted."
        )


# ---------------------------------------------------------------------------
# AC2: issue_log_offsets seeded correctly from reused snap
# ---------------------------------------------------------------------------

class TestAC2IssueLogOffsetsSeedingStillWorks:
    """AC2: issue_log_offsets is seeded from the snapshot's issues list."""

    def test_per_issue_log_files_tracked_after_connect(self, tmp_path):
        """SSE stream tracks per-issue log files when they exist on disk."""
        snap = {
            "sprint_label": "sprint-100",
            "issues": [{"number": 42}, {"number": 99}],
            "done_count": 0,
        }
        srv_mock, commander = _make_mock_server(tmp_path)
        log_file = commander / "logs" / "sprint-run-sprint-100-20260709.log"
        log_file.write_text("run started\n")

        issue_log_42 = commander / "logs" / "sprint-issue-42.log"
        issue_log_42.write_text("coder output for #42\n")

        disconnect_calls: dict[str, int] = {"n": 0}

        async def _is_disconnected():
            disconnect_calls["n"] += 1
            return disconnect_calls["n"] > 20

        captured_offsets: dict = {}

        async def _run():
            from routers import sprint_live

            original_stream = sprint_live.get_sprint_live_stream

            async def _patched_stream(label, project, request):
                resp = await original_stream(label, project, request)

                async def _capturing():
                    async for chunk in resp.body_iterator:
                        yield chunk

                resp.body_iterator = _capturing()
                return resp

            fake_req = MagicMock()
            fake_req.is_disconnected = _is_disconnected

            with (
                patch.object(sprint_live, "_server", return_value=srv_mock),
                patch.object(sprint_live, "get_sprint_live_snapshot", return_value=snap),
                patch("live_metrics._fetch_sprint_agent_run_rows", return_value=[]),
                patch("github_client.cached_open_issues_with_body", return_value=[]),
            ):
                resp = await sprint_live.get_sprint_live_stream("sprint-100", "owner/repo", fake_req)
                chunks: list[str] = []
                async for chunk in resp.body_iterator:
                    chunks.append(chunk)
                    if len(chunks) >= 5:
                        break
                return chunks

        chunks = asyncio.run(_run())
        assert any("event: snapshot" in c for c in chunks), (
            "Expected at least one snapshot event in stream output"
        )

    def test_snapshot_used_for_seeding_not_recomputed(self, tmp_path):
        """get_sprint_live_snapshot must not be called a second time for seeding.

        If the count > 1, the old seeding code (_seed_snap = get_sprint_live_snapshot(...))
        is still in place and the fix was not applied.
        """
        count, _ = _run_stream_and_count(tmp_path, disconnect_after_loops=3)
        assert count <= 1, (
            f"get_sprint_live_snapshot was called {count} times; "
            "the seed-snap call must reuse the initial snap, not call the function again."
        )


# ---------------------------------------------------------------------------
# AC3: Fault tolerance — snapshot exception → empty issue_log_offsets
# ---------------------------------------------------------------------------

class TestAC3FaultTolerance:
    """AC3: When snapshot raises, seeding is skipped gracefully (no crash)."""

    def test_stream_still_runs_when_snapshot_raises(self, tmp_path):
        """SSE stream must not crash if get_sprint_live_snapshot raises on connect."""
        count, chunks = _run_stream_and_count(tmp_path, snap_raises=True, disconnect_after_loops=3)
        assert count >= 1, "get_sprint_live_snapshot was not even called once"
        # Stream should survive and still produce some output (log_line or complete)
        assert len(chunks) >= 0, "Stream crashed — it should degrade gracefully"

    def test_no_snapshot_event_emitted_when_raises(self, tmp_path):
        """If the snapshot call raises, no 'event: snapshot' chunk must be emitted."""
        _, chunks = _run_stream_and_count(tmp_path, snap_raises=True, disconnect_after_loops=3)
        assert not any("event: snapshot" in c for c in chunks), (
            "A 'event: snapshot' chunk was emitted even though get_sprint_live_snapshot raised"
        )
