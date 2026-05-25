"""Tests for issue #29 — fix tokens-today metric.

AC-1: /api/debug/token-usage returns row_count, latest_recorded_at, tokens_today.
AC-2: Endpoint shape is correct (integer row_count, ISO-8601 or null, integer).
AC-3: tokens_today in /api/projects metrics matches debug endpoint value.
AC-4: post_tool_used.py logs to stderr when discarding a payload with no tokens.
AC-5: _read_last_usage_from_transcript() correctly parses the JSONL transcript.

Root cause: Claude Code does NOT include token usage fields in PostToolUse hook
payloads. Token data is in the JSONL transcript file written by Claude Code to
~/.claude/projects/<project>/<session>.jsonl under assistant entries'
message.usage field. The hook reads that file via the transcript_path payload
field to get real token counts.
"""
import json
import subprocess
import sys
import tempfile
import os
import pytest
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_hook_module():
    """Import post_tool_used without executing main()."""
    import importlib.util

    hook_path = Path(__file__).parent.parent / "hooks" / "post_tool_used.py"
    spec = importlib.util.spec_from_file_location("post_tool_used", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_transcript_line(input_tokens: int, output_tokens: int,
                          cache_read: int = 0, session_id: str = "abc123") -> str:
    """Return a JSONL line simulating a Claude Code assistant transcript entry."""
    entry = {
        "type": "assistant",
        "sessionId": session_id,
        "cwd": "/tmp",
        "timestamp": "2026-05-25T00:00:00.000Z",
        "message": {
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": 0,
            }
        }
    }
    return json.dumps(entry)


# ---------------------------------------------------------------------------
# AC-5: Unit-test _read_last_usage_from_transcript() from the hook module
# ---------------------------------------------------------------------------

class TestReadLastUsageFromTranscript:
    """AC-5: _read_last_usage_from_transcript() correctly reads JSONL transcripts."""

    @pytest.fixture(scope="class")
    def hook(self):
        return _import_hook_module()

    def test_reads_token_counts_from_jsonl(self, hook, tmp_path):
        """Reads input_tokens + cache_read and output_tokens from a transcript."""
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            _make_transcript_line(input_tokens=500, output_tokens=150) + "\n"
        )
        inp, out = hook._read_last_usage_from_transcript(str(transcript))
        # input_tokens(500) + cache_read(0) = 500
        assert inp == 500
        assert out == 150

    def test_cache_read_tokens_added_to_input(self, hook, tmp_path):
        """cache_read_input_tokens are included in the returned input count."""
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            _make_transcript_line(input_tokens=100, output_tokens=50, cache_read=200) + "\n"
        )
        inp, out = hook._read_last_usage_from_transcript(str(transcript))
        assert inp == 300  # 100 + 200
        assert out == 50

    def test_returns_most_recent_entry(self, hook, tmp_path):
        """Returns usage from the last assistant entry, not the first."""
        transcript = tmp_path / "session.jsonl"
        lines = [
            _make_transcript_line(input_tokens=10, output_tokens=5),
            _make_transcript_line(input_tokens=999, output_tokens=888),
        ]
        transcript.write_text("\n".join(lines) + "\n")
        inp, out = hook._read_last_usage_from_transcript(str(transcript))
        assert inp == 999
        assert out == 888

    def test_returns_zeros_for_missing_file(self, hook, tmp_path):
        """Returns (0, 0) when the transcript file does not exist."""
        inp, out = hook._read_last_usage_from_transcript(str(tmp_path / "nonexistent.jsonl"))
        assert inp == 0
        assert out == 0

    def test_returns_zeros_for_empty_transcript(self, hook, tmp_path):
        """Returns (0, 0) when the transcript has no assistant entries."""
        transcript = tmp_path / "session.jsonl"
        non_assistant = json.dumps({"type": "user", "message": "hello"})
        transcript.write_text(non_assistant + "\n")
        inp, out = hook._read_last_usage_from_transcript(str(transcript))
        assert inp == 0
        assert out == 0

    def test_skips_entries_with_zero_usage(self, hook, tmp_path):
        """Skips entries where both token counts are zero; finds next valid one."""
        transcript = tmp_path / "session.jsonl"
        lines = [
            _make_transcript_line(input_tokens=300, output_tokens=100),
            _make_transcript_line(input_tokens=0, output_tokens=0),
        ]
        transcript.write_text("\n".join(lines) + "\n")
        inp, out = hook._read_last_usage_from_transcript(str(transcript))
        # zero-usage entry is skipped, falls back to the previous valid entry
        assert inp == 300
        assert out == 100

    def test_handles_malformed_json_lines(self, hook, tmp_path):
        """Malformed JSON lines in the transcript are skipped without crashing."""
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            "not-json\n" +
            _make_transcript_line(input_tokens=200, output_tokens=75) + "\n"
        )
        inp, out = hook._read_last_usage_from_transcript(str(transcript))
        assert inp == 200
        assert out == 75

    def test_empty_string_path_returns_zeros(self, hook):
        """Empty transcript_path returns (0, 0) without error."""
        inp, out = hook._read_last_usage_from_transcript("")
        assert inp == 0
        assert out == 0


