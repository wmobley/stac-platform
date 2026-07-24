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


def test_get_item_returns_none_on_404_and_item_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/missing-item"):
            return httpx.Response(404, json={"detail": "not found"})
        return httpx.Response(200, json={"id": "present-item", "properties": {}})

    client = StacClient(
        url="https://stac.example/api/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.get_item("subsidence-rates", "missing-item") is None
        item = client.get_item("subsidence-rates", "present-item")
        assert item == {"id": "present-item", "properties": {}}
    finally:
        client.close()


def test_patch_item_sends_only_the_partial_body():
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append((request.method, json.loads(request.content)))
        return httpx.Response(200, json={"id": "some-item"})

    client = StacClient(
        url="https://stac.example/api/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client.patch_item("subsidence-rates", "some-item", {"properties": {"subside:location": "Comal County, Texas"}})
    finally:
        client.close()

    assert seen == [("PATCH", {"properties": {"subside:location": "Comal County, Texas"}})]


def test_patch_item_raises_on_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "links must be empty on write"})

    client = StacClient(
        url="https://stac.example/api/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        try:
            client.patch_item("subsidence-rates", "some-item", {"properties": {}})
            raised = False
        except httpx.HTTPStatusError:
            raised = True
        assert raised
    finally:
        client.close()

