"""Pure STAC Item / Collection builders. No I/O, no network — easily unit-tested.

Both the publish task and the reconcile bridge build their STAC documents here, so
the two write paths produce byte-identical shapes.
"""

from __future__ import annotations

from typing import Any

from .manifest import Granule

STAC_VERSION = "1.0.0"

# Extensions whose fields we emit (raster stats on the COG asset).
ITEM_EXTENSIONS = [
    "https://stac-extensions.github.io/raster/v1.1.0/schema.json",
]


def bbox_to_geometry(bbox: list[float]) -> dict[str, Any]:
    """[w, s, e, n] -> a closed GeoJSON Polygon (CCW exterior ring)."""
    w, s, e, n = bbox
    return {
        "type": "Polygon",
        "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
    }


def _temporal_props(g: Granule) -> dict[str, Any]:
    """Either {datetime} or {datetime: null, start_datetime, end_datetime}."""
    if g.start_datetime and g.end_datetime:
        return {
            "datetime": None,
            "start_datetime": g.start_datetime,
            "end_datetime": g.end_datetime,
        }
    return {"datetime": g.datetime}


def build_item(
    granule: Granule,
    collection_id: str,
    assets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Assemble a STAC Item from a Granule and its (already-built) assets."""
    properties = dict(granule.properties)
    properties.update(_temporal_props(granule))

    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": list(ITEM_EXTENSIONS),
        "id": granule.item_id,
        "collection": collection_id,
        "geometry": bbox_to_geometry(granule.bbox),
        "bbox": list(granule.bbox),
        "properties": properties,
        "assets": assets,
        "links": [],  # stac-fastapi-pgstac injects self/parent/collection links
    }


# A wide-open default extent; real extents are recomputed from member items by the
# bridge (and by PgSTAC's `update_collection_extents`).
_WORLD_BBOX = [-180.0, -90.0, 180.0, 90.0]


def build_collection(
    collection_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    spatial_bbox: list[float] | None = None,
    temporal_interval: list[list[str | None]] | None = None,
    license: str = "proprietary",
) -> dict[str, Any]:
    """Build a STAC Collection document."""
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection_id,
        "title": title or collection_id,
        "description": description or f"STAC collection {collection_id}",
        "license": license,
        "extent": {
            "spatial": {"bbox": [spatial_bbox or _WORLD_BBOX]},
            "temporal": {"interval": temporal_interval or [[None, None]]},
        },
        "links": [],
    }


def union_bbox(boxes: list[list[float]]) -> list[float]:
    """Spatial union of [w,s,e,n] boxes (for recomputing a Collection extent)."""
    if not boxes:
        return list(_WORLD_BBOX)
    w = min(b[0] for b in boxes)
    s = min(b[1] for b in boxes)
    e = max(b[2] for b in boxes)
    n = max(b[3] for b in boxes)
    return [w, s, e, n]


def union_interval(items: list[Granule]) -> list[list[str | None]]:
    """Temporal union over granules -> a single [[min_start, max_end]] interval."""
    starts = [g.start_datetime or g.datetime for g in items if (g.start_datetime or g.datetime)]
    ends = [g.end_datetime or g.datetime for g in items if (g.end_datetime or g.datetime)]
    return [[min(starts) if starts else None, max(ends) if ends else None]]
