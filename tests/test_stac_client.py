from __future__ import annotations

import httpx

from stacmap.stac_client import StacClient


def test_stac_client_accepts_raw_or_prefixed_bearer_tokens():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return httpx.Response(404, json={"detail": "not found"})

    for token in ("header.payload.signature", "Bearer header.payload.signature"):
        client = StacClient(
            url="https://stac.example/api/v1",
            token=token,
            transport=httpx.MockTransport(handler),
        )
        try:
            assert client.collection_exists("missing") is False
        finally:
            client.close()

    assert seen == ["Bearer header.payload.signature", "Bearer header.payload.signature"]

