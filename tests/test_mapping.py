"""Unit tests for the pure mapping core (no network / DB / CKAN needed).

Run: `python -m pytest tests/ -q`  (or `python tests/test_mapping.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacmap import assets as A
from stacmap import stac
from stacmap.manifest import granule_from_subside_manifest

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
