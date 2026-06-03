"""Reconcile a CKAN dataset into a PgSTAC Collection.

For one dataset (= Collection):
  1. read its resources grouped by ``stac_item_id`` (CkanClient.iter_item_resources),
  2. for each group, fetch the manifest resource to recover bbox/dates/source links,
  3. build the STAC Item (assets = the group's resources, classified by name),
  4. upsert Collection (extent recomputed from members) + every Item,
  5. prune STAC Items whose CKAN ``stac_item_id`` group has vanished.

This is the backstop for the dual-write publish task: it makes CKAN the source of
truth, backfills datasets that predate the task, and repairs any half-published
granule (CKAN write succeeded, STAC write failed).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from stacmap import assets as A
from stacmap import stac
from stacmap.ckan import CkanClient
from stacmap.manifest import Granule, granule_from_subside_manifest

from .loader import PgstacWriter


@dataclass
class ReconcileResult:
    collection_id: str
    upserted: int
    pruned: int
    skipped: int


def _fetch_json(url: str) -> dict:
    resp = httpx.get(url, timeout=60.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def _manifest_resource(resources: list[dict]) -> dict | None:
    """The group's manifest = the .json resource (preferring a *-manifest.json)."""
    jsons = [r for r in resources if (r.get("name") or "").lower().endswith(".json")]
    if not jsons:
        return None
    for r in jsons:
        if "manifest" in (r.get("name") or "").lower():
            return r
    return jsons[0]


def _build_assets(granule: Granule, resources: list[dict]) -> dict[str, dict]:
    """Classify each CKAN resource into a STAC asset by file name."""
    out: dict[str, dict] = {}
    for res in resources:
        name = res.get("name") or ""
        href = res.get("url")
        if not href:
            continue
        key, asset = A.asset_for_resource(
            name, href, display_range=granule.display_range
        )
        # Avoid clobbering when several resources share a key (e.g. many .nc links).
        if key in out:
            key = f"{key}_{len(out):04d}"
        out[key] = asset
    return out


def reconcile_dataset(
    collection_id: str,
    *,
    ckan: CkanClient | None = None,
    writer: PgstacWriter | None = None,
    collection_title: str | None = None,
    collection_description: str | None = None,
    prune: bool = True,
) -> ReconcileResult:
    ckan = ckan or CkanClient()
    own_writer = writer is None
    writer = writer or PgstacWriter()
    try:
        granules: list[Granule] = []
        items: list[dict] = []
        skipped = 0

        for item_id, resources in ckan.iter_item_resources(collection_id):
            manifest_res = _manifest_resource(resources)
            if not manifest_res:
                skipped += 1  # no manifest -> can't place it in space/time
                continue
            try:
                manifest = _fetch_json(manifest_res["url"])
                granule = granule_from_subside_manifest(manifest, item_id)
            except Exception:
                skipped += 1
                continue
            assets = _build_assets(granule, resources)
            items.append(stac.build_item(granule, collection_id, assets))
            granules.append(granule)

        collection = stac.build_collection(
            collection_id,
            title=collection_title,
            description=collection_description,
            spatial_bbox=stac.union_bbox([g.bbox for g in granules]),
            temporal_interval=stac.union_interval(granules),
        )
        writer.upsert_collection(collection)
        for item in items:
            writer.upsert_item(item)

        pruned = 0
        if prune:
            built_ids = {it["id"] for it in items}
            for stale in writer.existing_item_ids(collection_id) - built_ids:
                writer.delete_item(stale, collection_id)
                pruned += 1

        return ReconcileResult(collection_id, len(items), pruned, skipped)
    finally:
        if own_writer:
            writer.close()
