"""Configuration for the STAC API service.

We run the **upstream** ``stac_fastapi.pgstac.app`` (env-driven) and layer on
write-auth + a static React viewer. Importing this module maps our friendly
``STAC_*`` variables onto the env vars the upstream Settings reads, so it must be
imported *before* ``stac_fastapi.pgstac.app``.

Upstream env vars used: STAC_FASTAPI_TITLE / STAC_FASTAPI_DESCRIPTION, CORS_ORIGINS,
ENABLE_TRANSACTIONS_EXTENSIONS, and **PREFIX_PATH** (serves the API under /api/v1
so the React app can own /). PgSTAC connection uses the standard PG* vars.
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

# API path prefix — the React app owns "/", the STAC API serves under this.
API_PREFIX = os.environ.get("PREFIX_PATH", "/api/v1")


def _apply_upstream_env() -> None:
    """Translate our STAC_* config into the upstream Settings' env vars."""
    mapping = {
        "STAC_FASTAPI_TITLE": os.environ.get("STAC_API_TITLE", "TACC STAC API"),
        "STAC_FASTAPI_DESCRIPTION": os.environ.get(
            "STAC_API_DESCRIPTION", "Spatiotemporal catalog beside CKAN"
        ),
        "CORS_ORIGINS": os.environ.get("STAC_CORS_ORIGINS", "*"),
        # Transactions on by default (the publish path + register UI need it).
        "ENABLE_TRANSACTIONS_EXTENSIONS": os.environ.get("STAC_ENABLE_TRANSACTIONS", "true"),
        # Serve the STAC API under /api/v1 so "/" is free for the viewer.
        "PREFIX_PATH": API_PREFIX,
    }
    for key, value in mapping.items():
        os.environ.setdefault(key, value)


_apply_upstream_env()
