"""Unit tests for stacmap.geocode (mocked Nominatim -- no live network calls)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from stacmap.geocode import resolve_location
from stacmap.manifest import parse_manifest

# A ~65km bbox around New Braunfels/Comal County, TX (typical SUBSIDE run size --
# see the design spec's real-world examples).
NB_BBOX = [-98.35, 29.55, -97.85, 30.05]


def _mock_transport(address_by_point: dict[tuple[float, float], dict]) -> httpx.MockTransport:
    """Maps each sampled (lat, lon), rounded to 2dp, to a Nominatim `address` dict."""

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        key = (round(float(params["lat"]), 2), round(float(params["lon"]), 2))
        address = address_by_point.get(key)
        if address is None:
            return httpx.Response(404, json={"error": "no result"})
        return httpx.Response(200, json={"address": address})

    return httpx.MockTransport(handler)


def test_unanimous_agreement_at_city_tier():
    # All 3 sample points agree on city -> city name, regardless of bbox size.
    bbox = [-98.15, 29.68, -97.95, 29.88]  # ~22km
    centroid = (round((29.68 + 29.88) / 2, 2), round((-98.15 + -97.95) / 2, 2))
    sw = (29.68, -98.15)
    ne = (29.88, -97.95)
    address = {"city": "New Braunfels", "county": "Comal County", "state": "Texas"}
    transport = _mock_transport({centroid: address, sw: address, ne: address})
    assert resolve_location(bbox, transport=transport, request_delay_s=0) == "New Braunfels, Texas"


def test_majority_at_city_tier_despite_larger_bbox():
    # ~65km bbox (typical SUBSIDE size): 2 of 3 points agree on city -- a real
    # run regularly clips a neighboring city/county at one corner. Majority
    # (>=2 of 3) is enough to accept the city tier; this MUST NOT escalate to
    # county/state just because one corner disagrees (the bug live testing
    # caught: every real item was resolving all the way to "Texas").
    centroid = (round((29.55 + 30.05) / 2, 2), round((-98.35 + -97.85) / 2, 2))
    sw = (29.55, -98.35)
    ne = (30.05, -97.85)
    transport = _mock_transport({
        centroid: {"city": "New Braunfels", "county": "Comal County", "state": "Texas"},
        sw: {"city": "New Braunfels", "county": "Comal County", "state": "Texas"},
        ne: {"city": "San Marcos", "county": "Hays County", "state": "Texas"},
    })
    assert resolve_location(NB_BBOX, transport=transport, request_delay_s=0) == "New Braunfels, Texas"


def test_falls_to_county_when_city_has_no_majority():
    # All 3 points disagree on city (no majority anywhere) but 2 of 3 agree on
    # county -> county name.
    centroid = (round((29.55 + 30.05) / 2, 2), round((-98.35 + -97.85) / 2, 2))
    sw = (29.55, -98.35)
    ne = (30.05, -97.85)
    transport = _mock_transport({
        centroid: {"city": "New Braunfels", "county": "Comal County", "state": "Texas"},
        sw: {"city": "San Marcos", "county": "Comal County", "state": "Texas"},
        ne: {"city": "Seguin", "county": "Guadalupe County", "state": "Texas"},
    })
    assert resolve_location(NB_BBOX, transport=transport, request_delay_s=0) == "Comal County, Texas"


def test_no_majority_anywhere_falls_back_to_centroid():
    # No tier -- not even state -- reaches a majority (a synthetic worst case;
    # SUBSIDE runs don't cross state lines, but the algorithm must still
    # degrade gracefully). Rather than giving up, use the centroid's own best
    # available field.
    centroid = (round((29.55 + 30.05) / 2, 2), round((-98.35 + -97.85) / 2, 2))
    sw = (29.55, -98.35)
    ne = (30.05, -97.85)
    transport = _mock_transport({
        centroid: {"city": "New Braunfels", "county": "Comal County", "state": "Texas"},
        sw: {"city": "San Marcos", "county": "Hays County", "state": "Oklahoma"},
        ne: {"city": "Luling", "county": "Caldwell County", "state": "Louisiana"},
    })
    assert resolve_location(NB_BBOX, transport=transport, request_delay_s=0) == "New Braunfels, Texas"


def test_three_different_counties_same_state_falls_back_to_centroid_not_state():
    # Regression test for a real bug caught in live testing: 3 points in
    # genuinely different counties (all within Texas -- Hill Country runs are
    # large enough to span Kerr/Bandera/Gillespie) must NOT settle for
    # majority-agreed "Texas". State is deliberately excluded from majority
    # consideration so this falls through to the centroid's own more specific
    # answer instead of the least useful possible label.
    centroid = (round((29.684677093365284 + 30.327536837573625) / 2, 2), round((-99.33350645938997 + -98.89412972058209) / 2, 2))
    sw = (29.684677093365284, -99.33350645938997)
    ne = (30.327536837573625, -98.89412972058209)
    transport = _mock_transport({
        (round(centroid[0], 2), round(centroid[1], 2)): {"hamlet": "Guadalupe Heights", "county": "Kerr County", "state": "Texas"},
        (round(sw[0], 2), round(sw[1], 2)): {"county": "Bandera County", "state": "Texas"},
        (round(ne[0], 2), round(ne[1], 2)): {"county": "Gillespie County", "state": "Texas"},
    })
    bbox = [-99.33350645938997, 29.684677093365284, -98.89412972058209, 30.327536837573625]
    assert resolve_location(bbox, transport=transport, request_delay_s=0) == "Guadalupe Heights, Texas"


def test_all_sample_points_fail_returns_none():
    transport = _mock_transport({})  # every point 404s
    assert resolve_location(NB_BBOX, transport=transport, request_delay_s=0) is None


def test_two_of_three_points_fail_still_resolves_from_centroid():
    # Centroid resolves even though both corners 404 -- majority-of-1 is still
    # a majority, so this returns a name instead of None.
    centroid = (round((29.55 + 30.05) / 2, 2), round((-98.35 + -97.85) / 2, 2))
    transport = _mock_transport({
        centroid: {"city": "New Braunfels", "county": "Comal County", "state": "Texas"},
    })
    assert resolve_location(NB_BBOX, transport=transport, request_delay_s=0) == "New Braunfels, Texas"


def test_invalid_bbox_returns_none_without_network():
    assert resolve_location(None) is None
    assert resolve_location([1, 2, 3]) is None


def test_parse_manifest_defaults_to_no_geocoding():
    """Opt-in contract: without resolve_location, parse_manifest never touches
    the network and never sets subside:location -- this is what keeps
    test_mapping.py's pure-unit-test invariant intact."""
    manifest = {
        "bbox": {"lon_min": -95.55, "lat_min": 29.55, "lon_max": -95.35, "lat_max": 29.75},
        "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
        "frame_ids": [8882],
    }
    g, _, _ = parse_manifest(manifest, "job-123")
    assert "subside:location" not in g.properties


def test_parse_manifest_calls_resolve_location_with_bbox():
    manifest = {
        "bbox": {"lon_min": -95.55, "lat_min": 29.55, "lon_max": -95.35, "lat_max": 29.75},
        "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
        "frame_ids": [8882],
    }
    calls = []

    def fake_resolve(bbox):
        calls.append(bbox)
        return "Houston, Texas"

    g, _, _ = parse_manifest(manifest, "job-123", resolve_location=fake_resolve)
    assert calls == [[-95.55, 29.55, -95.35, 29.75]]
    assert g.properties["subside:location"] == "Houston, Texas"


def test_parse_manifest_swallows_resolve_location_exceptions():
    manifest = {
        "bbox": {"lon_min": -95.55, "lat_min": 29.55, "lon_max": -95.35, "lat_max": 29.75},
        "config": {"start_date": "2024-06-01", "end_date": "2024-09-01"},
        "frame_ids": [8882],
    }

    def broken_resolve(bbox):
        raise RuntimeError("Nominatim exploded")

    g, _, _ = parse_manifest(manifest, "job-123", resolve_location=broken_resolve)
    assert "subside:location" not in g.properties


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
