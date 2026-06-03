"""Thin ``ckanapi`` wrapper for the CKAN side of the platform.

CKAN is the asset store + human catalog. A CKAN *dataset* (package) is one STAC
Collection; its *resources* are grouped into STAC Items by a ``stac_item_id``
custom field. This module knows just enough CKAN to:

  * ensure a dataset exists (the Collection),
  * upload a file resource (COG/PNG/manifest) tagged with ``stac_item_id``,
  * register a *link* resource (the source NetCDF, not uploaded),
  * iterate a dataset's resources grouped by ``stac_item_id`` (for the bridge).

Resource custom fields: CKAN stores unknown keys passed to ``resource_create`` as
first-class resource attributes, so ``stac_item_id`` round-trips on read.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from typing import Any, Iterator

ITEM_ID_FIELD = "stac_item_id"


class CkanError(RuntimeError):
    pass


class CkanClient:
    def __init__(self, url: str | None = None, token: str | None = None, org: str | None = None):
        self.url = (url or os.environ.get("CKAN_URL", "")).rstrip("/")
        self.token = token or os.environ.get("CKAN_TOKEN")
        self.org = org or os.environ.get("CKAN_ORG")
        if not self.url:
            raise CkanError("CKAN_URL is not set")
        try:
            from ckanapi import RemoteCKAN
        except ImportError as exc:  # pragma: no cover
            raise CkanError("ckanapi is not installed (pip install ckanapi)") from exc
        self._ckan = RemoteCKAN(self.url, apikey=self.token)

    # --- datasets (= Collections) --------------------------------------------
    def get_dataset(self, name: str) -> dict | None:
        from ckanapi.errors import NotFound

        try:
            return self._ckan.action.package_show(id=name)
        except NotFound:
            return None

    def ensure_dataset(self, name: str, *, title: str | None = None,
                       notes: str | None = None) -> dict:
        """Return the dataset, creating it (private to ``self.org``) if absent."""
        existing = self.get_dataset(name)
        if existing:
            return existing
        if not self.org:
            raise CkanError("CKAN_ORG must be set to create a dataset")
        return self._ckan.action.package_create(
            name=name,
            title=title or name,
            notes=notes or "",
            owner_org=self.org,
        )

    # --- resources (= Assets) -------------------------------------------------
    # Upload/link are idempotent: a re-run patches the existing resource for the
    # same (name, stac_item_id) instead of creating a duplicate.
    def _existing_resource_id(self, dataset: str, name: str, item_id: str) -> str | None:
        pkg = self.get_dataset(dataset)
        if not pkg:
            return None
        for res in pkg.get("resources", []):
            if res.get("name") == name and res.get(ITEM_ID_FIELD) == item_id:
                return res["id"]
        return None

    def upload_resource(self, dataset: str, file_path: str, *, item_id: str,
                        name: str | None = None, fmt: str | None = None) -> dict:
        """Upload a local file as a resource tagged with ``stac_item_id`` (upsert)."""
        fname = name or os.path.basename(file_path)
        existing = self._existing_resource_id(dataset, fname, item_id)
        fields = dict(name=fname, format=fmt or _fmt_from_name(fname),
                      **{ITEM_ID_FIELD: item_id})
        with open(file_path, "rb") as fh:
            if existing:
                return self._ckan.action.resource_patch(id=existing, upload=fh, **fields)
            return self._ckan.action.resource_create(package_id=dataset, upload=fh, **fields)

    def link_resource(self, dataset: str, url: str, *, item_id: str,
                      name: str | None = None, fmt: str | None = None) -> dict:
        """Register a remote URL as a link-type resource, no upload (upsert)."""
        fname = name or url.rsplit("/", 1)[-1]
        existing = self._existing_resource_id(dataset, fname, item_id)
        fields = dict(name=fname, url=url, format=fmt or _fmt_from_name(fname),
                      **{ITEM_ID_FIELD: item_id})
        if existing:
            return self._ckan.action.resource_patch(id=existing, **fields)
        return self._ckan.action.resource_create(package_id=dataset, **fields)

    # --- read side (for the bridge) ------------------------------------------
    def iter_item_resources(self, dataset: str) -> Iterator[tuple[str, list[dict]]]:
        """Yield (stac_item_id, [resources]) for a dataset, grouped by item id.

        Resources lacking a ``stac_item_id`` are skipped (they aren't STAC items).
        Insertion order is preserved so output is deterministic.
        """
        pkg = self.get_dataset(dataset)
        if not pkg:
            return
        groups: "OrderedDict[str, list[dict]]" = OrderedDict()
        for res in pkg.get("resources", []):
            item_id = res.get(ITEM_ID_FIELD)
            if not item_id:
                continue
            groups.setdefault(item_id, []).append(res)
        for item_id, resources in groups.items():
            yield item_id, resources


def _fmt_from_name(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].upper() if "." in name else ""
    return {"TIF": "GeoTIFF", "TIFF": "GeoTIFF", "NC": "NetCDF"}.get(ext, ext)
