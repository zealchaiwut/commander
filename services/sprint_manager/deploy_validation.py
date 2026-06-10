"""Deploy-config field validation (issue #769).

Pure, dependency-free validators for the inline working_dir / port edits made on
a local-host Deploy card. The dashboard's validate endpoint calls these before
persisting an edit, so a bad value is rejected with a user-visible error instead
of being written to the deploy config.

  - working_dir → must exist on disk AND be a git clone (``.git`` dir or file).
  - port        → must be an integer in 1–65535 AND not already in use on host.

Filesystem and socket checks live here (not in the pure schema module) so the
server stays thin and the checks are individually unit-testable.
"""
from __future__ import annotations

import os
import socket
from typing import Any


class DeployValidationError(ValueError):
    """A working_dir or port edit failed validation. Maps to an HTTP 400."""


# ── port ──────────────────────────────────────────────────────────────────────


def validate_port(value: Any) -> int:
    """Return *value* as an int in 1–65535, or raise :class:`DeployValidationError`.

    Booleans are rejected (``True``/``False`` are not meaningful ports) and so are
    floats / non-numeric strings — only a clean integer in range is accepted.
    """
    if isinstance(value, bool):
        raise DeployValidationError(
            f"Port must be an integer between 1 and 65535; got {value!r}"
        )
    try:
        if isinstance(value, str):
            port = int(value.strip())
        elif isinstance(value, int):
            port = value
        else:
            raise ValueError
    except (ValueError, TypeError):
        raise DeployValidationError(
            f"Port must be an integer between 1 and 65535; got {value!r}"
        )
    if port < 1 or port > 65535:
        raise DeployValidationError(
            f"Port must be between 1 and 65535; got {port}"
        )
    return port


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True when *port* is already bound by a listener on *host*.

    Attempts a plain bind (no SO_REUSEADDR) — a live listener makes bind fail with
    ``EADDRINUSE``. A successful bind means the port is free.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        return False
    except OSError:
        return True
    finally:
        sock.close()


# ── working_dir ───────────────────────────────────────────────────────────────


def is_git_clone(path: str) -> bool:
    """True when *path* contains a ``.git`` entry (a dir for clones, a file for
    linked worktrees)."""
    return os.path.exists(os.path.join(path, ".git"))


def validate_working_dir(path: Any) -> str:
    """Validate a working_dir edit. Returns the path on success.

    Raises :class:`DeployValidationError` when the path is empty, does not exist
    on disk, or exists but is not a git clone.
    """
    p = (path or "").strip() if isinstance(path, str) else ""
    if not p:
        raise DeployValidationError("Folder path is required.")
    if not os.path.isdir(p):
        raise DeployValidationError(f"Folder does not exist on disk: {p}")
    if not is_git_clone(p):
        raise DeployValidationError(f"Folder is not a git clone: {p}")
    return p
