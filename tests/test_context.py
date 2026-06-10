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


def test_shipped_seed_specs_build_cleanly():
    specs = json.loads(register_context.DEFAULT_SPECS.read_text())
    items = [C.build_context_item(layer, collection_id=specs["collection"]["id"])
             for layer in specs["layers"]]
    assert {i["id"] for i in items} == {
        "satellite", "texas_counties", "major-aquifers", "minor-aquifers",
    }
    # The OPERA frame layer carries the availability role + an MVT service link.
    sat = next(i for i in items if i["id"] == "satellite")
    assert sat["properties"]["subside:context"]["role"] == "availability"
    assert sat["properties"]["subside:context"]["service"] == "mvt"
    assert sat["links"][0]["type"] == "application/vnd.mapbox-vector-tile"
