from __future__ import annotations

import httpx

from stacmap.ckan import CkanClient, dataset_name


def test_dataset_name_is_deterministic_and_slugified():
    name = dataset_name("Subsidence Rates", "subside-h2i-2024-06-01")
    assert name == "subsidence-rates--subside-h2i-2024-06-01"
    # Stable across calls (so re-publishing upserts the same dataset).
    assert dataset_name("c", "i") == dataset_name("c", "i")
    # Over-long names are truncated + hashed to stay <= 100 chars and unique.
    long_item = "x" * 200
    a = dataset_name("col", long_item)
    assert len(a) <= 100
    assert a != dataset_name("col", long_item + "y")


def test_ensure_run_dataset_tags_collection_and_item_extras():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/package_show"):
            return httpx.Response(404, json={"success": False, "error": {"__type": "Not Found"}})
        if request.url.path.endswith("/package_create"):
            import json
            body = json.loads(request.content)
            assert body["name"] == "subsidence-rates--run-1"
            extras = {e["key"]: e["value"] for e in body["extras"]}
            assert extras == {"stac_collection": "subsidence-rates", "stac_item_id": "run-1"}
            return httpx.Response(200, json={"success": True, "result": {"name": body["name"]}})
        raise AssertionError(f"unexpected CKAN action: {request.url}")

    ckan = CkanClient(url="https://ckan.example", token="k", org="myorg",
                      transport=httpx.MockTransport(handler))
    try:
        pkg = ckan.ensure_run_dataset("subsidence-rates", "run-1")
    finally:
        ckan.close()
    assert pkg["name"] == "subsidence-rates--run-1"
    assert [r.url.path.rsplit("/", 1)[-1] for r in requests] == ["package_show", "package_create"]


def test_iter_collection_items_groups_per_run_datasets():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/package_search")
        return httpx.Response(200, json={"success": True, "result": {
            "count": 2,
            "results": [
                {"extras": [{"key": "stac_item_id", "value": "run-1"}],
                 "resources": [{"name": "a.tif", "url": "u1", "stac_item_id": "run-1"}]},
                {"extras": [{"key": "stac_item_id", "value": "run-2"}],
                 "resources": [{"name": "b.tif", "url": "u2", "stac_item_id": "run-2"}]},
            ],
        }})

    ckan = CkanClient(url="https://ckan.example", token="k",
                      transport=httpx.MockTransport(handler))
    try:
        items = list(ckan.iter_collection_items("subsidence-rates"))
    finally:
        ckan.close()
    assert [iid for iid, _ in items] == ["run-1", "run-2"]
    assert items[0][1][0]["name"] == "a.tif"


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


def test_ensure_dataset_scheming_type_and_fields():
    """A scheming dataset carries its `type`, owner_org override, and schema fields,
    and is NOT tagged with the bridge's stac_collection extra."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        if request.url.path.endswith("/package_show"):
            return httpx.Response(404, json={"success": False, "error": {"__type": "Not Found"}})
        if request.url.path.endswith("/package_create"):
            body = _json.loads(request.content)
            bodies.append(body)
            return httpx.Response(200, json={"success": True, "result": {"name": body["name"]}})
        raise AssertionError(f"unexpected CKAN action: {request.url}")

    ckan = CkanClient(url="https://ckan.example", token="k", org="fallback-org",
                      transport=httpx.MockTransport(handler))
    try:
        ckan.ensure_dataset(
            "my-external", dataset_type="subside_dataset", owner_org="twdb-subside",
            fields={"title": "T", "private": False, "temporal_coverage_start": "2001-02-01"},
        )
    finally:
        ckan.close()
    (body,) = bodies
    assert body["type"] == "subside_dataset"
    assert body["owner_org"] == "twdb-subside"          # overrides client org
    assert body["private"] is False
    assert body["temporal_coverage_start"] == "2001-02-01"
    assert "extras" not in body                          # un-bridged: no stac_collection


def test_link_resource_without_item_id_carries_scheming_fields():
    """A standalone external resource: no stac_item_id, object fields JSON-encoded."""
    import json as _json
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/package_show"):
            return httpx.Response(200, json={"success": True, "result": {"resources": []}})
        if request.url.path.endswith("/resource_create"):
            return httpx.Response(200, json={"success": True, "result": {"id": "r1", "url": "u"}})
        raise AssertionError(f"unexpected CKAN action: {request.url}")

    ckan = CkanClient(url="https://ckan.example", token="k",
                      transport=httpx.MockTransport(handler))
    orig = ckan._action_multipart
    ckan._action_multipart = lambda name, fields, **kw: (sent.append(fields), orig(name, fields, **kw))[1]
    try:
        ckan.link_resource(
            "my-external", "https://svc/FeatureServer/0", name="Layer 0", fmt="Esri REST",
            extra_fields={"collection_method": "Administrative Record",
                          "spatial": {"type": "Polygon", "coordinates": []}},
        )
    finally:
        ckan.close()
    (fields,) = sent
    assert "stac_item_id" not in fields                  # not a SUBSIDE run
    assert fields["collection_method"] == "Administrative Record"
    assert _json.loads(fields["spatial"])["type"] == "Polygon"  # object -> JSON string
