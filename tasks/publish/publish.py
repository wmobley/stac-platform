"""Reusable dual-write publish task: CKAN + STAC, in one step.

At publish time we already hold the manifest (bbox/dates), the rendered artifacts
(COG, overlay), and — after upload — the public CKAN URLs. So we write the STAC
Item synchronously: no polling lag, the granule is searchable immediately.

Order: **CKAN first, STAC second.** If the STAC write fails, the granule still
lives in CKAN and the reconcile bridge will pick it up — CKAN stays the source of
truth.

Usage (args override the matching CKAN_*/STAC_* env vars)::

    python -m tasks.publish.publish \
        --collection subsidence-rates --item-id job-123 \
        --manifest run-manifest.json \
        --cog disp_displacement.tif --overlay disp_overlay.png

Everything but --collection/--item-id/--manifest is optional. Source NetCDF links
are taken from the manifest's ``product_urls`` and registered as CKAN link
resources (not uploaded).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from stacmap import assets as A
from stacmap import stac
from stacmap.ckan import CkanClient
from stacmap.manifest import Granule, load_granule
from stacmap.stac_client import StacClient


def publish_granule(
    *,
    collection_id: str,
    granule: Granule,
    manifest_path: str | Path,
    cog_path: str | Path | None = None,
    overlay_path: str | Path | None = None,
    extra_files: list[str | Path] | None = None,
    ckan: CkanClient | None = None,
    stac_client: StacClient | None = None,
    collection_title: str | None = None,
    collection_description: str | None = None,
) -> dict:
    """Upload artifacts to CKAN, then build + upsert the STAC Item. Returns the Item."""
    ckan = ckan or CkanClient()
    item_id = granule.item_id

    # 1. CKAN: ensure the dataset (= Collection) and upload/link the assets.
    ckan.ensure_dataset(
        collection_id, title=collection_title, notes=collection_description
    )
    item_assets: dict[str, dict] = {}

    def _upload(path: str | Path, *, display_range=None) -> None:
        res = ckan.upload_resource(collection_id, str(path), item_id=item_id)
        key, asset = A.asset_for_resource(
            os.path.basename(str(path)), res["url"], display_range=display_range
        )
        item_assets[key] = asset

    if cog_path:
        _upload(cog_path, display_range=granule.display_range)
    if overlay_path:
        _upload(overlay_path)
    # The manifest itself becomes the `metadata` asset and the bridge's source of truth.
    _upload(manifest_path)
    for extra in extra_files or []:
        _upload(extra)

    # Source NetCDFs: link-only resources -> `source` link assets (back-populatable).
    for i, url in enumerate(granule.source_urls):
        res = ckan.link_resource(collection_id, url, item_id=item_id)
        key = "source" if i == 0 else f"source_{i:04d}"
        item_assets[key] = A.make_asset(
            res.get("url", url),
            title=os.path.basename(url),
            media_type=A.NETCDF_MEDIA_TYPE,
            roles=["data", "source"],
        )

    # 2. STAC: ensure the Collection, then PUT the Item (idempotent upsert).
    item = stac.build_item(granule, collection_id, item_assets)
    stac_client = stac_client or StacClient()
    collection = stac.build_collection(
        collection_id,
        title=collection_title,
        description=collection_description,
        spatial_bbox=granule.bbox,
        temporal_interval=stac.union_interval([granule]),
    )
    stac_client.ensure_collection(collection)
    stac_client.upsert_item(collection_id, item)
    return item


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Dual-write a granule to CKAN + STAC.")
    p.add_argument("--collection", required=True, help="CKAN dataset = STAC Collection id")
    p.add_argument("--item-id", required=True, help="STAC Item id (stac_item_id on resources)")
    p.add_argument("--manifest", required=True, help="Path to the SUBSIDE run manifest JSON")
    p.add_argument("--cog", help="Path to the displacement COG (.tif)")
    p.add_argument("--overlay", help="Path to the PNG overlay")
    p.add_argument("--extra", action="append", default=[], help="Extra file(s) to upload as assets")
    p.add_argument("--collection-title", default=os.environ.get("STAC_COLLECTION_TITLE"))
    p.add_argument("--collection-description", default=os.environ.get("STAC_COLLECTION_DESCRIPTION"))
    args = p.parse_args(argv)

    granule = load_granule(args.manifest, args.item_id)
    item = publish_granule(
        collection_id=args.collection,
        granule=granule,
        manifest_path=args.manifest,
        cog_path=args.cog,
        overlay_path=args.overlay,
        extra_files=args.extra,
        collection_title=args.collection_title,
        collection_description=args.collection_description,
    )
    print(f"published item {item['id']} -> collection {args.collection}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
