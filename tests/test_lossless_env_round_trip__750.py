"""Tests for issue #750: Lossless round-trip for .env values containing ' #' (runs against UAT)"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "dashboard"))
from env_file import parse_env_text, write_env_vars, _needs_quoting  # noqa: E402


# --- Acceptance Criteria ---

def test_lossless_env_round_trip__ac1_read_value_with_space_hash():
    """AC1: Reading a `.env` value that contains ` #` returns the full string, not truncated."""
    # Given a .env snippet with SECRET=secret #1 (unquoted)
    env_text = "SECRET=secret #1\n"

    # When we parse it
    pairs = parse_env_text(env_text)

    # Then the value should be the full string "secret #1", not truncated to "secret"
    assert len(pairs) == 1
    assert pairs[0] == ("SECRET", "secret #1")


def test_lossless_env_round_trip__ac2_write_space_hash_with_quotes():
    """AC2: Saving a value containing ` #` writes it with double quotes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # When we write a value containing " #"
        write_env_vars(env_file, [("SECRET", "secret #1")])

        # Then the file should contain the quoted version
        content = env_file.read_text()
        assert 'SECRET="secret #1"' in content


def test_lossless_env_round_trip__ac3_write_whitespace_with_quotes():
    """AC3: Values containing whitespace but no ` #` are quoted on write."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # When we write a value with whitespace (no " #")
        write_env_vars(env_file, [("VALUE", "hello world")])

        # Then it should be quoted
        content = env_file.read_text()
        assert 'VALUE="hello world"' in content


def test_lossless_env_round_trip__ac4_write_clean_value_unquoted():
    """AC4: Values with no ` #` or whitespace are written unquoted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # When we write a clean value (no spaces, no " #")
        write_env_vars(env_file, [("PASSWORD", "mypassword")])

        # Then it should be unquoted
        content = env_file.read_text()
        assert content.strip() == "PASSWORD=mypassword"


def test_lossless_env_round_trip__ac5_full_round_trip_lossless():
    """AC5: A full read → modify → save → re-read round-trip is lossless."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # Given initial content with a value containing " #"
        original_pairs = [("SECRET", "secret #1"), ("OTHER", "value")]
        write_env_vars(env_file, original_pairs)

        # When we read, modify, and write back
        read_pairs = parse_env_text(env_file.read_text())
        assert read_pairs[0] == ("SECRET", "secret #1")  # ensure we read it correctly first

        # Modify and write back the same value
        write_env_vars(env_file, read_pairs)

        # Then re-reading should produce the same value
        final_pairs = parse_env_text(env_file.read_text())
        assert ("SECRET", "secret #1") in final_pairs


def test_lossless_env_round_trip__ac6_already_quoted_preserves_content():
    """AC6: Already-quoted values in .env preserve inner content after round-trip."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # Given a .env file with an already-quoted value containing " #"
        env_file.write_text('TOKEN="bearer abc #2"\n')

        # When we read it
        pairs = parse_env_text(env_file.read_text())
        assert pairs[0] == ("TOKEN", "bearer abc #2")

        # And write it back
        write_env_vars(env_file, pairs)

        # Then re-read should still have the full value
        final_pairs = parse_env_text(env_file.read_text())
        assert ("TOKEN", "bearer abc #2") in final_pairs


# --- Additional edge-case tests ---

def test_lossless_env_round_trip__needs_quoting_function():
    """Test the _needs_quoting helper function covers all cases."""
    # Should quote: space-hash
    assert _needs_quoting("secret #1") is True

    # Should quote: whitespace
    assert _needs_quoting("hello world") is True
    assert _needs_quoting("value ") is True
    assert _needs_quoting(" value") is True
    assert _needs_quoting("a\tb") is True

    # Should not quote: clean values
    assert _needs_quoting("mypassword") is False
    assert _needs_quoting("value123") is False
    assert _needs_quoting("PORT_5432") is False


def test_lossless_env_round_trip__mixed_quoted_and_unquoted():
    """Test file with mixed quoted and unquoted values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # Write mixed values
        pairs = [
            ("CLEAN", "password"),
            ("WITH_SPACE", "hello world"),
            ("WITH_HASH", "secret #1"),
            ("QUOTED", "already_quoted_value"),
        ]
        write_env_vars(env_file, pairs)

        # Read back
        content = env_file.read_text()
        assert "CLEAN=password" in content  # unquoted
        assert 'WITH_SPACE="hello world"' in content  # quoted
        assert 'WITH_HASH="secret #1"' in content  # quoted

        # Parse and verify
        parsed = parse_env_text(content)
        parsed_dict = dict(parsed)
        assert parsed_dict["CLEAN"] == "password"
        assert parsed_dict["WITH_SPACE"] == "hello world"
        assert parsed_dict["WITH_HASH"] == "secret #1"
        assert parsed_dict["QUOTED"] == "already_quoted_value"


def test_lossless_env_round_trip__clean_values_unchanged():
    """Test that clean values (no spaces, no #) written unquoted are preserved verbatim when unchanged."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # Write initial file with a clean value
        env_file.write_text("PORT=5432\nPASSWORD=secret\n")

        # Read and write back without changing
        pairs = parse_env_text(env_file.read_text())
        write_env_vars(env_file, pairs)

        # Clean values should remain unquoted
        content = env_file.read_text()
        assert "PORT=5432\n" in content
        assert "PASSWORD=secret\n" in content


def test_lossless_env_round_trip__preserves_comments_and_blank_lines():
    """Test that comment-only and blank lines are preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        env_file = Path(tmpdir) / ".env"

        # Create file with comments and blanks
        env_file.write_text("# This is a comment\nPORT=5432\n\n# Another comment\nHOST=localhost\n")

        # Read and write back
        pairs = parse_env_text(env_file.read_text())
        write_env_vars(env_file, pairs)

        content = env_file.read_text()
        # Comments and blanks should be preserved
        assert "# This is a comment" in content
        assert "# Another comment" in content
        # and variables too
        assert "PORT=5432" in content
        assert "HOST=localhost" in content
