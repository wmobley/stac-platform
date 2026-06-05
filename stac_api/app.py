"""STAC API app + static React viewer.

Runs the upstream ``stac_fastapi.pgstac.app`` under the ``/api/v1`` prefix and:
  * guards write (Transactions) routes with **per-user Tapis auth** (any valid
    Tapis token; reads stay public),
  * serves the built React viewer (``webui/dist``) at ``/`` as an SPA,
  * exposes ``/healthz``.

Importing ``settings`` first maps our STAC_* config onto the upstream env vars
(including PREFIX_PATH=/api/v1).

Run with: ``uvicorn stac_api.app:app``
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import settings as cfg
from . import auth
from . import _pods_tls

# asyncpg direct-TLS (+ALPN) for the Tapis pods Postgres tunnel; no-op locally.
_pods_tls.apply()

from stac_fastapi.pgstac.app import app  # noqa: E402  (after env mapping + patch)

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
# Write (Transactions) routes live under <prefix>/collections.
_WRITE_PREFIX = f"{cfg.API_PREFIX.rstrip('/')}/collections"


@app.middleware("http")
async def _guard_writes(request: Request, call_next):
    """Require a valid Tapis token for transaction routes; reads stay public.

    Matched: write methods whose path is under ``/api/v1/collections`` (create/
    replace/delete collections + items). ``POST /api/v1/search`` is a read and is
    not matched. Open when STAC_AUTH_DISABLED=true (local dev).
    """
    if not cfg.AUTH_DISABLED and request.method in _WRITE_METHODS \
            and _WRITE_PREFIX in request.url.path:
        token = auth.bearer_from_header(request.headers.get("authorization"))
        if not auth.validate_tapis_token(token):
            return JSONResponse(
                {"detail": "a valid Tapis bearer token is required to write"},
                status_code=401,
            )
    return await call_next(request)


@app.get("/healthz", tags=["health"], include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok"}


# Serve the built React viewer at "/" (SPA fallback). Mounted last so the API
# routes (under /api/v1) and /healthz take precedence. Skipped if not built yet.
if os.path.isdir(cfg.WEBUI_DIR):
    app.mount("/", StaticFiles(directory=cfg.WEBUI_DIR, html=True), name="webui")
