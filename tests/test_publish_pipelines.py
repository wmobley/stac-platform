"""Integration test: run the publish step for BOTH SUBSIDE pipelines end-to-end
against a real STAC API.

This exercises the `stac-publish` path (the same `stacmap.publish` code the
Workflows FunctionTask / orchestrator call) for each pipeline's manifest shape:

  * **H2I**  — one displacement COG + a PNG overlay
  * **WERC** — two COGs (cumulative + velocity), no overlay

CKAN is stubbed (no upload credentials needed) — we only verify the STAC side:
the Item is created with the right assets and is searchable. A *full* live run
(HPC download + analysis) is `python tapis/workflows/orchestrate.py --pipeline
{h2i,werc} --allocation <A>` with the publish env set; this script tests the
publish logic without that cost.

Prereqs: a running STAC API with writes open. Easiest:
    docker compose up -d           # STAC_AUTH_DISABLED=true, :8081
    STAC_URL=http://localhost:8081/api/v1 python tests/test_publish_pipelines.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from stacmap.publish import publish_from_dir

STAC_URL = os.environ.get("STAC_URL", "http://localhost:8081/api/v1").rstrip("/")
COLLECTION = os.environ.get("STAC_COLLECTION", "pipeline-test")


class StubCkan:
    """Stand-in for CkanClient: records uploads, returns fake public asset URLs."""

    def __init__(self, base="https://ckan.example/resource"):
        self.base = base
        self.uploaded: list[str] = []

    def ensure_run_dataset(self, collection_id, item_id, *, title=None, notes=None):
        return {"name": f"{collection_id}--{item_id}"}

    def upload_resource(self, dataset, file_path, *, item_id, name=None, fmt=None):
        fn = name or os.path.basename(str(file_path))
        self.uploaded.append(fn)
        return {"url": f"{self.base}/{item_id}/{fn}"}

    def link_resource(self, dataset, url, *, item_id, name=None, fmt=None):
        return {"url": url}


# --- sample manifests (trimmed real shapes) + the files they reference ---------
H2I = {
    "manifest_name": "run-manifest.json",
    "manifest": {
        "bbox": {"lon_min": -95.55, "lat_min": 29.55, "lon_max": -95.35, "lat_max": 29.75},
        "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
        "frame_ids": [8882],
        "product_urls": ["https://cumulus.asf.earthdatacloud.nasa.gov/x/OPERA_F08882.nc"],
        "artifacts": {
            "cog_tif": "/out/disp_displacement.tif",
            "overlay_png": "/out/disp_overlay.png",
            "display_range": {"vmin": -0.01, "vmax": 0.065},
        },
    },
    "files": ["disp_displacement.tif", "disp_overlay.png"],
    "item_id": "h2i-test-2024",
    "expect_assets": {"cog", "overlay", "metadata", "source"},
}
WERC = {
    "manifest_name": "werc-run-manifest.json",
    "manifest": {
        "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
        "frame_id": 8882,
        "artifacts": {
            "cumulative_displacement_geotiff": {
                "path": "/out/opera_disp_s1_cumulative.tif",
                "bounds": [-95.5551, 29.5500, -95.3455, 29.7500],
                "clip_range_mm": [-19.67, 21.66],
            },
            "velocity_geotiff": {
                "path": "/out/opera_disp_s1_velocity.tif",
                "bounds": [-95.5551, 29.5500, -95.3455, 29.7500],
                "p02_p98_mm_per_year": [-553.1, 594.1],
            },
        },
    },
    "files": ["opera_disp_s1_cumulative.tif", "opera_disp_s1_velocity.tif"],
    "item_id": "werc-test-2024",
    "expect_assets": {"cumulative", "velocity", "metadata"},
}


def _run_case(case) -> None:
    import json
    label = case["item_id"]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / case["manifest_name"]).write_text(json.dumps(case["manifest"]))
        for fn in case["files"]:
            (tmp / fn).write_bytes(b"\x49\x49\x2a\x00dummy-cog")  # not a real COG; CKAN is stubbed
        item = publish_from_dir(
            collection_id=COLLECTION,
            manifest_path=str(tmp / case["manifest_name"]),
            item_id=case["item_id"],
            files_dir=str(tmp),
            ckan=StubCkan(),
        )
    # Read the Item back from the STAC API to prove it was written + is searchable.
    got = httpx.get(f"{STAC_URL}/collections/{COLLECTION}/items/{case['item_id']}", timeout=30)
    got.raise_for_status()
    assets = set(got.json().get("assets", {}))
    missing = case["expect_assets"] - assets
    assert not missing, f"{label}: missing assets {missing} (got {assets})"
    # And that /search finds it by the collection.
    s = httpx.post(f"{STAC_URL}/search", json={"collections": [COLLECTION],
                   "ids": [case["item_id"]]}, timeout=30).json()
    assert any(f["id"] == case["item_id"] for f in s.get("features", [])), f"{label}: not in /search"
    print(f"ok  {label}: assets={sorted(assets)}")


def _cleanup(item_ids) -> None:
    token = os.environ.get("STAC_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for iid in item_ids:
        try:
            httpx.request("DELETE", f"{STAC_URL}/collections/{COLLECTION}/items/{iid}",
                          headers=headers, timeout=30)
        except Exception:
            pass


def main() -> int:
    print(f"STAC_URL = {STAC_URL}  collection = {COLLECTION}")
    try:
        for case in (H2I, WERC):
            _run_case(case)
    finally:
        _cleanup([H2I["item_id"], WERC["item_id"]])
    print("both pipelines published + verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
