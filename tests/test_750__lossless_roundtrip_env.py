"""Tests for issue #750 — Lossless round-trip for .env values containing ' #'.

Each test anchored to a specific acceptance criterion.

AC1 — read_space_hash_unquoted: Reading SECRET=secret #1 (unquoted)
      returns 'secret #1'.
AC2 — write_space_hash_quoted: Writing 'secret #1' produces
      SECRET="secret #1".
AC3 — write_whitespace_quoted: Writing 'hello world' produces
      VALUE="hello world".
AC4 — write_clean_unquoted: Writing 'mypassword' produces
      SECRET=mypassword (no quotes).
AC5 — roundtrip_lossless: full write → save → re-read round-trip
      of a value with ' #'.
AC6 — already_quoted_roundtrip: already-quoted value with ' #'
      survives a round-trip.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DASHBOARD_DIR = REPO_ROOT / "apps" / "dashboard"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASHBOARD_DIR))


def _ef():
    for mod in ("env_file",):
        sys.modules.pop(mod, None)
    import env_file
    return env_file


# AC1 — reading unquoted value with ' #' returns full string
def test_read_unquoted_space_hash_returns_full_value():
    """AC1: Reading SECRET=secret #1 (unquoted) returns 'secret #1',
    not 'secret'."""
    ef = _ef()
    result = ef.parse_env_text("SECRET=secret #1\n")
    assert result == [("SECRET", "secret #1")]


# AC2 — writing a value with ' #' produces a double-quoted line
def test_write_space_hash_value_is_quoted(tmp_path):
    """AC2: Saving a value containing ' #' writes it in double quotes."""
    ef = _ef()
    env = tmp_path / ".env"
    env.write_text("")
    ef.write_env_vars(env, [("SECRET", "secret #1")])
    line = env.read_text().strip()
    assert line == 'SECRET="secret #1"'


# AC3 — writing a value with whitespace (no ' #') produces a double-quoted line
def test_write_whitespace_value_is_quoted(tmp_path):
    """AC3: Saving a value with whitespace writes it in double quotes."""
    ef = _ef()
    env = tmp_path / ".env"
    env.write_text("")
    ef.write_env_vars(env, [("VALUE", "hello world")])
    line = env.read_text().strip()
    assert line == 'VALUE="hello world"'


# AC4 — clean value (no whitespace, no ' #') is written unquoted
def test_write_clean_value_is_unquoted(tmp_path):
    """AC4: Saving a value with no whitespace or ' #' writes it unquoted."""
    ef = _ef()
    env = tmp_path / ".env"
    env.write_text("")
    ef.write_env_vars(env, [("SECRET", "mypassword")])
    line = env.read_text().strip()
    assert line == "SECRET=mypassword"


# AC5 — full round-trip is lossless
def test_roundtrip_space_hash_lossless(tmp_path):
    """AC5: write 'secret #1' → save → re-read returns 'secret #1'
    (lossless)."""
    ef = _ef()
    env = tmp_path / ".env"
    env.write_text("")
    ef.write_env_vars(env, [("SECRET", "secret #1")])
    pairs = ef.parse_env_text(env.read_text())
    assert pairs == [("SECRET", "secret #1")]


# AC6 — already-quoted value round-trips correctly
def test_already_quoted_value_roundtrip(tmp_path):
    """AC6: Already-quoted value with ' #' preserves inner content
    after read → write → read."""
    ef = _ef()
    env = tmp_path / ".env"
    env.write_text('TOKEN="bearer abc #2"\n')
    # First read: outer quotes stripped, inner content returned.
    pairs = ef.parse_env_text(env.read_text())
    assert pairs == [("TOKEN", "bearer abc #2")]
    # Write back the same value.
    ef.write_env_vars(env, [("TOKEN", "bearer abc #2")])
    # Re-read: value still intact.
    pairs2 = ef.parse_env_text(env.read_text())
    assert pairs2 == [("TOKEN", "bearer abc #2")]


# Additional: read_env_vars public API returns full value for unquoted ' #'
def test_read_env_vars_returns_full_space_hash_value(tmp_path):
    """AC1 (via public API): read_env_vars returns the full value
    for unquoted ' #' lines."""
    ef = _ef()
    env = tmp_path / ".env"
    env.write_text("SECRET=secret #1\n")
    got = ef.read_env_vars(env)
    assert got == [{"key": "SECRET", "value": "secret #1"}]
