"""Bearer-token auth gate for write endpoints (issue #1864).

Reads COMMANDER_API_TOKEN from the environment at call time.
When set, POST/PUT/PATCH/DELETE requests must carry:
  Authorization: Bearer <token>
GET, HEAD, OPTIONS, and SSE paths are always open.
Requests from 127.0.0.1 / ::1 (same-host hooks) are exempt.
When the env var is unset, all requests pass through unchanged.
"""
from __future__ import annotations

import os

from fastapi.responses import JSONResponse
from starlette.requests import Request


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def bearer_auth_gate(request: Request) -> JSONResponse | None:
    """Return a 401 JSONResponse if auth fails, or None to pass through."""
    token = os.environ.get("COMMANDER_API_TOKEN", "").strip()
    if not token:
        return None

    if request.method.upper() not in _WRITE_METHODS:
        return None

    client_host = request.client.host if request.client else ""
    if client_host in _LOCALHOST_HOSTS:
        return None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == token:
        return None

    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


def inject_auth_script(html: str) -> str:
    """Inject a fetch-patching <script> into HTML when COMMANDER_API_TOKEN is set.

    The injected script monkey-patches window.fetch so the browser
    automatically adds Authorization: Bearer <token> on all non-GET
    requests. This requires no changes to existing frontend JS.
    """
    token = os.environ.get("COMMANDER_API_TOKEN", "").strip()
    if not token:
        return html

    script = (
        "<script>(function(){"
        "var _t=" + repr(token) + ";"
        "var _f=window.fetch;"
        "window.fetch=function(u,o){"
        "o=o||{};"
        "var m=(o.method||'GET').toUpperCase();"
        "if(m!=='GET'&&m!=='HEAD'&&m!=='OPTIONS'){"
        "o.headers=Object.assign({},o.headers,{'Authorization':'Bearer '+_t});"
        "}"
        "return _f.call(this,u,o);"
        "};"
        "}());</script>"
    )
    return html.replace("</head>", script + "</head>", 1)
