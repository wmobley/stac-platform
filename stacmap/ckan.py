"""Thin CKAN Action API client for the CKAN side of the platform.

CKAN is the asset store + human catalog. To keep the human catalog browsable, a
CKAN *dataset* (package) is **one run** = one STAC Item: it holds just that run's
handful of resources (COG/overlay/manifest/source). The STAC Collection is *not*
a CKAN dataset — it is a logical grouping that spans many per-run datasets, joined
by a ``stac_collection`` package extra. (Previously every run dumped its resources
into one shared dataset, which made the CKAN dataset page an unbrowsable wall.)

This module knows just enough CKAN to:

  * ensure a per-run dataset exists, tagged with ``stac_collection`` + ``stac_item_id``,
  * upload a file resource (COG/PNG/manifest) tagged with ``stac_item_id``,
  * register a *link* resource (the source NetCDF, not uploaded),
  * find every per-run dataset in a collection and yield its (item_id, resources)
    for the bridge.

Custom fields: CKAN stores unknown keys passed to ``resource_create`` as
first-class resource attributes (so ``stac_item_id`` round-trips on a resource),
and package-level ``extras`` round-trip on the dataset (``stac_collection`` /
``stac_item_id``) and are searchable via ``fq``.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import OrderedDict
from typing import Any, Iterator

import httpx

ITEM_ID_FIELD = "stac_item_id"
COLLECTION_FIELD = "stac_collection"

# CKAN dataset names: lowercase, 2-100 chars, only [a-z0-9_-].
_NAME_MAX = 100


def dataset_name(collection_id: str, item_id: str) -> str:
    """Deterministic per-run CKAN dataset name: slug of ``{collection}--{item}``.

    Stable for a given (collection, item) so re-publishing upserts the same
    dataset. If the slug exceeds CKAN's 100-char limit it is truncated and a hash
    of the full string appended to keep it unique.
    """
    raw = f"{collection_id}--{item_id}".lower()
    slug = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-")
    if len(slug) > _NAME_MAX:
        digest = hashlib.sha1(raw.encode()).hexdigest()[:8]
        slug = f"{slug[: _NAME_MAX - 9].rstrip('-')}-{digest}"
    return slug


def _extra(pkg: dict, key: str) -> str | None:
    """Read a package extra by key from a CKAN package dict."""
    for entry in pkg.get("extras") or []:
        if entry.get("key") == key:
            return entry.get("value")
    return None


class CkanError(RuntimeError):
    pass


class CkanClient:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        org: str | None = None,
        *,
        timeout: float = 300.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = (url or os.environ.get("CKAN_URL", "")).rstrip("/")
        self.token = token or os.environ.get("CKAN_TOKEN")
        self.org = org or os.environ.get("CKAN_ORG")
        if not self.url:
            raise CkanError("CKAN_URL is not set")
        self._client = httpx.Client(
            base_url=self.url,
            headers=self._headers(self.token),
            timeout=timeout,
            transport=transport,
        )

    @staticmethod
    def _headers(token: str | None) -> dict[str, str]:
        if not token:
            return {}
        token = token.strip()
        if not token:
            return {}
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        # ckan.tacc.utexas.edu is fronted by Tapis auth; JWTs must be sent as
        # bearer tokens. Plain CKAN API keys continue to use CKAN's raw header.
        if token.count(".") == 2:
            return {"Authorization": f"Bearer {token}"}
        return {"Authorization": token}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CkanClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _action_json(self, name: str, payload: dict[str, Any]) -> dict:
        resp = self._client.post(f"/api/3/action/{name}", json=payload)
        return self._result(name, resp)

    def _action_multipart(
        self,
        name: str,
        fields: dict[str, Any],
        *,
        file_path: str | None = None,
    ) -> dict:
        data = {key: str(value) for key, value in fields.items() if value is not None}
        if file_path is None:
            resp = self._client.post(f"/api/3/action/{name}", data=data)
            return self._result(name, resp)
        filename = os.path.basename(file_path)
        with open(file_path, "rb") as fh:
            files = {"upload": (filename, fh, "application/octet-stream")}
            resp = self._client.post(f"/api/3/action/{name}", data=data, files=files)
        try:
            return self._result(name, resp)
        except CkanError as exc:
            size = os.path.getsize(file_path)
            raise CkanError(f"{exc}; upload={filename} size={size} bytes") from exc

    def _result(self, action: str, resp: httpx.Response) -> dict:
        text = resp.text[:1000]
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CkanError(f"CKAN {action} returned HTTP {resp.status_code}: {text}") from exc
        if resp.status_code >= 400 or not payload.get("success"):
            raise CkanError(f"CKAN {action} failed HTTP {resp.status_code}: {payload}")
        return payload["result"]

    # --- datasets (= one run = one STAC Item) ---------------------------------
    def get_dataset(self, name: str) -> dict | None:
        resp = self._client.post("/api/3/action/package_show", json={"id": name})
        if resp.status_code == 404:
            return None
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CkanError(f"CKAN package_show returned HTTP {resp.status_code}: {resp.text[:1000]}") from exc
        if not payload.get("success"):
            error = payload.get("error") or {}
            if error.get("__type") == "Not Found" or resp.status_code == 404:
                return None
            raise CkanError(f"CKAN package_show failed HTTP {resp.status_code}: {payload}")
        return payload["result"]

    def ensure_run_dataset(self, collection_id: str, item_id: str, *,
                           title: str | None = None,
                           notes: str | None = None) -> dict:
        """Return the per-run dataset, creating it (private to ``self.org``) if absent.

        The dataset is named deterministically from ``(collection_id, item_id)`` and
        carries ``stac_collection`` + ``stac_item_id`` extras so the bridge can find
        every run in a collection and recover its STAC Item id.
        """
        name = dataset_name(collection_id, item_id)
        existing = self.get_dataset(name)
        if existing:
            return existing
        if not self.org:
            raise CkanError("CKAN_ORG must be set to create a dataset")
        return self._action_json("package_create", {
            "name": name,
            "title": title or item_id,
            "notes": notes or "",
            "owner_org": self.org,
            "extras": [
                {"key": COLLECTION_FIELD, "value": collection_id},
                {"key": ITEM_ID_FIELD, "value": item_id},
            ],
        })

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
        if existing:
            return self._action_multipart("resource_patch", {"id": existing, **fields}, file_path=file_path)
        return self._action_multipart(
            "resource_create", {"package_id": dataset, **fields}, file_path=file_path
        )

    def link_resource(self, dataset: str, url: str, *, item_id: str,
                      name: str | None = None, fmt: str | None = None) -> dict:
        """Register a remote URL as a link-type resource, no upload (upsert)."""
        fname = name or url.rsplit("/", 1)[-1]
        existing = self._existing_resource_id(dataset, fname, item_id)
        fields = dict(name=fname, url=url, format=fmt or _fmt_from_name(fname),
                      **{ITEM_ID_FIELD: item_id})
        if existing:
            return self._action_multipart("resource_patch", {"id": existing, **fields})
        return self._action_multipart("resource_create", {"package_id": dataset, **fields})

    # --- read side (for the bridge) ------------------------------------------
    def find_collection_datasets(self, collection_id: str) -> Iterator[dict]:
        """Yield every per-run dataset tagged with this ``stac_collection``.

        Paginates ``package_search`` on the extra; results include resources.
        """
        start, rows = 0, 100
        while True:
            result = self._action_json("package_search", {
                "fq": f'{COLLECTION_FIELD}:"{collection_id}"',
                "rows": rows,
                "start": start,
                "include_private": True,
            })
            results = result.get("results", [])
            for pkg in results:
                yield pkg
            start += len(results)
            if not results or start >= result.get("count", 0):
                return

    def iter_collection_items(self, collection_id: str) -> Iterator[tuple[str, list[dict]]]:
        """Yield (stac_item_id, [resources]) for every run in a collection.

        Each per-run dataset is one STAC Item; its item id comes from the dataset's
        ``stac_item_id`` extra (falling back to a resource's ``stac_item_id``).
        Datasets with no resolvable item id or no resources are skipped.
        """
        for pkg in self.find_collection_datasets(collection_id):
            resources = pkg.get("resources", [])
            if not resources:
                continue
            item_id = _extra(pkg, ITEM_ID_FIELD)
            if not item_id:
                item_id = next((r.get(ITEM_ID_FIELD) for r in resources
                                if r.get(ITEM_ID_FIELD)), None)
            if not item_id:
                continue
            yield item_id, resources

    def iter_item_resources(self, dataset: str) -> Iterator[tuple[str, list[dict]]]:
        """Yield (stac_item_id, [resources]) for a single dataset, grouped by item id.

        Resources lacking a ``stac_item_id`` are skipped (they aren't STAC items).
        Insertion order is preserved so output is deterministic. Retained for
        single-dataset inspection; the bridge uses :meth:`iter_collection_items`.
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
