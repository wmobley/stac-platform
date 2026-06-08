"""Dual-write a granule to CKAN + the STAC API.

Single source of the publish logic, reused by the Tapis Workflows ``stac-publish``
FunctionTask (pip-installs this package) and any CLI / orchestrator caller.

Handles both SUBSIDE pipelines via :func:`stacmap.manifest.parse_manifest`:
  * **H2I** — one displacement COG + a PNG overlay.
  * **WERC** — two COGs (cumulative + velocity), each with its own value range.

CKAN first, STAC second: if the STAC write fails the granule still lives in CKAN
and the reconcile bridge picks it up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import assets as A
from . import stac
from .ckan import CkanClient
from .manifest import CogSpec, Granule, parse_manifest
from .stac_client import StacClient


def publish_item(
    *,
    collection_id: str,
    granule: Granule,
    cog_files: list[tuple[str, CogSpec]],
    overlay_path: str | None = None,
    manifest_path: str | None = None,
    ckan: CkanClient | None = None,
    stac_client: StacClient | None = None,
    collection_title: str | None = None,
    collection_description: str | None = None,
) -> dict:
    """Upload the given local files to CKAN, then build + upsert the STAC Item."""
    ckan = ckan or CkanClient()
    item_id = granule.item_id
    # One CKAN dataset per run (= one STAC Item), tagged with this collection so
    # the bridge can regroup runs into the (unchanged) STAC Collection.
    run_title = f"{collection_title or collection_id} — {item_id}"
    dataset = ckan.ensure_run_dataset(
        collection_id, item_id, title=run_title, notes=collection_description,
    )
    dataset_name = dataset["name"]

    item_assets: dict[str, dict] = {}
    for local_path, spec in cog_files:
        res = ckan.upload_resource(dataset_name, str(local_path), item_id=item_id)
        item_assets[spec.key] = A.make_asset(
            res["url"], title=spec.title or spec.filename,
            media_type=A.COG_MEDIA_TYPE, roles=["data", "visual"],
            display_range=spec.display_range,
            byte_size=res.get("size"), checksum=res.get("hash"),
        )
    if overlay_path:
        res = ckan.upload_resource(dataset_name, str(overlay_path), item_id=item_id)
        item_assets["overlay"] = A.make_asset(
            res["url"], title=os.path.basename(str(overlay_path)),
            media_type=A.PNG_MEDIA_TYPE, roles=["overlay", "visual"],
            byte_size=res.get("size"), checksum=res.get("hash"),
        )
    if manifest_path:
        res = ckan.upload_resource(dataset_name, str(manifest_path), item_id=item_id)
        item_assets["metadata"] = A.make_asset(
            res["url"], title="manifest", media_type=A.JSON_MEDIA_TYPE, roles=["metadata"],
            byte_size=res.get("size"), checksum=res.get("hash"),
        )
    # Source NetCDFs: link-only resources -> `source` link assets (back-populatable).
    for i, url in enumerate(granule.source_urls):
        res = ckan.link_resource(dataset_name, url, item_id=item_id)
        key = "source" if i == 0 else f"source_{i:04d}"
        item_assets[key] = A.make_asset(
            res.get("url", url), title=os.path.basename(url),
            media_type=A.NETCDF_MEDIA_TYPE, roles=["data", "source"],
            byte_size=res.get("size"), checksum=res.get("hash"),
        )

    item = stac.build_item(granule, collection_id, item_assets)
    stac_client = stac_client or StacClient()
    collection = stac.build_collection(
        collection_id, title=collection_title, description=collection_description,
        spatial_bbox=granule.bbox, temporal_interval=stac.union_interval([granule]),
    )
    stac_client.ensure_collection(collection)
    stac_client.upsert_item(collection_id, item)
    return item


def publish_from_dir(
    *,
    collection_id: str,
    manifest_path: str | Path,
    item_id: str,
    files_dir: str | Path,
    **kwargs,
) -> dict:
    """Parse a manifest, find its COG(s)/overlay in ``files_dir`` by name, publish.

    Used when the artifacts have been staged locally (the orchestrator fetches the
    tapis:// files into a temp dir; the hosted FunctionTask stages its inputs).
    """
    data = json.loads(Path(manifest_path).read_text())
    granule, cogs, overlay = parse_manifest(data, item_id)
    files_dir = Path(files_dir)
    cog_files = [(str(files_dir / s.filename), s) for s in cogs if (files_dir / s.filename).exists()]
    overlay_path = None
    if overlay and (files_dir / overlay).exists():
        overlay_path = str(files_dir / overlay)
    return publish_item(
        collection_id=collection_id, granule=granule, cog_files=cog_files,
        overlay_path=overlay_path, manifest_path=str(manifest_path), **kwargs,
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Dual-write a granule to CKAN + STAC.")
    p.add_argument("--collection", required=True, help="CKAN dataset = STAC Collection id")
    p.add_argument("--item-id", required=True, help="STAC Item id (stac_item_id on resources)")
    p.add_argument("--manifest", required=True, help="Path to the run manifest JSON (H2I or WERC)")
    p.add_argument("--files-dir", default=None,
                   help="Directory holding the COG(s)/overlay named as in the manifest "
                        "(default: the manifest's directory)")
    p.add_argument("--collection-title", default=os.environ.get("STAC_COLLECTION_TITLE"))
    p.add_argument("--collection-description", default=os.environ.get("STAC_COLLECTION_DESCRIPTION"))
    args = p.parse_args(argv)

    files_dir = args.files_dir or os.path.dirname(os.path.abspath(args.manifest))
    item = publish_from_dir(
        collection_id=args.collection, manifest_path=args.manifest, item_id=args.item_id,
        files_dir=files_dir, collection_title=args.collection_title,
        collection_description=args.collection_description,
    )
    print(f"published item {item['id']} -> collection {args.collection}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
