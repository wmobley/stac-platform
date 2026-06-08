"""Pure STAC Item / Collection builders. No I/O, no network — easily unit-tested.

Both the publish task and the reconcile bridge build their STAC documents here, so
the two write paths produce byte-identical shapes.
"""

from __future__ import annotations

from typing import Any

from .manifest import Granule

STAC_VERSION = "1.0.0"

# Extension schema URIs. We declare one only when the corresponding fields are
# actually present on an asset — an extension URI without populated fields proves
# nothing, so the `stac_extensions` list is computed per-item, not hardcoded.
RASTER_EXT = "https://stac-extensions.github.io/raster/v1.1.0/schema.json"
FILE_EXT = "https://stac-extensions.github.io/file/v2.1.0/schema.json"
RENDER_EXT = "https://stac-extensions.github.io/render/v1.0.0/schema.json"

# Default tiler recipe for the displacement COGs: a zero-centered diverging ramp,
# rescaled to each band's own statistics.
RENDER_COLORMAP = "rdbu_r"
RENDER_RESAMPLING = "bilinear"


def _render_for_asset(key: str, asset: dict[str, Any]) -> dict[str, Any] | None:
    """A Render recipe for a COG asset that carries raster:bands statistics."""
    bands = asset.get("raster:bands")
    if not bands:
        return None
    stats = bands[0].get("statistics") or {}
    lo, hi = stats.get("minimum"), stats.get("maximum")
    if lo is None or hi is None:
        return None
    return {
        "title": asset.get("title") or key,
        "assets": [key],
        "rescale": [[lo, hi]],
        "colormap_name": RENDER_COLORMAP,
        "resampling": RENDER_RESAMPLING,
    }


def _renders(assets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build the Item-level `renders` map from every COG asset with statistics."""
    out: dict[str, dict[str, Any]] = {}
    for key, asset in assets.items():
        recipe = _render_for_asset(key, asset)
        if recipe is not None:
            out[key] = recipe
    return out


def _item_extensions(
    assets: dict[str, dict[str, Any]], has_renders: bool
) -> list[str]:
    """Declare only the extensions whose fields actually appear on this Item."""
    exts: list[str] = []
    if any("raster:bands" in a for a in assets.values()):
        exts.append(RASTER_EXT)
    if any(("file:size" in a or "file:checksum" in a) for a in assets.values()):
        exts.append(FILE_EXT)
    if has_renders:
        exts.append(RENDER_EXT)
    return exts


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

    renders = _renders(assets)
    if renders:
        properties["renders"] = renders

    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": _item_extensions(assets, bool(renders)),
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
