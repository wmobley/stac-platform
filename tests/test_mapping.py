"""Unit tests for the pure mapping core (no network / DB / CKAN needed).

Run: `python -m pytest tests/ -q`  (or `python tests/test_mapping.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacmap import assets as A
from stacmap import stac
from stacmap.manifest import granule_from_subside_manifest, parse_manifest

# A trimmed real SUBSIDE run manifest (see subside h2i_lab walkthrough_outputs).
MANIFEST = {
    "bbox": {"lat_max": 29.75, "lat_min": 29.55, "lon_max": -95.35, "lon_min": -95.55},
    "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
    "frame_ids": [8882, 38238],
    "product_count": 2,
    "product_urls": [
        "https://cumulus.asf.earthdatacloud.nasa.gov/OPERA/x/OPERA_L3_DISP-S1_IW_F08882.nc",
    ],
    "artifacts": {"display_range": {"vmin": -0.0095, "vmax": 0.0649}},
}


# A trimmed real WERC run manifest (see subside werc walkthrough_outputs): no
# top-level bbox, two GeoTIFFs with their own ranges, manifest name werc-run-*.
WERC_MANIFEST = {
    "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
    "frame_id": 8882,
    "artifacts": {
        "cumulative_displacement_geotiff": {
            "path": "/x/opera_disp_s1_cumulative.tif",
            "bounds": [-95.5551, 29.5500, -95.3455, 29.7500],
            "clip_range_mm": [-19.67, 21.66], "crs": "EPSG:4326",
        },
        "velocity_geotiff": {
            "path": "/x/opera_disp_s1_velocity.tif",
            "bounds": [-95.5551, 29.5500, -95.3455, 29.7500],
            "p02_p98_mm_per_year": [-553.1, 594.1], "crs": "EPSG:4326",
        },
    },
}


def test_parse_h2i():
    g, cogs, overlay = parse_manifest(MANIFEST, "job-123")
    assert [c.key for c in cogs] == ["cog"]
    assert cogs[0].filename == "disp_displacement.tif"
    assert cogs[0].display_range == {"vmin": -0.0095, "vmax": 0.0649}
    assert overlay == "disp_overlay.png"
    assert g.bbox == [-95.55, 29.55, -95.35, 29.75]


def test_parse_werc():
    g, cogs, overlay = parse_manifest(WERC_MANIFEST, "werc-1")
    assert [c.key for c in cogs] == ["cumulative", "velocity"]
    assert cogs[0].filename == "opera_disp_s1_cumulative.tif"
    assert cogs[0].display_range == {"vmin": -19.67, "vmax": 21.66}
    assert cogs[1].filename == "opera_disp_s1_velocity.tif"
    assert cogs[1].display_range == {"vmin": -553.1, "vmax": 594.1}
    assert overlay is None                          # WERC has no overlay
    assert g.bbox == [-95.5551, 29.55, -95.3455, 29.75]   # from GeoTIFF bounds
    assert g.start_datetime == "2024-06-01T00:00:00Z"
    assert g.properties["subside:frame_id"] == 8882


def test_granule_from_manifest():
    g = granule_from_subside_manifest(MANIFEST, "job-123")
    assert g.item_id == "job-123"
    assert g.bbox == [-95.55, 29.55, -95.35, 29.75]  # [w, s, e, n]
    assert g.datetime is None  # date range -> start/end, datetime null
    assert g.start_datetime == "2024-06-01T00:00:00Z"
    assert g.end_datetime == "2024-09-01T00:00:00Z"
    assert g.source_urls and g.source_urls[0].endswith(".nc")
    assert g.display_range == {"vmin": -0.0095, "vmax": 0.0649}
    assert g.properties["subside:frame_ids"] == [8882, 38238]


def test_asset_classification():
    key, asset = A.asset_for_resource("disp_displacement.tif", "https://ckan/x.tif",
                                      display_range={"vmin": -1.0, "vmax": 1.0})
    assert key == "cog"
    assert asset["type"] == A.COG_MEDIA_TYPE
    assert "data" in asset["roles"] and "visual" in asset["roles"]
    # display range -> raster:bands statistics for tiler auto-rescale
    assert asset["raster:bands"][0]["statistics"] == {"minimum": -1.0, "maximum": 1.0}

    key, asset = A.asset_for_resource("overlay.png", "https://ckan/x.png")
    assert key == "overlay" and asset["type"] == A.PNG_MEDIA_TYPE

    key, asset = A.asset_for_resource("run-manifest.json", "https://ckan/m.json")
    assert key == "metadata"


def test_non_finite_display_range_is_dropped():
    """NaN/Infinity stats must never reach the Item -- httpx's strict JSON encoder
    (allow_nan=False) rejects them outright, crashing the STAC publish PUT."""
    import math

    key, asset = A.asset_for_resource(
        "d.tif", "https://ckan/d.tif", display_range={"vmin": float("nan"), "vmax": 1.0},
    )
    assert key == "cog"
    assert "raster:bands" not in asset

    key, asset = A.asset_for_resource(
        "d.tif", "https://ckan/d.tif", display_range={"vmin": -1.0, "vmax": math.inf},
    )
    assert "raster:bands" not in asset

    # A render recipe requires raster:bands statistics, so it's skipped too.
    item = stac.build_item(
        granule_from_subside_manifest(MANIFEST, "job-123"),
        "subsidence-rates",
        {"cog": asset},
    )
    assert "renders" not in item["properties"]
    assert stac.RASTER_EXT not in item["stac_extensions"]


def test_file_extension_fields():
    # size + a bare md5 digest -> file:size (int) + file:checksum (md5 multihash).
    _, cog = A.asset_for_resource(
        "d.tif", "https://ckan/d.tif",
        byte_size="123456", checksum="d41d8cd98f00b204e9800998ecf8427e",
    )
    assert cog["file:size"] == 123456
    assert cog["file:checksum"] == "d50110d41d8cd98f00b204e9800998ecf8427e"

    # sha2-256 (64 hex) -> 1220 prefix; junk / missing -> no file:checksum.
    assert A._multihash("a" * 64).startswith("1220")
    _, link = A.asset_for_resource("p.nc", "https://x/p.nc", byte_size=None, checksum="nope")
    assert "file:size" not in link and "file:checksum" not in link


def test_render_recipe_and_dynamic_extensions():
    g = granule_from_subside_manifest(MANIFEST, "job-123")
    _, cog = A.asset_for_resource(
        "d.tif", "https://ckan/d.tif", display_range=g.display_range, byte_size=42,
    )
    item = stac.build_item(g, "subsidence-rates", {"cog": cog})

    # Render recipe derived from the COG's raster:bands statistics.
    render = item["properties"]["renders"]["cog"]
    assert render["assets"] == ["cog"]
    assert render["rescale"] == [[-0.0095, 0.0649]]
    assert render["colormap_name"] == stac.RENDER_COLORMAP

    # Declared extensions == exactly those whose fields are present (raster/file/render).
    assert item["stac_extensions"] == [stac.RASTER_EXT, stac.FILE_EXT, stac.RENDER_EXT]

    # An Item with no extension fields declares nothing and has no renders.
    plain = stac.build_item(g, "c", {"meta": A.make_asset("https://x/m.json")})
    assert plain["stac_extensions"] == []
    assert "renders" not in plain["properties"]


def test_build_item_and_collection():
    g = granule_from_subside_manifest(MANIFEST, "job-123")
    _, cog = A.asset_for_resource("d.tif", "https://ckan/d.tif", display_range=g.display_range)
    item = stac.build_item(g, "subsidence-rates", {"cog": cog})

    assert item["type"] == "Feature"
    assert item["id"] == "job-123"
    assert item["collection"] == "subsidence-rates"
    assert item["bbox"] == [-95.55, 29.55, -95.35, 29.75]
    assert item["geometry"]["type"] == "Polygon"
    # closed ring (first == last)
    ring = item["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert item["properties"]["datetime"] is None
    assert item["properties"]["start_datetime"] == "2024-06-01T00:00:00Z"

    coll = stac.build_collection(
        "subsidence-rates",
        spatial_bbox=stac.union_bbox([g.bbox]),
        temporal_interval=stac.union_interval([g]),
    )
    assert coll["id"] == "subsidence-rates"
    assert coll["extent"]["spatial"]["bbox"] == [[-95.55, 29.55, -95.35, 29.75]]
    assert coll["extent"]["temporal"]["interval"] == [["2024-06-01T00:00:00Z",
                                                       "2024-09-01T00:00:00Z"]]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
