"""STAC API app.

Runs the upstream ``stac_fastapi.pgstac.app`` (which wires CoreCrudClient, the
search/collection extensions, CORS, and — with ENABLE_TRANSACTIONS_EXTENSIONS —
the Transactions extension) and layers on:

  * a **bearer-token guard** on write routes (POST/PUT/DELETE under /collections),
  * a ``/healthz`` endpoint.

Importing ``settings`` first maps our STAC_* config onto the upstream env vars.

Run with: ``uvicorn stac_api.app:app``
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from . import settings as cfg  # noqa: F401  (import maps env BEFORE the app import)
from . import _pods_tls

# Teach asyncpg to do PostgreSQL direct SSL (TLS + ALPN 'postgresql') for the
# Tapis pods :443 tunnel. No-op unless PGSSLNEGOTIATION=direct. Must run before
# the app's startup builds the connection pool.
_pods_tls.apply()

from stac_fastapi.pgstac.app import app  # noqa: E402

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


@app.middleware("http")
async def _guard_writes(request: Request, call_next):
    """Require the write token for transaction routes; reads stay public.

    Transaction routes live under ``/collections`` (create/replace/delete
    collections + items). ``POST /search`` is a read and is intentionally not
    matched. No-op when STAC_WRITE_TOKEN is unset (local dev).
    """
    if cfg.WRITE_TOKEN and request.method in _WRITE_METHODS:
        path = request.url.path
        if "/collections" in path:
            auth = request.headers.get("authorization", "")
            token = auth[7:] if auth.lower().startswith("bearer ") else None
            if token != cfg.WRITE_TOKEN:
                return JSONResponse(
                    {"detail": "missing or invalid write token"}, status_code=401
                )
    return await call_next(request)


@app.get("/healthz", tags=["health"], include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}
