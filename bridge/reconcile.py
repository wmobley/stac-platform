"""Reconcile a collection's per-run CKAN datasets into a PgSTAC Collection.

A STAC Collection spans many per-run CKAN datasets joined by the ``stac_collection``
extra (see ``stacmap.ckan``). For one collection:
  1. find every per-run dataset tagged with it (CkanClient.iter_collection_items),
     each yielding (stac_item_id, [resources]),
  2. for each, fetch the manifest resource to recover bbox/dates/source links,
  3. build the STAC Item (assets = that run's resources, classified by name),
  4. upsert Collection (extent recomputed from members) + every Item,
  5. prune STAC Items whose CKAN per-run dataset has vanished.

This is the backstop for the dual-write publish task: it makes CKAN the source of
truth, backfills runs that predate the task, and repairs any half-published
granule (CKAN write succeeded, STAC write failed).
"""

from __future__ import annotations

import sys
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


def _fetch_json(url: str, headers: dict | None = None) -> dict:
    resp = httpx.get(url, timeout=60.0, follow_redirects=True, headers=headers or {})
    resp.raise_for_status()
    return resp.json()


def _resource_filename(res: dict) -> str:
    """Best filename for a CKAN resource. The publisher may set a human-readable
    ``name`` (e.g. "Run manifest JSON - …"), so fall back to the original filename
    it recorded, then the URL basename, before the display name."""
    return (
        res.get("subside_original_filename")
        or (res.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        or res.get("name")
        or ""
    )


def _is_json_resource(res: dict) -> bool:
    fn = _resource_filename(res).lower()
    fmt = (res.get("format") or "").lower()
    mt = (res.get("mimetype") or "").lower()
    return fn.endswith(".json") or fmt == "json" or "json" in mt


def _manifest_resource(resources: list[dict]) -> dict | None:
    """The group's manifest = the JSON resource (preferring a *manifest*).

    Detection is by real filename / format / mimetype (not the display name),
    so it works whether the resource was named by file or by human title.
    """
    jsons = [r for r in resources if _is_json_resource(r)]
    if not jsons:
        return None
    for r in jsons:
        blob = " ".join(str(r.get(k) or "") for k in
                        ("subside_original_filename", "name", "url")).lower()
        if "manifest" in blob:
            return r
    return jsons[0]


def _build_assets(granule: Granule, resources: list[dict]) -> dict[str, dict]:
    """Classify each CKAN resource into a STAC asset by its real file name."""
    out: dict[str, dict] = {}
    for res in resources:
        name = _resource_filename(res)
        href = res.get("url")
        if not href:
            continue
        key, asset = A.asset_for_resource(
            name, href, display_range=granule.display_range,
            byte_size=res.get("size"), checksum=res.get("hash"),
        )
        # Avoid clobbering when several resources share a key (e.g. many .nc links).
        if key in out:
            key = f"{key}_{len(out):04d}"
        out[key] = asset
    return out


def reconcile_collection(
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
        # CKAN at ckan.tacc is fronted by Tapis auth, so resource downloads (the
        # manifest JSON) need the same bearer token as the Action API calls.
        fetch_headers = CkanClient._headers(getattr(ckan, "token", None))

        for item_id, resources in ckan.iter_collection_items(collection_id):
            manifest_res = _manifest_resource(resources)
            if not manifest_res:
                names = [(r.get("name") or _resource_filename(r)) for r in resources]
                print(f"  skip {item_id}: no manifest JSON among {names}", file=sys.stderr)
                skipped += 1  # no manifest -> can't place it in space/time
                continue
            try:
                manifest = _fetch_json(manifest_res["url"], headers=fetch_headers)
                granule = granule_from_subside_manifest(manifest, item_id)
            except Exception as exc:
                print(f"  skip {item_id}: manifest fetch/parse failed "
                      f"({type(exc).__name__}: {str(exc)[:160]}) url={manifest_res.get('url')}",
                      file=sys.stderr)
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


# Back-compat alias: the public entrypoint was ``reconcile_dataset`` when a
# dataset == a Collection. It now reconciles a collection across per-run datasets.
reconcile_dataset = reconcile_collection
