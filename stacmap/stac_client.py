"""STAC Transactions HTTP client used by the publish task.

The task writes Items over HTTP (rather than touching PgSTAC directly) so it needs
no database credentials — only the API base URL and a write token. Uses the STAC
API *Transactions* extension:

    PUT  /collections/{cid}/items/{iid}     upsert an item (idempotent)
    POST /collections                       create a collection
    GET  /collections/{cid}                 existence check
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

# Transient server-side failures — gateway/proxy hiccups (502/503/504), pgstac
# cold starts or DB blips (500), rate limits (429) — should not abort a publish.
# A single such response mid-run otherwise strands the granule in CKAN with no
# STAC Item. Retry these (and transport errors) with backoff before giving up;
# 4xx (e.g. 404/409) are deterministic and pass straight through.
_RETRY_STATUS = frozenset({500, 502, 503, 504, 429})
_RETRY_DELAYS = (1.0, 2.0, 5.0, 10.0, 20.0)


class StacClient:
    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        retry_delays: tuple[float, ...] = _RETRY_DELAYS,
    ):
        self.url = (url or os.environ.get("STAC_URL", "")).rstrip("/")
        self.token = token or os.environ.get("STAC_TOKEN")
        if not self.url:
            raise RuntimeError("STAC_URL is not set")
        headers = {"Content-Type": "application/json"}
        if self.token:
            token = self.token.strip()
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        self._retry_delays = retry_delays
        self._client = httpx.Client(
            base_url=self.url,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue a request, retrying transient 5xx/429 and transport errors with backoff."""
        attempts = len(self._retry_delays) + 1
        for i in range(attempts):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if i == attempts - 1:
                    raise
                print(f"stac: {type(exc).__name__} on {method} {path}; retry in {self._retry_delays[i]}s")
                time.sleep(self._retry_delays[i])
                continue
            if resp.status_code in _RETRY_STATUS and i < attempts - 1:
                print(f"stac: HTTP {resp.status_code} on {method} {path}; retry in {self._retry_delays[i]}s")
                time.sleep(self._retry_delays[i])
                continue
            return resp
        return resp  # exhausted retries: return the last (still-failing) response

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StacClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def collection_exists(self, collection_id: str) -> bool:
        resp = self._send("GET", f"/collections/{collection_id}")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    def ensure_collection(self, collection: dict[str, Any]) -> None:
        """Create the collection if it does not already exist (idempotent)."""
        if self.collection_exists(collection["id"]):
            return
        resp = self._send("POST", "/collections", json=collection)
        # Tolerate a race where a concurrent publish created it first.
        if resp.status_code == 409:
            return
        resp.raise_for_status()

    def get_item(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        """Fetch a single item, or None if it (or the collection) doesn't exist."""
        resp = self._send("GET", f"/collections/{collection_id}/items/{item_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def upsert_item(self, collection_id: str, item: dict[str, Any]) -> None:
        """PUT an item (create or replace). Falls back to POST if PUT is unsupported."""
        iid = item["id"]
        resp = self._send("PUT", f"/collections/{collection_id}/items/{iid}", json=item)
        if resp.status_code == 404:
            # Item does not exist yet and the server requires POST-to-create.
            resp = self._send("POST", f"/collections/{collection_id}/items", json=item)
        resp.raise_for_status()

    def patch_item(self, collection_id: str, item_id: str, partial: dict[str, Any]) -> None:
        """PATCH a partial update onto an EXISTING item (JSON Merge Patch --
        only send the fields you want to change, e.g.
        ``{"properties": {"subside:location": "..."}}``). Prefer this over
        get-mutate-`upsert_item` for small edits: PUT expects a complete,
        clean Item, and re-submitting a GET response verbatim resends
        server-managed fields (e.g. pgstac-injected self/parent/root/collection
        `links`) that the API rejects with 400 on write."""
        resp = self._send("PATCH", f"/collections/{collection_id}/items/{item_id}", json=partial)
        resp.raise_for_status()

    def list_item_ids(self, collection_id: str, *, limit: int = 1000) -> list[str]:
        """Return the ids of the items currently in a collection (empty if absent)."""
        resp = self._send("GET", f"/collections/{collection_id}/items", params={"limit": limit})
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return [f["id"] for f in resp.json().get("features", []) if f.get("id")]

    def delete_item(self, collection_id: str, item_id: str) -> bool:
        """Delete an item. Returns False (without raising) when the server doesn't
        support deletes or the item is already gone, so callers can prune best-effort."""
        resp = self._send("DELETE", f"/collections/{collection_id}/items/{item_id}")
        if resp.status_code in (404, 405, 501):
            return False
        resp.raise_for_status()
        return True
