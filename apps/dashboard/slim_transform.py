#!/usr/bin/env python3
"""slim_transform.py — Create startup.py by removing app-factory code from server.py.

Removes from server.py (to produce startup.py):
  1. All @app.get/post/put/delete/patch/websocket/middleware decorated functions
  2. The @asynccontextmanager lifespan function
  3. The app factory block: app = FastAPI(...) through include_router calls

Prepends a _server() helper so startup.py functions can access server state.

Usage (from coder/):
    python3 apps/dashboard/slim_transform.py
"""
import ast
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent
source = (DASHBOARD / "server.py").read_text(encoding="utf-8")
lines_list = source.splitlines(keepends=False)
tree = ast.parse(source)

exclude: set[int] = set()

# ── Find route handlers, middleware, lifespan ─────────────────────────────────
for node in tree.body:  # top-level only
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    if not node.decorator_list:
        continue

    for dec in node.decorator_list:
        remove = False

        # @app.get, @app.post, @app.middleware, @app.websocket, etc.
        if isinstance(dec, ast.Call):
            func = dec.func
            if (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "app"):
                remove = True

        # @asynccontextmanager lifespan function
        if (isinstance(dec, ast.Name) and dec.id == "asynccontextmanager"
                and node.name == "lifespan"):
            remove = True

        if remove:
            start = node.decorator_list[0].lineno
            end = node.end_lineno
            for ln in range(start, end + 1):
                exclude.add(ln)
            break

# ── Find the app factory block (app = FastAPI ... logger = getLogger) ─────────
factory_start: int | None = None
factory_end: int | None = None
for i, line in enumerate(lines_list, 1):
    stripped = line.strip()
    if stripped.startswith("app = FastAPI(") and factory_start is None:
        factory_start = i
    if factory_start is not None and "logger = logging.getLogger" in line:
        factory_end = i - 1
        break

if factory_start and factory_end:
    print(f"App factory block: lines {factory_start}–{factory_end}")
    for ln in range(factory_start, factory_end + 1):
        exclude.add(ln)

# ── Build startup.py ──────────────────────────────────────────────────────────
HEADER = """\
# startup.py — helper functions extracted from server.py (issue #1267).
#
# server.py imports this module and injects all its names into the server
# namespace so router files can still do:  srv = _server(); srv.<name>
#
# Circular-import safety: helpers call _server() only inside function bodies,
# never at module level.

def _server():
    \"\"\"Deferred import of the server module — safe at call time.\"\"\"
    import server  # noqa: PLC0415
    return server


# Re-import service objects removed with the app factory block so that
# helper functions (e.g. _cache_refresh_loop) can still call broadcast().
try:
    from routers.logs_service import broadcast, _subscribers  # noqa: E402
    from routers.milestones_service import resolve_bulk_milestone as _resolve_bulk_milestone  # noqa: E402
    from routers.bulk_tickets import _get_bulk_job  # noqa: E402
except Exception:
    pass  # available after server.py finishes importing its routers

"""

content: list[str] = [HEADER]
for i, line in enumerate(lines_list, 1):
    if i not in exclude:
        content.append(line + "\n")

(DASHBOARD / "startup.py").write_text("".join(content), encoding="utf-8")

total_excluded = len(exclude)
total_kept = len(lines_list) - total_excluded
print(f"startup.py written: {total_kept + HEADER.count(chr(10))} lines")
print(f"  removed: {total_excluded} lines ({len([n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))])} top-level functions found)")