# ---------------------------------------------------------------------------
# AC-4: stderr logging when payload is discarded
# ---------------------------------------------------------------------------

class TestHookStderrLogging:
    """AC-4: hook logs to stderr when it discards a no-token payload."""

    def test_empty_payload_logs_to_stderr(self):
        """Hook with no transcript_path emits a stderr skip message."""
        hook_path = str(Path(__file__).parent.parent / "hooks" / "post_tool_used.py")
        payload = json.dumps({"session_id": "test", "tool_use": {}})

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, "Hook must exit 0 even when skipping"
        assert "[post_tool_used] skipped" in result.stderr, (
            f"Expected skip message in stderr, got: {result.stderr!r}"
        )

    def test_valid_transcript_does_not_log_skip(self, tmp_path):
        """Hook does NOT emit skip message when transcript has token data."""
        hook_path = str(Path(__file__).parent.parent / "hooks" / "post_tool_used.py")

        transcript = tmp_path / "session.jsonl"
        transcript.write_text(
            _make_transcript_line(input_tokens=100, output_tokens=50) + "\n"
        )

        payload = json.dumps({
            "session_id": "test",
            "cwd": "/tmp",
            "transcript_path": str(transcript),
        })

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload,
            capture_output=True,
            text=True,
        )
        # Hook may fail to POST (no running server), but must not log "skipped"
        assert "skipped" not in result.stderr, (
            f"Unexpected 'skipped' in stderr: {result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# AC-1 & AC-2: GET /api/debug/token-usage endpoint shape
# ---------------------------------------------------------------------------

class TestDebugTokenUsageEndpoint:
    """AC-1 & AC-2: /api/debug/token-usage exists and returns the correct shape."""

    def test_endpoint_returns_200(self, client):
        """GET /api/debug/token-usage returns HTTP 200."""
        res = client.get("/api/debug/token-usage")
        assert res.status_code == 200

    def test_response_is_json(self, client):
        """Response body is valid JSON."""
        res = client.get("/api/debug/token-usage")
        data = res.json()
        assert isinstance(data, dict)

    def test_required_keys_present(self, client):
        """AC-2: response contains row_count, latest_recorded_at, tokens_today."""
        res = client.get("/api/debug/token-usage")
        data = res.json()
        assert "row_count" in data, "Missing 'row_count'"
        assert "latest_recorded_at" in data, "Missing 'latest_recorded_at'"
        assert "tokens_today" in data, "Missing 'tokens_today'"

    def test_row_count_is_integer(self, client):
        """row_count is an integer >= 0."""
        res = client.get("/api/debug/token-usage")
        data = res.json()
        assert isinstance(data["row_count"], int)
        assert data["row_count"] >= 0

    def test_tokens_today_is_integer(self, client):
        """tokens_today is an integer >= 0."""
        res = client.get("/api/debug/token-usage")
        data = res.json()
        assert isinstance(data["tokens_today"], int)
        assert data["tokens_today"] >= 0

    def test_latest_recorded_at_is_string_or_null(self, client):
        """latest_recorded_at is an ISO-8601 string or null."""
        res = client.get("/api/debug/token-usage")
        data = res.json()
        val = data["latest_recorded_at"]
        assert val is None or isinstance(val, str), (
            f"latest_recorded_at must be string or null, got {type(val)}"
        )

    def test_tokens_today_lte_row_count_implies_rows(self, client):
        """tokens_today > 0 implies row_count > 0."""
        res = client.get("/api/debug/token-usage")
        data = res.json()
        if data["tokens_today"] > 0:
            assert data["row_count"] > 0, (
                "tokens_today > 0 but row_count is 0 — inconsistent state"
            )


# ---------------------------------------------------------------------------
# AC-3: tokens_today in /api/projects metrics matches debug endpoint
# ---------------------------------------------------------------------------

class TestTokensTodayConsistency:
    """AC-3: Tokens Today card value is consistent with the debug endpoint."""

    def test_projects_metrics_has_tokens_today(self, client):
        """GET /api/projects returns metrics.tokens_today."""
        res = client.get("/api/projects")
        assert res.status_code == 200
        data = res.json()
        assert "metrics" in data
        assert "tokens_today" in data["metrics"]

    def test_tokens_today_is_non_negative(self, client):
        """metrics.tokens_today is an integer >= 0."""
        res = client.get("/api/projects")
        data = res.json()
        val = data["metrics"]["tokens_today"]
        assert isinstance(val, int)
        assert val >= 0

    def test_tokens_today_consistent_with_debug_endpoint(self, client):
        """metrics.tokens_today from /api/projects matches /api/debug/token-usage.tokens_today.

        Both endpoints read from the same DB column so they should agree
        (within a small window, since two HTTP calls are made).
        """
        proj_res  = client.get("/api/projects")
        debug_res = client.get("/api/debug/token-usage")

        assert proj_res.status_code == 200
        assert debug_res.status_code == 200

        proj_today  = proj_res.json()["metrics"]["tokens_today"]
        debug_today = debug_res.json()["tokens_today"]

        # Allow small difference in case a row was written between the two calls
        assert abs(proj_today - debug_today) < 10_000, (
            f"Mismatch: /api/projects says {proj_today}, "
            f"/api/debug/token-usage says {debug_today}"
        )
