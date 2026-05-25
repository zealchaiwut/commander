#!/usr/bin/env python3
"""run_server.py — Start the Commander dashboard server.

Usage:
    cd apps/dashboard
    python run_server.py          # listens on port 8000 (or PORT env var)

This is a convenience launcher so the server can be started from apps/dashboard/
without remembering the uvicorn invocation.
"""
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    here = Path(__file__).parent
    venv_uvicorn = here / "venv" / "bin" / "uvicorn"
    port = os.environ.get("PORT", "8000")

    if venv_uvicorn.exists():
        uvicorn_cmd = str(venv_uvicorn)
    else:
        uvicorn_cmd = "uvicorn"

    cmd = [uvicorn_cmd, "server:app", "--host", "0.0.0.0", "--port", port]
    print(f"Starting Commander dashboard on port {port} ...")
    os.chdir(here)
    os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
