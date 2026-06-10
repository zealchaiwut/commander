"""Read and write `.env` files for the env-var editor (issue #727).

Parsing rules:
- A variable line matches ``KEY=VALUE`` (an optional ``export `` prefix is
  allowed). KEY must start with a letter or underscore.
- The returned value is the text after the first ``=``, with a trailing inline
  comment (`` # ...``) stripped and surrounding whitespace removed.
- Comment-only lines and blank lines are not returned by the parser but are
  preserved verbatim on write.

Write rules (issue #727 AC):
- Original line order and inline comments are preserved where a key's value is
  unchanged (the line is kept verbatim).
- A rewritten key appears at its original position.
- A new key is appended at the end of the file.
- A key absent from the submitted set is removed.
"""

from __future__ import annotations

import re
from pathlib import Path

# KEY=VALUE, optional leading "export ", key is a shell-style identifier.
_VAR_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _strip_inline_comment(raw_value: str) -> str:
    """Return the value with a trailing `` # comment`` removed.

    Only an unquoted inline comment (whitespace then ``#``) is stripped. A value
    wrapped in matching quotes is returned with its quotes intact so round-trips
    are lossless for quoted secrets.
    """
    value = raw_value.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[: end + 1]
        return value
    # Unquoted: a comment starts at the first " #" sequence.
    m = re.search(r"\s+#", value)
    if m:
        value = value[: m.start()]
    return value.strip()


def parse_env_text(text: str) -> list[tuple[str, str]]:
    """Parse `.env` text into ``[(key, value), ...]`` in file order."""
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _VAR_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        value = _strip_inline_comment(m.group(2))
        pairs.append((key, value))
    return pairs


def read_env_vars(path: Path) -> list[dict]:
    """Read a `.env` file and return ``[{"key": k, "value": v}, ...]``.

    Returns ``[]`` when the file does not exist. Values are plaintext — masking
    is a display-only concern handled client-side.
    """
    p = Path(path)
    if not p.exists():
        return []
    pairs = parse_env_text(p.read_text())
    return [{"key": k, "value": v} for k, v in pairs]


def write_env_vars(path: Path, pairs: list[tuple[str, str]]) -> None:
    """Write ``pairs`` (``[(key, value), ...]``) back to ``path``.

    Preserves original line order and inline comments for keys whose value is
    unchanged; rewrites changed keys in place; drops keys not in ``pairs``;
    appends new keys at the end. Creates the file if it does not exist.
    """
    p = Path(path)
    new_map: dict[str, str] = {}
    order: list[str] = []
    for key, value in pairs:
        if key not in new_map:
            order.append(key)
        new_map[key] = value

    original = p.read_text() if p.exists() else ""
    out: list[str] = []
    seen: set[str] = set()

    for line in original.splitlines():
        m = _VAR_RE.match(line)
        if not m:
            out.append(line)  # comment-only or blank line — keep verbatim
            continue
        key = m.group(1)
        if key not in new_map:
            continue  # deleted
        if key in seen:
            continue  # duplicate original key — keep only first occurrence
        seen.add(key)
        current = _strip_inline_comment(m.group(2))
        if current == new_map[key]:
            out.append(line)  # unchanged — verbatim, preserves inline comment
        else:
            out.append(f"{key}={new_map[key]}")  # rewritten in place

    for key in order:
        if key not in seen:
            out.append(f"{key}={new_map[key]}")  # new key appended at end

    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    p.write_text(text)
