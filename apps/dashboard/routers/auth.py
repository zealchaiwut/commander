"""Bearer-token auth gate for write endpoints (issue #1864).

Reads COMMANDER_API_TOKEN from the environment at call time.
When set, POST/PUT/PATCH/DELETE requests must carry:
  Authorization: Bearer <token>
GET, HEAD, OPTIONS, and SSE paths are always open.
Requests from 127.0.0.1 / ::1 (same-host hooks) are exempt.
When the env var is unset, all requests pass through unchanged.

Token delivery to the browser (issue #1895): the token is NEVER emitted into
any served HTML or response body. An unauthenticated GET can always fetch the
dashboard pages, so inlining the secret there (the original #1864 approach)
leaked it to anyone who could load a page. Instead the injected script reads the
token from ``localStorage`` — the operator stores it once in their own browser
(``commanderSetApiToken('<token>')`` from the console, or the Settings field) —
and attaches it to non-GET fetches. No server-rendered secret, no sessions or
accounts (stays within the sanctioned single-static-token model).
"""
from __future__ import annotations

import hmac
import os

from fastapi.responses import JSONResponse
from starlette.requests import Request


_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# request.client.host is always a resolved IP, never a hostname, so "localhost"
# could never match here — 127.0.0.1 / ::1 are the real same-host signals
# (issue #1893).
_LOCALHOST_HOSTS = frozenset({"127.0.0.1", "::1"})

# localStorage key the browser reads the token from (see inject_auth_script).
_TOKEN_STORAGE_KEY = "commander_api_token"


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
    if auth_header.startswith("Bearer "):
        presented = auth_header[7:]
        # Constant-time compare so the equality check cannot be used as a
        # byte-by-byte timing oracle to recover the token (issue #1892).
        if hmac.compare_digest(presented, token):
            return None

    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


def inject_auth_script(html: str) -> str:
    """Inject a fetch-patching <script> into served HTML (issue #1895).

    The script carries NO secret. It reads the bearer token from
    ``localStorage`` at request time and adds ``Authorization: Bearer <token>``
    to every non-GET fetch. It also exposes ``window.commanderSetApiToken`` /
    ``window.commanderClearApiToken`` so the operator can store the token in
    their own browser once. When ``COMMANDER_API_TOKEN`` is unset the whole
    scheme is a no-op, but the (secretless) script is still safe to inject, so
    it is injected unconditionally — the served HTML never depends on the token.
    """
    key = _TOKEN_STORAGE_KEY
    script = (
        "<script>(function(){"
        "var K=" + repr(key) + ";"
        "window.commanderSetApiToken=function(t){"
        "try{localStorage.setItem(K,t);}catch(e){}"
        "};"
        "window.commanderClearApiToken=function(){"
        "try{localStorage.removeItem(K);}catch(e){}"
        "};"
        "var _f=window.fetch;"
        "window.fetch=function(u,o){"
        "o=o||{};"
        "var m=(o.method||'GET').toUpperCase();"
        "if(m!=='GET'&&m!=='HEAD'&&m!=='OPTIONS'){"
        "var t=null;try{t=localStorage.getItem(K);}catch(e){}"
        "if(t){o.headers=Object.assign({},o.headers,{'Authorization':'Bearer '+t});}"
        "}"
        "return _f.call(this,u,o);"
        "};"
        "}());</script>"
    )
    return html.replace("</head>", script + "</head>", 1)
