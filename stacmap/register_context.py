"""Register SUBSIDE *context* layers (basemap / reference overlays) into STAC.

Reads an author-facing specs file and upserts a ``subside-context`` Collection plus
one Item per layer over the STAC Transactions API. Idempotent: re-running updates
the same Items in place, so adding/editing a layer is "edit the specs file, re-run"
— no frontend deploy.

Specs file (JSON) shape::

    {
      "collection": {"id": "subside-context", "title": "...", "description": "..."},
      "layers": [
        {"id": "major-aquifers", "title": "Major aquifers", "service": "geojson",
         "href": "https://.../query?...f=geojson", "group": "Hydrogeology",
         "kind": "Major aquifer", "color": "#1d4ed8", "default_visible": false,
         "attribution": "TWDB via ArcGIS", "bbox": [-107, 25, -93, 37]},
        ...
      ]
    }

Each layer's ``service`` is one of ``geojson`` / ``wms`` / ``xyz`` / ``mvt``; see
:func:`stacmap.context.build_context_item` for the full set of optional fields.

Usage::

    python -m stacmap.register_context --specs context_layers.json
    # honors STAC_URL / STAC_TOKEN from the environment (see StacClient)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import context as C
from .stac_client import StacClient

# Load .env so local runs pick up STAC_*/TAPIS_* (no-op in the pod, where these come
# from the container env and there's no .env).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

#: Shipped default specs (the two ArcGIS aquifer overlays the UI used to hardcode).
DEFAULT_SPECS = Path(__file__).resolve().parents[1] / "context_layers.json"


def register(
    specs: dict, *, stac_client: StacClient | None = None, prune: bool = True
) -> list[str]:
    """Upsert the context Collection + every layer Item. Returns the layer ids.

    The ``subside-context`` collection is fully owned by the specs file, so by
    default we also prune items that are no longer in it — making registration
    declarative (e.g. dropping the OPERA "satellite" availability layer here
    removes it from the map). Pruning is best-effort: a STAC API that doesn't
    support deletes leaves the stale item in place (the UI hides it regardless).
    """
    coll_spec = specs.get("collection") or {}
    collection_id = coll_spec.get("id") or C.CONTEXT_COLLECTION_ID
    collection = C.build_context_collection(
        collection_id=collection_id,
        title=coll_spec.get("title"),
        description=coll_spec.get("description"),
    )

    own_client = stac_client is None
    stac_client = stac_client or StacClient()
    try:
        stac_client.ensure_collection(collection)
        ids: list[str] = []
        for layer in specs.get("layers", []):
            item = C.build_context_item(layer, collection_id=collection_id)
            stac_client.upsert_item(collection_id, item)
            ids.append(item["id"])
        if prune:
            keep = set(ids)
            for existing in stac_client.list_item_ids(collection_id):
                if existing not in keep and stac_client.delete_item(collection_id, existing):
                    print(f"pruned context layer no longer in specs: {existing}")
        return ids
    finally:
        if own_client:
            stac_client.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Register SUBSIDE context layers into STAC.")
    p.add_argument(
        "--specs",
        default=str(DEFAULT_SPECS),
        help=f"Path to the context-layers specs JSON (default: {DEFAULT_SPECS.name}).",
    )
    args = p.parse_args(argv)

    # The STAC API is Tapis-fronted: prefer an explicit STAC_TOKEN, else mint a Tapis
    # JWT via password grant (TAPIS_BASE_URL/TAPIS_USERNAME/TAPIS_PASSWORD), sent as a
    # bearer token — same flow as stacmap.register_external.
    token = os.environ.get("STAC_TOKEN")
    if not token:
        from .tapis_auth import mint_tapis_jwt
        token = mint_tapis_jwt()
    client = StacClient(token=token)

    specs = json.loads(Path(args.specs).read_text())
    try:
        ids = register(specs, stac_client=client)
    finally:
        client.close()
    print(f"registered {len(ids)} context layer(s) -> collection "
          f"{(specs.get('collection') or {}).get('id') or C.CONTEXT_COLLECTION_ID}: "
          f"{', '.join(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
