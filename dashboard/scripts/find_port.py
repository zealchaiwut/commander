#!/usr/bin/env python3
"""Find a free port for a project app server.

Usage:
    scripts/find_port.py --prefer 8002
    scripts/find_port.py --prefer 8002 --strategy always_random

Output: prints chosen port number to stdout (e.g. "8002" or "54312")
Exit 0 on success.

Logic:
    1. If --strategy always_random: bind to OS-assigned port and print it.
    2. Otherwise (prefer_default): try binding to --prefer port.
       If free, print it and exit 0.
       If occupied, fall back to OS-assigned free port.
"""
import argparse
import socket
import sys


def _is_port_free(port: int) -> bool:
    """Return True if the port can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False


def _get_free_port() -> int:
    """Ask the OS for a free ephemeral port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def find_port(prefer: int, strategy: str = "prefer_default") -> int:
    """Return the port to use based on strategy.

    Args:
        prefer:   The preferred port number.
        strategy: "prefer_default" or "always_random".

    Returns:
        The chosen port number.
    """
    if strategy == "always_random":
        return _get_free_port()

    # prefer_default: try the preferred port first
    if _is_port_free(prefer):
        return prefer

    # Preferred port is occupied; fall back to OS-assigned free port
    return _get_free_port()


def main() -> None:
    p = argparse.ArgumentParser(
        description="Find a free port for a project app server.",
    )
    p.add_argument(
        "--prefer",
        type=int,
        required=True,
        help="Preferred port number to try first",
    )
    p.add_argument(
        "--strategy",
        default="prefer_default",
        choices=["prefer_default", "always_random"],
        help="Port selection strategy (default: prefer_default)",
    )
    args = p.parse_args()

    chosen = find_port(prefer=args.prefer, strategy=args.strategy)
    print(chosen)


if __name__ == "__main__":
    main()
