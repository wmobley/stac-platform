"""Unit tests for the pure context-layer builders + the register orchestration.

Run: `python -m pytest tests/test_context.py -q`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacmap import context as C
from stacmap import register_context
from stacmap.stac_client import StacClient

GEOJSON_SPEC = {
    "id": "major-aquifers",
    "title": "Major aquifers",
    "service": "geojson",
    "href": "https://example.com/0/query?f=geojson",
    "group": "Hydrogeology",
    "kind": "Major aquifer",
    "color": "#1d4ed8",
    "opacity": 0.35,
    "default_visible": False,
    "attribution": "TWDB",
    "bbox": [-107.0, 25.5, -93.0, 36.7],
}

WMS_SPEC = {
    "id": "faults",
    "service": "wms",
    "href": "https://example.com/wms",
    "wms_layers": ["faults"],
    "wms_transparent": True,
    "format": "image/png",
    "style": {"color": "#444", "weight": 2, "geomType": "LineString"},
}

MVT_SPEC = {
    "id": "wells",
    "service": "mvt",
    "href": "https://example.com/tiles/wells/{z}/{x}/{y}.mvt",
    "source_layers": ["wells"],
    "style": {"color": "#c2410c", "geomType": "Point", "radius": 4},
}


def test_geojson_item_is_self_describing_and_has_no_webmap_link():
    item = C.build_context_item(GEOJSON_SPEC)

    assert item["type"] == "Feature"
    assert item["id"] == "major-aquifers"
    assert item["collection"] == C.CONTEXT_COLLECTION_ID
    assert item["bbox"] == [-107.0, 25.5, -93.0, 36.7]
    # GeoJSON is fetched as a plain asset — no web-map-links link / extension.
    assert item["links"] == []
    assert item["stac_extensions"] == []

    asset = item["assets"]["service"]
    assert asset["href"] == GEOJSON_SPEC["href"]
    assert asset["type"] == "application/geo+json"
    assert asset["roles"] == ["overlay", "data"]

    ctx = item["properties"]["subside:context"]
    assert ctx["service"] == "geojson"
    assert ctx["kind"] == "Major aquifer"
    assert ctx["default_visible"] is False  # falsy value is kept, not pruned
    assert "wms_layers" not in ctx  # pruned: not applicable to geojson


def test_wms_item_emits_webmap_link_and_extension():
    item = C.build_context_item(WMS_SPEC)

    assert C.WEBMAP_EXT in item["stac_extensions"]
    (link,) = item["links"]
    assert link["rel"] == "wms"
    assert link["href"] == WMS_SPEC["href"]
    assert link["wms:layers"] == ["faults"]
    assert link["wms:transparent"] is True
    assert link["type"] == "image/png"


def test_mvt_item_uses_xyz_rel_with_mvt_media_type():
    item = C.build_context_item(MVT_SPEC)

    (link,) = item["links"]
    assert link["rel"] == "xyz"
    assert link["type"] == "application/vnd.mapbox-vector-tile"
    assert item["assets"]["service"]["type"] == "application/vnd.mapbox-vector-tile"
    assert item["properties"]["subside:context"]["source_layers"] == ["wells"]


FEATURE_SERVER_SPEC = {
    "id": "well-reports",
    "title": "Water Well Reports (TWDB)",
    "service": "feature-server",
    "href": "https://example.com/arcgis/rest/services/Public/WellReports/FeatureServer/0",
    "group": "Hydrogeology",
    "min_zoom": 9,
    "query_fields": ["County", "WellType", "DateOfWellCompletion"],
    "style": {"geomType": "Point", "color": "#b45309", "radius": 3},
    "bbox": [-106.6, 25.9, -93.5, 36.5],
}


def test_feature_server_item_is_self_describing_and_has_no_webmap_link():
    item = C.build_context_item(FEATURE_SERVER_SPEC)

    # Like geojson, a feature-server layer is fetched (viewport-driven) — no
    # web-map-links rel/extension.
    assert item["links"] == []
    assert item["stac_extensions"] == []
    assert item["assets"]["service"]["href"] == FEATURE_SERVER_SPEC["href"]
    assert item["assets"]["service"]["type"] == "application/geo+json"

    ctx = item["properties"]["subside:context"]
    assert ctx["service"] == "feature-server"
    assert ctx["min_zoom"] == 9
    assert ctx["query_fields"] == ["County", "WellType", "DateOfWellCompletion"]
    assert "where" not in ctx  # pruned: not supplied


def test_unknown_service_rejected():
    with pytest.raises(ValueError):
        C.build_context_item({"id": "x", "service": "tilejson", "href": "h"})


def test_item_without_bbox_covers_the_world():
    item = C.build_context_item({"id": "x", "service": "geojson", "href": "h"})
    assert item["bbox"] == [-180.0, -90.0, 180.0, 90.0]
    assert item["properties"]["title"] == "x"  # falls back to id


def test_register_upserts_collection_then_each_item():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # The client's base_url carries the /api/v1 prefix, so match on suffixes.
        path = request.url.path
        if request.method == "GET" and "/collections/" in path:
            return httpx.Response(404)  # collection missing -> force create
        if request.method == "POST" and path.endswith("/collections"):
            seen.append(("POST", "/collections"))
            return httpx.Response(201, json={})
        if request.method == "PUT" and "/items/" in path:
            seen.append(("PUT", path[path.index("/collections"):]))
            return httpx.Response(200, json={})
        return httpx.Response(404)

    client = StacClient(url="https://stac.example/api/v1", transport=httpx.MockTransport(handler))
    try:
        ids = register_context.register(
            {"collection": {"id": "subside-context"}, "layers": [GEOJSON_SPEC, WMS_SPEC]},
            stac_client=client,
        )
    finally:
        client.close()

    assert ids == ["major-aquifers", "faults"]
    assert seen[0] == ("POST", "/collections")
    assert ("PUT", "/collections/subside-context/items/major-aquifers") in seen
    assert ("PUT", "/collections/subside-context/items/faults") in seen


def test_register_prunes_items_no_longer_in_specs():
    # The collection already holds an item ("satellite") that the specs no longer
    # list; register() should delete it so the catalog stays declarative.
    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/collections/subside-context"):
            return httpx.Response(200, json={"id": "subside-context"})  # exists
        if request.method == "GET" and path.endswith("/items"):
            return httpx.Response(200, json={"features": [
                {"id": "major-aquifers"}, {"id": "satellite"},
            ]})
        if request.method == "PUT" and "/items/" in path:
            return httpx.Response(200, json={})
        if request.method == "DELETE" and "/items/" in path:
            deleted.append(path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        return httpx.Response(404)

    client = StacClient(url="https://stac.example/api/v1", transport=httpx.MockTransport(handler))
    try:
        ids = register_context.register(
            {"collection": {"id": "subside-context"}, "layers": [GEOJSON_SPEC]},
            stac_client=client,
        )
    finally:
        client.close()

    assert ids == ["major-aquifers"]
    assert deleted == ["satellite"]  # the orphan was pruned; the kept item was not


def test_build_context_item_service_kinds():
    """The builder handles each service kind + render hints (project-agnostic).

    Project-specific specs (e.g. SUBSIDE's texas_counties / aquifers / well-reports)
    now live in the consuming repo at ``subside/stac/context_layers.json`` — not in
    this generic platform — so we test the *builder* against an inline spec instead
    of any shipped file.
    """
    specs = {
        "collection": {"id": "demo-context"},
        "layers": [
            {"id": "wells", "title": "Wells", "service": "feature-server",
             "href": "https://example/FeatureServer/0", "min_zoom": 9,
             "query_fields": ["A", "B"]},
            {"id": "aquifers", "title": "Aquifers", "service": "geojson",
             "href": "https://example/query?f=geojson", "visible_when": "anon"},
            {"id": "tiles", "title": "Tiles", "service": "wms",
             "href": "https://example/wms", "wms_layers": ["x"]},
        ],
    }
    items = {
        layer["id"]: C.build_context_item(layer, collection_id=specs["collection"]["id"])
        for layer in specs["layers"]
    }

    # feature-server: viewport-driven, gated by min_zoom, no web-map-links link.
    wells = items["wells"]
    assert wells["properties"]["subside:context"]["service"] == "feature-server"
    assert wells["properties"]["subside:context"]["min_zoom"] == 9
    assert wells["links"] == []
    # geojson: visible_when passthrough, no link.
    assert items["aquifers"]["properties"]["subside:context"]["visible_when"] == "anon"
    assert items["aquifers"]["links"] == []
    # wms: gets a web-map-links link relation.
    assert any(link.get("rel") == "wms" for link in items["tiles"]["links"])
