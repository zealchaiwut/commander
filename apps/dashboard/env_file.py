"""Read and write `.env` files for the env-var editor (issue #727).

Parsing rules:
- A variable line matches ``KEY=VALUE`` (an optional ``export `` prefix is
  allowed). KEY must start with a letter or underscore.
- For quoted values the surrounding quotes are stripped and the inner content
  is returned (e.g. ``KEY="value #1"`` → ``value #1``).
- For unquoted values the raw trimmed value is returned as-is; inline
  comments are intentionally NOT stripped so that legitimate values such as
  ``SECRET=secret #1`` round-trip losslessly (issue #750).
- Comment-only lines and blank lines are not returned by the parser but are
  preserved verbatim on write.

Write rules (issue #727 AC):
- Original line order and inline comments are preserved where a key's value is
  unchanged (the line is kept verbatim).
- A rewritten key appears at its original position.
- A new key is appended at the end of the file.
- A key absent from the submitted set is removed.

Quoting rules (issue #750 AC):
- Values that contain whitespace or a space-hash sequence (`` #``) are
  written wrapped in double quotes so the parser does not misread them as
  having an inline comment on the next read.
- Values with no whitespace and no `` #`` are written unquoted (no change in
  behaviour for clean values).
"""

from __future__ import annotations

import re
from pathlib import Path

# KEY=VALUE, optional leading "export ", key is a shell-style identifier.
_VAR_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _parse_value(raw_value: str) -> str:
    """Return the display value for a raw .env value string
    (everything after the ``=`` delimiter).

    For quoted values the surrounding quotes are stripped and the inner content
    is returned (e.g. ``"a b"`` → ``a b``).
    For unquoted values the trimmed raw string is returned verbatim; inline
    comments are NOT stripped so the full value is preserved (issue #750 AC1).
    """
    value = raw_value.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value
    return value


def _comparison_value(raw_value: str) -> str:
    """Return the value used for change-detection in the write path.

    For quoted values: same as ``_parse_value`` (inner content, no quotes).
    For unquoted values: trailing inline comment (``\\s+#``) is stripped so
    that a file line such as ``PORT=5432  # note`` is treated as unchanged
    when the editor submits ``5432``, preserving the comment verbatim.
    """
    value = raw_value.strip()
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value
    m = re.search(r"\s+#", value)
    if m:
        return value[: m.start()].strip()
    return value


def _needs_quoting(value: str) -> bool:
    """Return ``True`` if *value* must be wrapped in double quotes on write.

    Values containing whitespace or a space-hash (`` #``) sequence are quoted
    so the parser reads them back in full without truncation
    (issue #750 AC2-4).
    """
    return bool(re.search(r"\s", value)) or " #" in value


def parse_env_text(text: str) -> list[tuple[str, str]]:
    """Parse `.env` text into ``[(key, value), ...]`` in file order."""
    pairs: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = _VAR_RE.match(line)
        if not m:
            continue
        key = m.group(1)
        value = _parse_value(m.group(2))
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
        current = _comparison_value(m.group(2))
        if current == new_map[key]:
            out.append(line)  # unchanged — verbatim, preserves inline comment
        else:
            v = new_map[key]
            out.append(f'{key}="{v}"' if _needs_quoting(v) else f"{key}={v}")

    for key in order:
        if key not in seen:
            v = new_map[key]
            out.append(f'{key}="{v}"' if _needs_quoting(v) else f"{key}={v}")

    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    p.write_text(text)
