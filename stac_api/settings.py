"""Configuration for the STAC API service.

We run the **upstream** ``stac_fastapi.pgstac.app`` (env-driven) and only layer on
write-auth. Importing this module maps our friendly ``STAC_*`` variables onto the
env vars the upstream Settings actually reads, so it must be imported *before*
``stac_fastapi.pgstac.app``.

Upstream env vars (discovered from ``stac_fastapi.pgstac.config.Settings``):
  STAC_FASTAPI_TITLE / STAC_FASTAPI_DESCRIPTION, ROOT_PATH, CORS_ORIGINS,
  ENABLE_TRANSACTIONS_EXTENSIONS. PgSTAC connection uses the standard PG* vars.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# Bearer token required for write (Transactions) routes; reads stay public.
# Unset disables write protection — acceptable ONLY for local dev.
WRITE_TOKEN = os.environ.get("STAC_WRITE_TOKEN")


def _apply_upstream_env() -> None:
    """Translate our STAC_* config into the upstream Settings' env vars."""
    mapping = {
        "STAC_FASTAPI_TITLE": os.environ.get("STAC_API_TITLE", "TACC STAC API"),
        "STAC_FASTAPI_DESCRIPTION": os.environ.get(
            "STAC_API_DESCRIPTION", "Spatiotemporal catalog beside CKAN"
        ),
        "CORS_ORIGINS": os.environ.get("STAC_CORS_ORIGINS", "*"),
        # Turn the Transactions extension on by default (the publish task needs it).
        "ENABLE_TRANSACTIONS_EXTENSIONS": os.environ.get(
            "STAC_ENABLE_TRANSACTIONS", "true"
        ),
    }
    if os.environ.get("STAC_API_ROOT_PATH"):
        mapping["ROOT_PATH"] = os.environ["STAC_API_ROOT_PATH"]
    for key, value in mapping.items():
        os.environ.setdefault(key, value)


_apply_upstream_env()
