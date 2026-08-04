"""Tests for issue #1967: avoid duplicate token-usage query in _compute_cost.

Verifies that `_compute_cost` calls `_query_token_usage(conn)` exactly once
per invocation, regardless of which branch executes.
"""
import pytest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

_TESTER_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _TESTER_ROOT / "scripts"

import sys
sys.path.insert(0, str(_SCRIPTS_DIR))

import export_hermes_report
from export_hermes_report import _compute_cost, _query_token_usage


@pytest.fixture
def temp_db():
    """Create a temporary test database with token_usage table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY,
            model_name TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER
        )
    """)

    yield db_path, conn

    conn.close()
    Path(db_path).unlink(missing_ok=True)


def test_compute_cost_single_query_with_price_map_match(temp_db):
    """AC1: _query_token_usage is called exactly once when price_map has matching model."""
    db_path, conn = temp_db

    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-sonnet-4-6", 1000, 2000),
    )
    conn.commit()

    price_map = {"claude-sonnet-4-6": {"in": 3, "out": 15}}

    with patch(
        "export_hermes_report._query_token_usage",
        wraps=export_hermes_report._query_token_usage,
    ) as mock_query:
        cost_str, cost_source = _compute_cost(conn, price_map)
        assert mock_query.call_count == 1, (
            f"_query_token_usage called {mock_query.call_count} times; expected 1 "
            "(price-map-match path)"
        )

    assert cost_source == "price_map"
    assert cost_str == "$0.03"


def test_compute_cost_single_query_with_price_map_no_match(temp_db):
    """AC1: _query_token_usage is called exactly once when price_map has no matching model."""
    db_path, conn = temp_db

    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-sonnet-4-6", 1000, 2000),
    )
    conn.commit()

    price_map = {"gpt-4": {"in": 30, "out": 60}}

    with patch(
        "export_hermes_report._query_token_usage",
        wraps=export_hermes_report._query_token_usage,
    ) as mock_query:
        cost_str, cost_source = _compute_cost(conn, price_map)
        assert mock_query.call_count == 1, (
            f"_query_token_usage called {mock_query.call_count} times; expected 1 "
            "(price-map-no-match path)"
        )

    assert cost_source == "token_count"
    assert cost_str == "3000 tokens"


def test_compute_cost_single_query_without_price_map(temp_db):
    """AC1: _query_token_usage is called exactly once when price_map is absent."""
    db_path, conn = temp_db

    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-opus-4-1", 5000, 3000),
    )
    conn.commit()

    with patch(
        "export_hermes_report._query_token_usage",
        wraps=export_hermes_report._query_token_usage,
    ) as mock_query:
        cost_str, cost_source = _compute_cost(conn, None)
        assert mock_query.call_count == 1, (
            f"_query_token_usage called {mock_query.call_count} times; expected 1 "
            "(no-price-map path)"
        )

    assert cost_source == "token_count"
    assert cost_str == "8000 tokens"


def test_compute_cost_multiple_models(temp_db):
    """Single query correctly aggregates multiple models."""
    db_path, conn = temp_db

    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-opus-4-1", 1000, 2000),
    )
    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-sonnet-4-6", 3000, 4000),
    )
    conn.commit()

    price_map = {
        "claude-opus-4-1": {"in": 15, "out": 75},
        "claude-sonnet-4-6": {"in": 3, "out": 15},
    }

    with patch(
        "export_hermes_report._query_token_usage",
        wraps=export_hermes_report._query_token_usage,
    ) as mock_query:
        cost_str, cost_source = _compute_cost(conn, price_map)
        assert mock_query.call_count == 1

    assert cost_source == "price_map"
    # opus: 1000*(15/1M) + 2000*(75/1M) = 0.015 + 0.15 = 0.165
    # sonnet: 3000*(3/1M) + 4000*(15/1M) = 0.009 + 0.06 = 0.069
    # total = 0.234
    assert cost_str == "$0.23"


def test_compute_cost_zero_tokens(temp_db):
    """Zero-token case returns 'unknown'."""
    db_path, conn = temp_db

    cost_str, cost_source = _compute_cost(conn, {"gpt-4": {"in": 30, "out": 60}})

    assert cost_source == "unknown"
    assert cost_str == "unknown"


def test_compute_cost_maintains_output_consistency(temp_db):
    """Cost output is identical before and after refactor for all paths."""
    db_path, conn = temp_db

    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-sonnet-4-6", 10000, 20000),
    )
    conn.commit()

    price_map = {"claude-sonnet-4-6": {"in": 3, "out": 15}}
    cost_str_1, source_1 = _compute_cost(conn, price_map)
    cost_str_2, source_2 = _compute_cost(conn, None)

    assert source_1 == "price_map"
    assert source_2 == "token_count"
    assert "30000" in cost_str_2


def test_compute_cost_model_alias_matching(temp_db):
    """Price_map model alias lookup works correctly."""
    db_path, conn = temp_db

    conn.execute(
        "INSERT INTO token_usage (model_name, input_tokens, output_tokens) "
        "VALUES (?, ?, ?)",
        ("claude-sonnet-4-6-20250721", 1000, 2000),
    )
    conn.commit()

    price_map = {"claude-sonnet-4-6": {"in": 3, "out": 15}}
    cost_str, cost_source = _compute_cost(conn, price_map)

    assert cost_source == "price_map"
    assert cost_str == "$0.03"
