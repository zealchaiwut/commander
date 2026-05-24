"""Tests for issue #29 — fix tokens-today metric.

AC-1: /api/debug/token-usage returns row_count, latest_recorded_at, tokens_today.
AC-2: Endpoint shape is correct (integer row_count, ISO-8601 or null, integer).
AC-3: tokens_today in /api/projects metrics matches debug endpoint value.
AC-4: post_tool_used.py logs to stderr when discarding a payload with no tokens.
AC-5: _extract_usage() correctly parses the Claude Code PostToolUse payload.
"""
import io
import json
import subprocess
import sys
import types
import pytest

# ---------------------------------------------------------------------------
# AC-5: Unit-test _extract_usage() from the hook module
# ---------------------------------------------------------------------------

def _import_hook_module():
    """Import post_tool_used without executing main()."""
    import importlib.util
    from pathlib import Path

    hook_path = Path(__file__).parent.parent / "hooks" / "post_tool_used.py"
    spec = importlib.util.spec_from_file_location("post_tool_used", hook_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestExtractUsage:
    """AC-5: _extract_usage() correctly reads the PostToolUse payload."""

    @pytest.fixture(scope="class")
    def hook(self):
        return _import_hook_module()

    def test_reads_top_level_usage_key(self, hook):
        """Standard payload with top-level 'usage' key is parsed correctly."""
        payload = {
            "session_id": "abc123",
            "tool_name": "Bash",
            "cwd": "/tmp",
            "usage": {
                "input_tokens": 500,
                "output_tokens": 150,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
        inp, out = hook._extract_usage(payload)
        assert inp == 500
        assert out == 150

    def test_reads_tool_result_usage_fallback(self, hook):
        """Fallback to tool_result.usage when top-level usage is absent."""
        payload = {
            "session_id": "abc123",
            "tool_name": "Bash",
            "cwd": "/tmp",
            "tool_result": {
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            },
        }
        inp, out = hook._extract_usage(payload)
        assert inp == 200
        assert out == 80

    def test_returns_zeros_when_no_usage(self, hook):
        """Returns (0, 0) when neither usage path has token data."""
        payload = {"session_id": "abc123", "tool_name": "Bash", "cwd": "/tmp"}
        inp, out = hook._extract_usage(payload)
        assert inp == 0
        assert out == 0

    def test_top_level_usage_takes_precedence_over_tool_result(self, hook):
        """Top-level usage key wins when both paths have values."""
        payload = {
            "session_id": "abc123",
            "usage": {"input_tokens": 300, "output_tokens": 100},
            "tool_result": {"usage": {"input_tokens": 999, "output_tokens": 999}},
        }
        inp, out = hook._extract_usage(payload)
        assert inp == 300
        assert out == 100

    def test_none_values_treated_as_zero(self, hook):
        """None token values in usage dict are treated as 0."""
        payload = {
            "usage": {"input_tokens": None, "output_tokens": None}
        }
        inp, out = hook._extract_usage(payload)
        assert inp == 0
        assert out == 0

    def test_missing_usage_keys_default_to_zero(self, hook):
        """Missing input_tokens / output_tokens within usage default to 0."""
        payload = {"usage": {}}
        inp, out = hook._extract_usage(payload)
        assert inp == 0
        assert out == 0


# ---------------------------------------------------------------------------
# AC-4: stderr logging when payload is discarded
# ---------------------------------------------------------------------------

class TestHookStderrLogging:
    """AC-4: hook logs to stderr when it discards a no-token payload."""

    def test_empty_payload_logs_to_stderr(self):
        """Running the hook with an empty-token payload emits a stderr line."""
        import subprocess
        from pathlib import Path

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

    def test_valid_token_payload_does_not_log_skip(self):
        """Hook does NOT emit skip message when token data is present."""
        from pathlib import Path

        hook_path = str(Path(__file__).parent.parent / "hooks" / "post_tool_used.py")
        payload = json.dumps({
            "session_id": "test",
            "cwd": "/tmp",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload,
            capture_output=True,
            text=True,
        )
        # Hook may fail to POST (no running server), but must not log "skipped"
        assert "skipped" not in result.stderr


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
