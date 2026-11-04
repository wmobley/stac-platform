"""Standard-variable typing: CkanClient payloads, publish_item routing, and the
backfill script's decision/safety logic (all mocked; no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import httpx

import backfill_svo_fields as B
from stacmap.ckan import CkanClient
from stacmap.manifest import CogSpec, Granule
from stacmap.publish import publish_item


# --- CkanClient payloads -------------------------------------------------------
def _ckan_capturing():
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/package_show"):
            return httpx.Response(200, json={"success": True, "result": {"resources": []}})
        bodies.append(request.content)
        return httpx.Response(200, json={"success": True, "result": {"url": "u"}})

    return CkanClient(url="https://ckan.example", token="k",
                      transport=httpx.MockTransport(handler)), bodies


def test_upload_resource_carries_mint_standard_variables(tmp_path):
    f = tmp_path / "a.tif"
    f.write_bytes(b"x")
    ckan, bodies = _ckan_capturing()
    ckan.upload_resource("ds", str(f), item_id="i",
                         standard_variables=["a__b", "c__d"])
    assert b"a__b,c__d" in bodies[0] and b"mint_standard_variables" in bodies[0]


def test_upload_and_link_omit_field_when_untyped(tmp_path):
    f = tmp_path / "a.tif"
    f.write_bytes(b"x")
    ckan, bodies = _ckan_capturing()
    ckan.upload_resource("ds", str(f), item_id="i")
    ckan.upload_resource("ds", str(f), item_id="i", standard_variables=[])
    ckan.link_resource("ds", "https://x/y.nc", item_id="i")
    assert all(b"mint_standard_variables" not in b for b in bodies)


def test_link_resource_carries_field():
    ckan, bodies = _ckan_capturing()
    ckan.link_resource("ds", "https://x/y.nc", item_id="i", standard_variables=["a__b"])
    assert b"mint_standard_variables" in bodies[0] and b"a__b" in bodies[0]


# --- publish_item routing ------------------------------------------------------
class _Ckan:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def ensure_run_dataset(self, *a, **k):
        return {"name": "ds"}

    def upload_resource(self, dataset, path, *, item_id, **kw):
        self.calls.append(("upload", Path(path).name, kw))
        return {"url": "https://c/" + Path(path).name}

    def link_resource(self, dataset, url, *, item_id, **kw):
        self.calls.append(("link", url, kw))
        return {"url": url}


class _Stac:
    def ensure_collection(self, c): pass
    def upsert_item(self, cid, item): pass


def _publish(role_variables, tmp_path):
    granule = Granule(item_id="i", bbox=[0, 0, 1, 1], datetime="2024-01-01T00:00:00Z",
                      source_urls=["https://x/a.nc"])
    files = []
    for name, key in (("c.tif", "cumulative"), ("v.tif", "velocity")):
        (tmp_path / name).write_bytes(b"x")
        files.append((str(tmp_path / name), CogSpec(filename=name, key=key)))
    (tmp_path / "o.png").write_bytes(b"x")
    ckan = _Ckan()
    kwargs = {} if role_variables is None else {"role_variables": role_variables}
    publish_item(collection_id="c", granule=granule, cog_files=files,
                 overlay_path=str(tmp_path / "o.png"), ckan=ckan, stac_client=_Stac(), **kwargs)
    return {name: kw for _, name, kw in ckan.calls}


def test_publish_item_passes_matching_list_per_role(tmp_path):
    got = _publish({"cumulative": ["land_surface__displacement"],
                    "velocity": ["land_surface__displacement_rate"]}, tmp_path)
    assert got["c.tif"] == {"standard_variables": ["land_surface__displacement"]}
    assert got["v.tif"] == {"standard_variables": ["land_surface__displacement_rate"]}
    assert got["o.png"] == {} and got["https://x/a.nc"] == {}


def test_publish_item_default_passes_nothing(tmp_path):
    assert all(kw == {} for kw in _publish(None, tmp_path).values())


# --- backfill decision logic ---------------------------------------------------
def _res(**kw):
    base = {"id": "r1", "name": "n", "format": "TIFF", "url": "u", "url_type": "upload",
            "size": 5, "stac_item_id": "i", "subside_asset_role": "data",
            "subside_asset_key": "cog", "subside_pipeline": "p"}
    base.update(kw)
    return base


def test_classify_maps_key_to_variable_and_skips():
    rv = B.DEFAULT_ROLE_VARIABLES
    assert B.classify_resource(_res(), rv) == ("set", "land_surface__displacement")
    assert B.classify_resource(_res(subside_asset_key="velocity"), rv) == (
        "set", "land_surface__displacement_rate")
    assert B.classify_resource(_res(mint_standard_variables="land_surface__displacement"), rv)[0] == "skip-present"
    assert B.classify_resource(_res(mint_standard_variables="stale"), rv)[0] == "set"
    assert B.classify_resource(_res(subside_asset_key=None, subside_asset_role=None), rv)[0] == "skip-untyped"
    assert B.classify_resource(_res(format="PNG"), rv)[0] == "skip-nontif"


class _Mock:
    """Mock CkanClient: records actions; resource_show returns `after`."""

    def __init__(self, after=None):
        self.actions: list[tuple[str, dict]] = []
        self.after = after

    def _action_json(self, name, payload):
        self.actions.append((name, payload))
        if name == "resource_show":
            # A list of answers is consumed in order (pre-check, then read-back).
            if isinstance(self.after, list):
                return self.after.pop(0) if len(self.after) > 1 else self.after[0]
            return self.after
        return {}


def test_patch_payload_is_minimal_and_verified():
    pre = _res()
    m = _Mock(after={**pre, "mint_standard_variables": "v"})
    B.patch_and_verify(m, pre, "v")
    assert m.actions[0] == ("resource_patch", {"id": "r1", "mint_standard_variables": "v"})
    assert m.actions[1][0] == "resource_show"


@pytest.mark.parametrize("field", ["url", "url_type", "format", "size", "stac_item_id", "subside_asset_role"])
def test_readback_mismatch_aborts(field):
    pre = _res()
    m = _Mock(after={**pre, "mint_standard_variables": "v", field: "CHANGED"})
    with pytest.raises(B.VerificationError):
        B.patch_and_verify(m, pre, "v")


def test_readback_missing_new_field_aborts():
    pre = _res()
    with pytest.raises(B.VerificationError):
        B.patch_and_verify(_Mock(after=dict(pre)), pre, "v")


def test_rollback_manifest_holds_full_preimage_for_set_rows_only(tmp_path):
    a, b = _res(id="a"), _res(id="b", mint_standard_variables="land_surface__displacement")
    rows = [{"dataset": "d", "resource": a, "action": "set", "value": "v"},
            {"dataset": "d", "resource": b, "action": "skip-present", "value": "x"}]
    out = tmp_path / "rb.json"
    B.write_rollback_manifest(out, rows)
    data = json.loads(out.read_text())
    assert [e["pre_image"] for e in data["resources"]] == [a]


def test_apply_writes_only_set_rows():
    pre = _res()
    m = _Mock(after={**pre, "mint_standard_variables": "v"})
    rows = [{"dataset": "d", "resource": pre, "action": "set", "value": "v"},
            {"dataset": "d", "resource": _res(id="z"), "action": "skip-present", "value": "v"}]
    assert B.apply_plan(m, rows) == 1
    assert [a[0] for a in m.actions] == ["resource_patch", "resource_show"]


def test_rollback_restores_or_clears(tmp_path):
    pre = _res()  # had no field -> cleared
    p = tmp_path / "rb.json"
    p.write_text(json.dumps({"resources": [{"dataset": "d", "pre_image": pre, "new_value": "v"}]}))
    # pre-check sees our write still in place; read-back after the patch sees it cleared
    m = _Mock(after=[{**pre, "mint_standard_variables": "v"}, dict(pre)])
    assert B.rollback(m, p) == 1
    assert m.actions[0][0] == "resource_show"  # current value is checked first
    assert m.actions[1] == ("resource_patch", {"id": "r1", "mint_standard_variables": ""})


def test_rollback_keeps_a_newer_value(tmp_path):
    pre = _res()
    p = tmp_path / "rb.json"
    p.write_text(json.dumps({"resources": [{"dataset": "d", "pre_image": pre, "new_value": "v"}]}))
    m = _Mock(after={**pre, "mint_standard_variables": "later-publish"})
    assert B.rollback(m, p) == 0
    assert [a[0] for a in m.actions] == ["resource_show"]


def test_verify_tolerates_size_type_flip():
    pre = {**_res(), "size": 123}
    m = _Mock(after={**pre, "size": "123", "mint_standard_variables": "v"})
    B.patch_and_verify(m, pre, "v")  # must not raise
