"""Pure builders for *context* layers — basemap / reference overlays the SUBSIDE
map renders directly (WMS, XYZ raster tiles, MVT vector tiles, or remote GeoJSON).

Unlike the per-run COG Items (see :mod:`stacmap.stac`), a context layer is not a
searchable spatiotemporal product — it is a pointer to a *map service* plus the
render hints a client needs to draw it. They all live in one Collection
(``subside-context``) so the UI can discover them with a single request and add a
new layer by registering an Item — no frontend deploy.

Two consumers of the same render hints:
  * standards-compliant clients read the **web-map-links** link on the Item;
  * the SUBSIDE UI reads the self-contained ``subside:context`` property block,
    which is the contract we control end-to-end (see ui/src/lib/stacContext.js).

No I/O, no network — easily unit-tested. The :mod:`stacmap.register_context` CLI
turns author-facing specs into these documents and upserts them via the STAC
Transactions client.
"""

from __future__ import annotations

from typing import Any

from .stac import STAC_VERSION, bbox_to_geometry

#: One implicit root catalog, this one extra Collection holding every context layer.
CONTEXT_COLLECTION_ID = "subside-context"

#: Web Map Links extension — adds wms/xyz/wmts/tilejson link relations so a STAC
#: client can render a service directly, without a tiler.
WEBMAP_EXT = "https://stac-extensions.github.io/web-map-links/v1.2.0/schema.json"

#: Context layers are not time-specific, but STAC/PgSTAC require a datetime on
#: every Item. Use a fixed non-temporal anchor rather than "now" so re-registering
#: a layer is byte-stable (and the module stays free of wall-clock reads).
CONTEXT_DATETIME = "2020-01-01T00:00:00Z"

#: A context layer with no declared extent covers the whole world.
_WORLD_BBOX = [-180.0, -90.0, 180.0, 90.0]

#: Recognized service kinds and the asset media-type each one advertises.
#:
#: ``feature-server`` is an Esri ArcGIS *FeatureServer* layer endpoint (e.g.
#: ``.../FeatureServer/0``). Unlike ``geojson`` (a single static fetch, capped at
#: the server's maxRecordCount), the SUBSIDE UI consumes it *viewport-driven*:
#: re-querying the layer's ``/query`` endpoint with the current map-bounds envelope
#: on each pan/zoom (``f=geojson``), gated by ``min_zoom``. That lets a layer with
#: far more features than maxRecordCount (e.g. TWDB's 680k well reports) render
#: only what's in view, without standing up our own tiler.
_SERVICE_MEDIA_TYPE = {
    "geojson": "application/geo+json",
    "feature-server": "application/geo+json",
    "wms": "image/png",
    "xyz": "image/png",
    "mvt": "application/vnd.mapbox-vector-tile",
}


