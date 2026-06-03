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
from typing import Any

import httpx


class StacClient:
    def __init__(self, url: str | None = None, token: str | None = None, *, timeout: float = 60.0):
        self.url = (url or os.environ.get("STAC_URL", "")).rstrip("/")
        self.token = token or os.environ.get("STAC_TOKEN")
        if not self.url:
            raise RuntimeError("STAC_URL is not set")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.Client(base_url=self.url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StacClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def collection_exists(self, collection_id: str) -> bool:
        resp = self._client.get(f"/collections/{collection_id}")
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    def ensure_collection(self, collection: dict[str, Any]) -> None:
        """Create the collection if it does not already exist (idempotent)."""
        if self.collection_exists(collection["id"]):
            return
        resp = self._client.post("/collections", json=collection)
        # Tolerate a race where a concurrent publish created it first.
        if resp.status_code == 409:
            return
        resp.raise_for_status()

    def upsert_item(self, collection_id: str, item: dict[str, Any]) -> None:
        """PUT an item (create or replace). Falls back to POST if PUT is unsupported."""
        iid = item["id"]
        resp = self._client.put(f"/collections/{collection_id}/items/{iid}", json=item)
        if resp.status_code == 404:
            # Item does not exist yet and the server requires POST-to-create.
            resp = self._client.post(f"/collections/{collection_id}/items", json=item)
        resp.raise_for_status()
