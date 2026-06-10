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
import sys
from pathlib import Path

from . import context as C
from .stac_client import StacClient

#: Shipped default specs (the two ArcGIS aquifer overlays the UI used to hardcode).
DEFAULT_SPECS = Path(__file__).resolve().parents[1] / "context_layers.json"


def register(specs: dict, *, stac_client: StacClient | None = None) -> list[str]:
    """Upsert the context Collection + every layer Item. Returns the layer ids."""
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

    specs = json.loads(Path(args.specs).read_text())
    ids = register(specs)
    print(f"registered {len(ids)} context layer(s) -> collection "
          f"{(specs.get('collection') or {}).get('id') or C.CONTEXT_COLLECTION_ID}: "
          f"{', '.join(ids)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
