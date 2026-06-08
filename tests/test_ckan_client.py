from __future__ import annotations

import httpx

from stacmap.ckan import CkanClient


def test_jwt_token_uses_bearer_header_for_upload(tmp_path):
    token = "header.payload.signature"
    upload = tmp_path / "disp_displacement.tif"
    upload.write_bytes(b"dummy-cog")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == f"Bearer {token}"
        if request.url.path.endswith("/package_show"):
            return httpx.Response(200, json={"success": True, "result": {"resources": []}})
        if request.url.path.endswith("/resource_create"):
            body = request.content
            assert "multipart/form-data" in request.headers["content-type"]
            assert b'name="package_id"' in body
            assert b"subsidence-rates" in body
            assert b'name="stac_item_id"' in body
            assert b"subside-h2i-2024-06-01-2024-09-01" in body
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": {"id": "res-1", "url": "https://ckan.example/resource/res-1"},
                },
            )
        raise AssertionError(f"unexpected CKAN action: {request.url}")

    ckan = CkanClient(
        url="https://ckan.example",
        token=token,
        transport=httpx.MockTransport(handler),
    )
    try:
        result = ckan.upload_resource(
            "subsidence-rates",
            str(upload),
            item_id="subside-h2i-2024-06-01-2024-09-01",
        )
    finally:
        ckan.close()

    assert result["url"] == "https://ckan.example/resource/res-1"
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "package_show",
        "resource_create",
    ]


def test_ckan_api_key_uses_raw_authorization_header():
    assert CkanClient._headers("plain-ckan-key") == {"Authorization": "plain-ckan-key"}
    assert CkanClient._headers("Bearer already") == {"Authorization": "Bearer already"}
    assert CkanClient._headers("") == {}
