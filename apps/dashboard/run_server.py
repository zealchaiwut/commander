#!/usr/bin/env python3
"""run_server.py — Start the Commander dashboard server.

Usage:
    cd apps/dashboard
    python run_server.py          # listens on port 8000 (or PORT env var)

This is a convenience launcher so the server can be started from apps/dashboard/
without remembering the uvicorn invocation.
"""
import os
import sys
from pathlib import Path


def main() -> None:
    here = Path(__file__).parent

    # Install size-rotating logging for prd.log before launching (issue #762).
    repo_root = here.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        from services.logging import setup_logging
        setup_logging()
    except Exception:
        pass

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
