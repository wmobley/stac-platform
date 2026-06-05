"""Configuration for the STAC API service.

We run the **upstream** ``stac_fastapi.pgstac.app`` (env-driven) and layer on
write-auth + a static React viewer. Importing this module maps our friendly
``STAC_*`` variables onto the env vars the upstream Settings reads, so it must be
imported *before* ``stac_fastapi.pgstac.app``.

Upstream env vars used: STAC_FASTAPI_TITLE / STAC_FASTAPI_DESCRIPTION, CORS_ORIGINS,
ENABLE_TRANSACTIONS_EXTENSIONS, DOCS_URL, OPENAPI_URL, and **PREFIX_PATH** (serves
the API under /api/v1 so the React app can own /). PgSTAC connection uses the
standard PG* vars.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# --- write auth (per-user Tapis token) ---------------------------------------
# Tapis tenant base URL used to validate the caller's bearer token on writes.
TAPIS_BASE_URL = os.environ.get("TAPIS_BASE_URL", "https://portals.tapis.io").rstrip("/")
# Local-dev escape hatch: when true, write routes are open (no token required).
# Intended for docker-compose; never set in production.
AUTH_DISABLED = os.environ.get("STAC_AUTH_DISABLED", "").lower() in ("1", "true", "yes")

# Where the built React viewer lives (served at /). Absent dir -> no UI mount.
WEBUI_DIR = os.environ.get("STAC_WEBUI_DIR", "/app/webui")

# API path prefix - the React app owns "/", the STAC API serves under this.
API_PREFIX = os.environ.get("PREFIX_PATH", "/api/v1").rstrip("/") or ""
DOCS_URL = os.environ.get("DOCS_URL") or os.environ.get(
    "STAC_DOCS_URL",
    f"{API_PREFIX}/docs" if API_PREFIX else "/docs",
)
OPENAPI_URL = os.environ.get(
    "OPENAPI_URL",
) or os.environ.get(
    "STAC_OPENAPI_URL",
    f"{API_PREFIX}/openapi.json" if API_PREFIX else "/openapi.json",
)


def _default_description() -> str:
    api_base = API_PREFIX or "/"
    return f"""Spatiotemporal catalog API beside CKAN.

## How to use this API

- The React viewer is served at `/`; API calls use `{api_base}`.
- The OpenAPI schema is available at `{OPENAPI_URL}`.
- Read endpoints are public: list collections, inspect items, and search by
  collection, bbox, and datetime.
- Production write endpoints use the STAC Transactions extension and require
  `Authorization: Bearer <Tapis access token>`. Local Docker compose sets
  `STAC_AUTH_DISABLED=true`, so writes are open there.
- One CKAN dataset maps to one STAC Collection. CKAN resources grouped by
  `stac_item_id` become one STAC Item, and item assets point back to CKAN
  resource URLs.

## Common calls

```bash
curl -s {api_base}/collections
curl -s -X POST {api_base}/search \\
  -H 'Content-Type: application/json' \\
  -d '{{"collections":["demo"],"bbox":[-96,29,-95,30]}}'
```

For writes in production, click **Authorize** and paste a Tapis access token, or
send `Authorization: Bearer <token>` with collection and item transaction calls.
"""


def _apply_upstream_env() -> None:
    """Translate our STAC_* config into the upstream Settings' env vars."""
    mapping = {
        "STAC_FASTAPI_TITLE": os.environ.get("STAC_API_TITLE", "TACC STAC API"),
        "STAC_FASTAPI_DESCRIPTION": os.environ.get(
            "STAC_API_DESCRIPTION", _default_description()
        ),
        "CORS_ORIGINS": os.environ.get("STAC_CORS_ORIGINS", "*"),
        # Transactions on by default (the publish path + register UI need it).
        "ENABLE_TRANSACTIONS_EXTENSIONS": os.environ.get("STAC_ENABLE_TRANSACTIONS", "true"),
        # Keep FastAPI docs with the versioned API surface instead of /api.html.
        "DOCS_URL": DOCS_URL,
        "OPENAPI_URL": OPENAPI_URL,
        # Serve the STAC API under /api/v1 so "/" is free for the viewer.
        "PREFIX_PATH": API_PREFIX,
    }
    for key, value in mapping.items():
        os.environ.setdefault(key, value)


_apply_upstream_env()