def _prune(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None or an empty list/dict/string (keep 0/False)."""
    return {k: v for k, v in d.items() if not (v is None or v == [] or v == {} or v == "")}


def _webmap_link(service: str, href: str, hints: dict[str, Any], title: str) -> dict[str, Any] | None:
    """The web-map-links mirror of a service, for generic STAC clients.

    GeoJSON has no web-map-links relation (it is fetched as a normal asset), so it
    returns None — only the tiled services get a link.
    """
    media = hints.get("format") or _SERVICE_MEDIA_TYPE.get(service)
    if service == "wms":
        return _prune({
            "rel": "wms",
            "href": href,
            "type": media,
            "title": title,
            "wms:layers": hints.get("wms_layers") or [],
            "wms:styles": hints.get("wms_styles") or [],
            "wms:transparent": hints.get("wms_transparent"),
        })
    if service in ("xyz", "mvt"):
        # web-map-links has no dedicated MVT rel; xyz + the MVT media type is the
        # established convention for vector tiles served on an {z}/{x}/{y} template.
        return _prune({"rel": "xyz", "href": href, "type": media, "title": title})
    return None


def _context_hints(spec: dict[str, Any], service: str) -> dict[str, Any]:
    """The self-contained render block the SUBSIDE UI reads (`subside:context`)."""
    return _prune({
        "service": service,
        "group": spec.get("group"),
        "kind": spec.get("kind"),
        # A UI behavior marker (e.g. "availability" → the OPERA frame layer gets
        # viewport availability shading + click-to-pick-frame instead of a static
        # style). Plain layers omit it.
        "role": spec.get("role"),
        "feature_count": spec.get("feature_count"),
        # Default-on policy the UI resolves against auth state: "authed" (on only
        # when logged in), "anon" (on only when logged out), "always", or "never".
        # Omitted → the plain `default_visible` boolean applies.
        "visible_when": spec.get("visible_when"),
        "color": spec.get("color"),
        "style": spec.get("style"),
        "opacity": spec.get("opacity"),
        "min_zoom": spec.get("min_zoom"),
        "max_zoom": spec.get("max_zoom"),
        "default_visible": bool(spec.get("default_visible", False)),
        "attribution": spec.get("attribution"),
        "legend": spec.get("legend"),
        # WMS specifics (also mirrored onto the web-map-links link).
        "wms_layers": spec.get("wms_layers"),
        "wms_styles": spec.get("wms_styles"),
        "wms_transparent": spec.get("wms_transparent"),
        "format": spec.get("format"),
        # MVT specifics: the source-layer name(s) VectorGrid styles are keyed by.
        "source_layers": spec.get("source_layers"),
        # feature-server specifics: the outFields requested per viewport query
        # (drives the popup), and an optional server-side WHERE filter.
        "query_fields": spec.get("query_fields"),
        "where": spec.get("where"),
    })


def build_context_item(spec: dict[str, Any], collection_id: str = CONTEXT_COLLECTION_ID) -> dict[str, Any]:
    """Assemble a context-layer STAC Item from an author-facing spec.

    Required spec keys: ``id``, ``service`` (one of geojson/wms/xyz/mvt), ``href``.
    Everything else (``title``, ``group``, ``kind``, ``color``, ``style``,
    ``opacity``, ``min_zoom``/``max_zoom``, ``default_visible``, ``attribution``,
    ``legend``, ``bbox``, and the WMS/MVT specifics) is optional render metadata.
    """
    layer_id = spec["id"]
    service = spec["service"]
    href = spec["href"]
    if service not in _SERVICE_MEDIA_TYPE:
        raise ValueError(f"unknown context service {service!r} (expected one of {sorted(_SERVICE_MEDIA_TYPE)})")

    title = spec.get("title") or layer_id
    bbox = spec.get("bbox") or list(_WORLD_BBOX)
    hints = _context_hints(spec, service)
    media = hints.get("format") or _SERVICE_MEDIA_TYPE[service]

    properties: dict[str, Any] = {
        "datetime": CONTEXT_DATETIME,
        "title": title,
        "subside:context": hints,
    }
    if spec.get("description"):
        properties["description"] = spec["description"]

    link = _webmap_link(service, href, hints, title)

    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [WEBMAP_EXT] if link else [],
        "id": layer_id,
        "collection": collection_id,
        "geometry": bbox_to_geometry(bbox),
        "bbox": list(bbox),
        "properties": properties,
        "assets": {
            "service": _prune({
                "href": href,
                "type": media,
                "title": title,
                "roles": ["overlay", "data"],
            }),
        },
        "links": [link] if link else [],
    }


def build_context_collection(
    *,
    collection_id: str = CONTEXT_COLLECTION_ID,
    title: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Build the (world-extent, open-ended) Collection that holds context layers."""
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection_id,
        "title": title or "SUBSIDE context layers",
        "description": description
        or "Basemap / reference overlays (WMS, XYZ, vector tiles, GeoJSON) the "
        "SUBSIDE map discovers and renders dynamically.",
        "license": "various",
        "extent": {
            "spatial": {"bbox": [list(_WORLD_BBOX)]},
            "temporal": {"interval": [[None, None]]},
        },
        "links": [],
    }
