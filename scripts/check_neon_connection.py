#!/usr/bin/env python3
"""Pre-flight check for Neon (PostgreSQL) database connection.

Verifies DATABASE_URL (or DIRECT_URL with --direct) is configured, reachable,
and accepts basic queries. Exits 0 on success, non-zero on any failure.
Never prints credentials — only host and database name are shown.

Usage:
    python scripts/check_neon_connection.py
    python scripts/check_neon_connection.py --direct
"""
import argparse
import os
import signal
import sys
import time
from pathlib import Path
from urllib.parse import urlparse


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass


def _parse_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.hostname or "", (parsed.path or "").lstrip("/")


def _timeout_handler(signum, frame):
    raise TimeoutError("Connection timed out after 10 seconds")


def main() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(description="Check Neon database connection")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Test DIRECT_URL instead of DATABASE_URL (requires DIRECT_URL in .env)",
    )
    args = parser.parse_args()

    url_type = "DIRECT_URL" if args.direct else "DATABASE_URL"
    url = os.environ.get(url_type)

    if not url:
        print(f"ERROR: {url_type} not configured — set it in .env or the environment")
        sys.exit(1)

    host, dbname = _parse_url(url)
    print(f"Testing: {url_type}")
    print(f"  host:     {host}")
    print(f"  database: {dbname}")

    try:
        import psycopg2
    except ImportError:
        print("\nERROR: psycopg2 is not installed — run: pip install psycopg2-binary")
        sys.exit(1)

    # Hard cap slightly above connect_timeout so psycopg2 fires first for normal cases.
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(12)

    try:
        start = time.monotonic()
        conn = psycopg2.connect(url, connect_timeout=10)
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.execute("SELECT version()")
            row = cur.fetchone()
        conn.close()
        signal.alarm(0)
        elapsed_ms = round((time.monotonic() - start) * 1000)

        print(f"\nSUCCESS")
        print(f"  version:     {row[0]}")
        print(f"  round-trip:  {elapsed_ms}ms")
        sys.exit(0)

    except TimeoutError as e:
        signal.alarm(0)
        print(f"\nFAILURE: {e}")
        sys.exit(1)
    except psycopg2.OperationalError as e:
        signal.alarm(0)
        err_str = str(e).strip()
        print(f"\nFAILURE: OperationalError: {err_str}")
        low = err_str.lower()
        if "password" in low or "authentication" in low:
            print(f"Hint: Check your {url_type} password")
        elif "timeout" in low or "timed out" in low:
            print("Hint: Server unreachable — check host/port and firewall rules")
        sys.exit(1)
    except Exception as e:
        signal.alarm(0)
        print(f"\nFAILURE: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
