"""Tests for issue #750: Lossless round-trip for .env values containing ' #'"""
import tempfile
from pathlib import Path


from env_file import parse_env_text, write_env_vars


class TestReadingValuesWithSpaceHash:
    """AC1: Reading a `.env` value that contains ` #` returns the full string, not truncated."""

    def test_unquoted_value_with_space_hash_returns_full_string(self):
        """Read SECRET=secret #1 and get the full 'secret #1', not truncated 'secret'."""
        text = "SECRET=secret #1"
        pairs = parse_env_text(text)
        assert pairs == [("SECRET", "secret #1")]

    def test_multiple_space_hashes_in_unquoted_value(self):
        """Multiple space-hash sequences are preserved."""
        text = "VALUE=part1 # part2 # part3"
        pairs = parse_env_text(text)
        assert pairs == [("VALUE", "part1 # part2 # part3")]

    def test_space_hash_at_end_of_value(self):
        """Space-hash at the very end is preserved."""
        text = "CODE=abc123 #"
        pairs = parse_env_text(text)
        assert pairs == [("CODE", "abc123 #")]


class TestSavingValuesWithSpaceHash:
    """AC2: Saving a `.env` value that contains ` #` writes it with surrounding double quotes."""

    def test_save_value_with_space_hash_adds_quotes(self):
        """Write SECRET=secret #1 and expect KEY="secret #1" in file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            pairs = [("SECRET", "secret #1")]
            write_env_vars(path, pairs)
            content = path.read_text()
            assert 'SECRET="secret #1"' in content

    def test_saved_quoted_value_is_readable_back(self):
        """Write a space-hash value, then re-read it; should get the same value."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            original_pairs = [("SECRET", "secret #1")]
            write_env_vars(path, original_pairs)
            read_back = parse_env_text(path.read_text())
            assert read_back == original_pairs


class TestSavingValuesWithWhitespace:
    """AC3: Values containing whitespace but no ` #` are also quoted on write."""

    def test_value_with_space_gets_quoted(self):
        """Write VALUE=hello world and expect KEY="hello world" in file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            pairs = [("VALUE", "hello world")]
            write_env_vars(path, pairs)
            content = path.read_text()
            assert 'VALUE="hello world"' in content

    def test_value_with_tab_gets_quoted(self):
        """Values with tab character are quoted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            pairs = [("TAB_VAL", "hello\tworld")]
            write_env_vars(path, pairs)
            content = path.read_text()
            assert 'TAB_VAL="hello\tworld"' in content

    def test_value_with_newline_spaces_gets_quoted(self):
        """Multiline values with spaces are quoted (spaces trigger quoting)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            pairs = [("MULTILINE", "line one")]
            write_env_vars(path, pairs)
            content = path.read_text()
            assert 'MULTILINE="line one"' in content


class TestUnquotedCleanValues:
    """AC4: Values that do not contain ` #` or whitespace are written unquoted."""

    def test_clean_value_no_quotes(self):
        """Write SECRET=mypassword and expect KEY=mypassword (no quotes) in file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            pairs = [("SECRET", "mypassword")]
            write_env_vars(path, pairs)
            content = path.read_text()
            assert "SECRET=mypassword" in content
            assert 'SECRET="mypassword"' not in content

    def test_alphanumeric_with_special_chars_no_quotes(self):
        """Values like secret_123-abc are unquoted if no spaces or space-hash."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            pairs = [("TOKEN", "abc123_-defg")]
            write_env_vars(path, pairs)
            content = path.read_text()
            assert "TOKEN=abc123_-defg" in content
            assert 'TOKEN="' not in content


class TestRoundTripLossless:
    """AC5: A full read → modify → save → re-read round-trip produces original value without loss."""

    def test_roundtrip_space_hash_value(self):
        """Read SECRET=secret #1, modify, save, re-read; both should be unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            original = [("SECRET", "secret #1"), ("OTHER", "value")]
            write_env_vars(path, original)
            read_back_1 = parse_env_text(path.read_text())
            assert read_back_1 == original
            modified = [("SECRET", "new#secret"), ("OTHER", "value")]
            write_env_vars(path, modified)
            read_back_2 = parse_env_text(path.read_text())
            assert read_back_2 == modified

    def test_roundtrip_mixed_values(self):
        """Mix of clean, whitespace, and space-hash values round-trips correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            original = [
                ("CLEAN", "password123"),
                ("WITH_SPACE", "hello world"),
                ("WITH_HASH", "secret #1"),
            ]
            write_env_vars(path, original)
            read_back = parse_env_text(path.read_text())
            assert read_back == original


class TestAlreadyQuotedValues:
    """AC6: Already-quoted values in the `.env` file are parsed correctly and preserve inner content."""

    def test_read_already_quoted_value(self):
        """Reading TOKEN="bearer abc #2" should yield 'bearer abc #2' without quotes."""
        text = 'TOKEN="bearer abc #2"'
        pairs = parse_env_text(text)
        assert pairs == [("TOKEN", "bearer abc #2")]

    def test_roundtrip_already_quoted_value(self):
        """Write a value that was originally quoted, ensure round-trip is lossless."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text('TOKEN="bearer abc #2"\n')
            read_1 = parse_env_text(path.read_text())
            assert read_1 == [("TOKEN", "bearer abc #2")]
            write_env_vars(path, [("TOKEN", "bearer abc #2")])
            read_2 = parse_env_text(path.read_text())
            assert read_2 == [("TOKEN", "bearer abc #2")]

    def test_single_quoted_values(self):
        """Single-quoted values are also parsed, inner content extracted."""
        text = "SECRET='password #1'"
        pairs = parse_env_text(text)
        assert pairs == [("SECRET", "password #1")]


class TestPreservingComments:
    """Edge case: Unchanged lines with inline comments should preserve the comments."""

    def test_unchanged_value_with_inline_comment_preserved(self):
        """If a value in the file has an inline comment and value is unchanged, comment stays."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text("PORT=5432  # database port\n")
            pairs = parse_env_text(path.read_text())
            write_env_vars(path, pairs)
            content = path.read_text()
            assert "# database port" in content

    def test_changed_value_inline_comment_replaced_with_quotes(self):
        """If value changes from unquoted+comment to new value, quote if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".env"
            path.write_text("PORT=5432  # old port\n")
            write_env_vars(path, [("PORT", "port #1")])
            content = path.read_text()
            assert 'PORT="port #1"' in content
