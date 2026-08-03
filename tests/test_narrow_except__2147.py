"""Tests for #2147: Narrow broad except (…, Exception) in _gate_failure_scope_contaminated.

AC items verified:
  AC-1  OSError (e.g. missing file) is caught → function returns False
  AC-2  json.JSONDecodeError (invalid JSON) is caught → function returns False
  AC-3  ValueError is caught → function returns False
  AC-4  Unexpected exceptions (e.g. AttributeError) propagate — are NOT swallowed
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "services" / "sprint_manager"))

from sprint_manager import _gate_failure_scope_contaminated  # noqa: E402


# ---------------------------------------------------------------------------
# AC-1: OSError is caught → returns False
# ---------------------------------------------------------------------------

class TestAC1OSErrorCaught:
    def test_missing_sidecar_file_returns_false(self, tmp_path):
        """OSError from a missing sidecar file is caught and returns False."""
        # No sidecar written → read_text raises FileNotFoundError (subclass of OSError)
        result = _gate_failure_scope_contaminated(2147, repo_root=tmp_path)
        assert result is False, (
            "Missing sidecar (OSError) must return False, not propagate"
        )

    def test_permission_error_returns_false(self, tmp_path):
        """PermissionError (subclass of OSError) is caught and returns False."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text('{"detail": "tests/test_foo__9999.py"}', encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=PermissionError("no access")):
            result = _gate_failure_scope_contaminated(2147, repo_root=tmp_path)
        assert result is False, "PermissionError must be caught and return False"


# ---------------------------------------------------------------------------
# AC-2: json.JSONDecodeError is caught → returns False
# ---------------------------------------------------------------------------

class TestAC2JSONDecodeErrorCaught:
    def test_invalid_json_returns_false(self, tmp_path):
        """json.JSONDecodeError from malformed sidecar is caught and returns False."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text("not valid json }{", encoding="utf-8")

        result = _gate_failure_scope_contaminated(2147, repo_root=tmp_path)
        assert result is False, "Invalid JSON sidecar must return False, not propagate"

    def test_empty_file_returns_false(self, tmp_path):
        """Empty sidecar file (JSONDecodeError on loads) returns False."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text("", encoding="utf-8")

        result = _gate_failure_scope_contaminated(2147, repo_root=tmp_path)
        assert result is False, "Empty sidecar must return False, not propagate"


# ---------------------------------------------------------------------------
# AC-3: ValueError is caught → returns False
# ---------------------------------------------------------------------------

class TestAC3ValueErrorCaught:
    def test_value_error_returns_false(self, tmp_path):
        """ValueError raised during sidecar read/parse is caught and returns False."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text('{"detail": "x"}', encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=ValueError("bad value")):
            result = _gate_failure_scope_contaminated(2147, repo_root=tmp_path)
        assert result is False, "ValueError must be caught and return False"


# ---------------------------------------------------------------------------
# AC-4: Unexpected exceptions propagate — are NOT swallowed
# ---------------------------------------------------------------------------

class TestAC4UnexpectedExceptionsPropagate:
    def test_attribute_error_propagates(self, tmp_path):
        """AttributeError (not in the narrowed except) must propagate, not be swallowed."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text('{"detail": "x"}', encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=AttributeError("unexpected")):
            with pytest.raises(AttributeError):
                _gate_failure_scope_contaminated(2147, repo_root=tmp_path)

    def test_type_error_propagates(self, tmp_path):
        """TypeError (not in the narrowed except) must propagate, not be swallowed."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text('{"detail": "x"}', encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=TypeError("unexpected")):
            with pytest.raises(TypeError):
                _gate_failure_scope_contaminated(2147, repo_root=tmp_path)

    def test_runtime_error_propagates(self, tmp_path):
        """RuntimeError (not in the narrowed except) must propagate, not be swallowed."""
        runtime_dir = tmp_path / ".commander" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        sc = runtime_dir / "last-failure-2147.json"
        sc.write_text('{"detail": "x"}', encoding="utf-8")

        with patch.object(Path, "read_text", side_effect=RuntimeError("unexpected")):
            with pytest.raises(RuntimeError):
                _gate_failure_scope_contaminated(2147, repo_root=tmp_path)
