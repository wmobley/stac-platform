"""Unit tests for scripts/backfill_locations.py (mocked STAC API + geocoder --
no live network calls)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import httpx

from backfill_locations import _backfill_one
from stacmap.stac_client import StacClient

# Note: no `links` field here at all -- unlike the earlier PUT-based
# implementation, PATCH never touches it, so there's nothing to reset.
ITEM = {
    "type": "Feature",
    "stac_version": "1.0.0",
    "id": "subside-werc-2025-06-01-2025-09-01-b202e4b3-007",
    "collection": "subsidence-rates",
    "bbox": [-99.85, 29.67, -99.31, 30.32],
    "geometry": {"type": "Polygon", "coordinates": [[]]},
    "properties": {"start_datetime": "2025-06-01T00:00:00Z", "end_datetime": "2025-09-01T00:00:00Z"},
    "assets": {},
}


def _client_with_item(item, capture_patches):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=item)
        if request.method == "PATCH":
            capture_patches.append(json.loads(request.content))
            return httpx.Response(200, json=item)
        return httpx.Response(404)

    return StacClient(url="https://stacapi.example/api/v1", transport=httpx.MockTransport(handler))


def test_apply_sends_a_patch_with_only_the_location():
    patches = []
    client = _client_with_item(ITEM, patches)
    try:
        with patch("backfill_locations.resolve_location", return_value="New Braunfels, Texas"):
            outcome = _backfill_one(client, "subsidence-rates", ITEM["id"], apply=True)
    finally:
        client.close()

    assert outcome == "resolved"
    assert patches == [{"properties": {"subside:location": "New Braunfels, Texas"}}]


def test_dry_run_never_calls_patch():
    patches = []
    client = _client_with_item(ITEM, patches)
    try:
        with patch("backfill_locations.resolve_location", return_value="New Braunfels, Texas"):
            outcome = _backfill_one(client, "subsidence-rates", ITEM["id"], apply=False)
    finally:
        client.close()

    assert outcome == "resolved"
    assert patches == []


def test_already_has_location_is_skipped_without_geocoding():
    item = {**ITEM, "properties": {**ITEM["properties"], "subside:location": "Comal County, Texas"}}
    patches = []
    client = _client_with_item(item, patches)
    try:
        with patch("backfill_locations.resolve_location") as fake_resolve:
            outcome = _backfill_one(client, "subsidence-rates", item["id"], apply=True)
    finally:
        client.close()

    assert outcome == "already-had"
    assert patches == []
    fake_resolve.assert_not_called()


def test_no_bbox_is_skipped_without_geocoding():
    item = {**ITEM, "bbox": None}
    patches = []
    client = _client_with_item(item, patches)
    try:
        with patch("backfill_locations.resolve_location") as fake_resolve:
            outcome = _backfill_one(client, "subsidence-rates", item["id"], apply=True)
    finally:
        client.close()

    assert outcome == "no-bbox"
    assert patches == []
    fake_resolve.assert_not_called()


