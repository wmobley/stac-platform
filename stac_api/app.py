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
_AUTH_SCHEME = "TapisBearerAuth"
_SEARCH_EXAMPLE = {
    "collections": ["demo"],
    "bbox": [-96, 29, -95, 30],
    "limit": 10,
}
_COLLECTION_EXAMPLE = {
    "type": "Collection",
    "stac_version": "1.0.0",
    "id": "demo",
    "description": "Demo collection",
    "license": "proprietary",
    "extent": {
        "spatial": {"bbox": [[-180, -90, 180, 90]]},
        "temporal": {"interval": [[None, None]]},
    },
    "links": [],
}
_ITEM_EXAMPLE = {
    "type": "Feature",
    "stac_version": "1.0.0",
    "id": "item-1",
    "collection": "demo",
    "geometry": {"type": "Point", "coordinates": [-95.4, 29.6]},
    "bbox": [-95.4, 29.6, -95.4, 29.6],
    "properties": {"datetime": "2024-06-01T00:00:00Z"},
    "assets": {},
    "links": [],
}


def _add_description(operation: dict, note: str) -> None:
    description = operation.get("description") or ""
    if note not in description:
        operation["description"] = f"{description.rstrip()}\n\n{note}".strip()


def _add_json_example(operation: dict, name: str, summary: str, value: dict) -> None:
    request_body = operation.get("requestBody")
    if not isinstance(request_body, dict):
        return
    content = request_body.setdefault("content", {})
    media = content.get("application/json") or content.get("application/geo+json")
    if media is None:
        media = content.setdefault("application/json", {})
    examples = media.setdefault("examples", {})
    examples.setdefault(name, {"summary": summary, "value": value})


def _install_openapi_auth_metadata() -> None:
    """Advertise the production write-auth requirement in Swagger/OpenAPI."""
    original_openapi = app.openapi

    def custom_openapi() -> dict:
        schema = original_openapi()
        components = schema.setdefault("components", {})
        schemes = components.setdefault("securitySchemes", {})
        schemes.setdefault(
            _AUTH_SCHEME,
            {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "Tapis access token",
                "description": (
                    "Required for STAC Transactions writes in production. "
                    "Use any valid Tapis access token for this tenant."
                ),
            },
        )

        auth_note = (
            "Production write calls require `Authorization: Bearer <Tapis access token>`. "
            "Local Docker compose may disable this with `STAC_AUTH_DISABLED=true`."
        )
        search_note = (
            "Use search for discovery across collections. Start with a collection id, bbox, "
            "datetime, and limit, then inspect returned item assets for CKAN download URLs."
        )
        collection_note = (
            "Create or replace a STAC Collection. In this platform, one CKAN dataset maps "
            "to one STAC Collection."
        )
        item_note = (
            "Create or replace a STAC Item inside a collection. Assets should point at the "
            "published CKAN resource URLs for the job output files."
        )
        auth_requirement = {_AUTH_SCHEME: []}
        for path, operations in schema.get("paths", {}).items():
            for method, operation in operations.items():
                method_upper = method.upper()
                if not isinstance(operation, dict):
                    continue
                if path.endswith("/search") and method_upper == "POST":
                    _add_description(operation, search_note)
                    _add_json_example(
                        operation,
                        "bbox-search",
                        "Search a collection by bounding box",
                        _SEARCH_EXAMPLE,
                    )
                if path == _WRITE_PREFIX and method_upper == "POST":
                    _add_description(operation, collection_note)
                    _add_json_example(
                        operation,
                        "create-collection",
                        "Create a collection",
                        _COLLECTION_EXAMPLE,
                    )
                if path.startswith(_WRITE_PREFIX) and path.endswith("/items") \
                        and method_upper == "POST":
                    _add_description(operation, item_note)
                    _add_json_example(
                        operation,
                        "create-item",
                        "Create an item with CKAN-backed assets",
                        _ITEM_EXAMPLE,
                    )
                if path.startswith(_WRITE_PREFIX) and method_upper in _WRITE_METHODS:
                    security = operation.setdefault("security", [])
                    if auth_requirement not in security:
                        security.insert(0, auth_requirement)
                    _add_description(operation, auth_note)
                    operation.setdefault("responses", {}).setdefault(
                        "401",
                        {"description": "Missing or invalid Tapis bearer token"},
                    )
        return schema

    app.openapi = custom_openapi


_install_openapi_auth_metadata()


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
