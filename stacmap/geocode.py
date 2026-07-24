"""Bbox -> human-readable location name, via OSM Nominatim reverse geocoding.

Used once per run at **publish time** (never at UI render time — see
`subside/docs/design/2026-07-24-run-location-labels.md` for why: the Risk
Explorer map re-queries STAC on every pan/zoom, so a live geocode call there
would violate Nominatim's ~1 req/sec usage policy). The resolved name is
stored as the `subside:location` STAC property and read verbatim by the UI.

Design notes (see the design spec's Decisions section for the full history --
this went through two live-tested revisions before landing here):

* v1 required unanimous 3-point agreement starting at a size-scaled tier.
  Live testing against the real `subsidence-rates` collection showed EVERY
  item resolving to just "Texas": SUBSIDE bboxes (~65-70km) routinely span
  more than one Texas county (often only 30-50km across), so unanimous
  agreement failed almost always.
* v2 switched to majority agreement (>=2 of 3 resolved points) across city ->
  county -> state tiers, with a centroid-only fallback if nothing reached a
  majority anywhere. This still produced "Texas" for genuinely large rural
  runs spanning 3+ counties -- because **state-tier majority is nearly always
  trivially true within one state**, so it kept winning before the fallback
  path could ever run. (Also caught in the same round: a hardcoded zoom=10
  Nominatim query resolves directly to a county-level feature and omits any
  hamlet/locality entirely, even where one exists -- zoom=14 returns the full
  hierarchy, hamlet AND county AND state, whenever a finer feature exists.)
* This version: majority agreement is only checked at **city and county**
  tiers. If neither reaches a majority (the 3+-county case above), we go
  straight to the centroid's own finest available field (city, else county,
  else state) instead of ever accepting a bare majority-of-state -- a
  specific-but-not-perfectly-representative name (e.g. "Guadalupe Heights,
  Texas") is far more useful to a user scanning a list of runs than a
  technically-safe "Texas" that's true of nearly every run in the catalog.
* Every Nominatim call is a single attempt with a short timeout, swallowed on
  any failure -- never raises, never retries. This is a best-effort label,
  not a required field, and must never block or fail a publish.
"""

from __future__ import annotations

import time
from collections import Counter

import httpx

NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
# OSM's usage policy requires an identifying User-Agent for automated use.
USER_AGENT = "SUBSIDE-STAC-Publisher/1.0 (contact: wmobley@tacc.utexas.edu)"
REQUEST_TIMEOUT_S = 5.0
# Live testing against the real subsidence-rates collection showed that firing
# all 3 sample-point requests back-to-back (only throttling *between* items,
# not *within* one resolve_location() call) got rate-limited by Nominatim after
# ~7 items -- the remaining items silently got zero usable responses. This is
# the intra-call throttle; callers doing many resolve_location() calls in a
# row (e.g. scripts/backfill_locations.py) still need their OWN inter-item
# delay on top of this.
REQUEST_DELAY_S = 1.1

# Priority order within the "city" tier -- most-specific settlement-level field
# first. Same list `agents/ckan-agent-api` uses in its own reverse geocoder
# (app/agents/ckan_registration/persona_nodes.py::_reverse_geocode) for a
# WebODM/CKAN dataset-title flow; kept in sync manually since the two repos
# don't share a Python package.
_CITY_KEYS = ("neighbourhood", "suburb", "quarter", "city_district",
              "city", "town", "village", "hamlet", "municipality", "borough")
_FIELD_TIERS = ("city", "county", "state")


def _sample_points(bbox: list[float]) -> list[tuple[float, float]]:
    """Centroid + two opposite corners, as (lat, lon). Centroid is always
    first -- callers rely on that order for the fallback path."""
    west, south, east, north = bbox
    centroid = ((south + north) / 2, (west + east) / 2)
    sw = (south, west)
    ne = (north, east)
    return [centroid, sw, ne]


def _reverse_geocode(lat: float, lon: float, transport: httpx.BaseTransport | None = None) -> dict | None:
    """One Nominatim /reverse call. Never raises; returns None on any failure.

    zoom=14 (not a coarser value): live testing showed Nominatim resolves
    directly to whatever admin level the zoom implies and only includes finer
    levels in `address` when the matched feature is at least that fine -- a
    zoom=10 (county-level) query for a rural Hill Country point omitted the
    hamlet entirely, even though the exact same point at zoom=14 returned
    "hamlet": "Guadalupe Heights" AND "county": "Kerr County" AND "state":
    "Texas" all in the one response. zoom=14 gets the full hierarchy whenever
    one exists; there's no need to also query a coarser zoom separately.
    Same zoom `agents/ckan-agent-api`'s `_reverse_geocode` uses.

    `transport` is test-only (see test_geocode.py) -- matches the
    httpx.MockTransport pattern already used by ckan.py/stac_client.py so this
    can be tested without hitting the live endpoint.
    """
    try:
        with httpx.Client(transport=transport) as client:
            resp = client.get(
                NOMINATIM_REVERSE,
                params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1, "zoom": 14},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT_S,
            )
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception:
        return None


def _field_value(address: dict, tier: str) -> str | None:
    if tier == "city":
        for key in _CITY_KEYS:
            if address.get(key):
                return address[key]
        return None
    return address.get(tier)


def _format_location(tier: str, value: str, state: str | None) -> str:
    if tier == "state":
        return value
    if tier == "county" and not value.lower().endswith("county"):
        value = f"{value} County"
    return f"{value}, {state}" if state else value


def resolve_location(
    bbox: list[float],
    *,
    transport: httpx.BaseTransport | None = None,
    request_delay_s: float = REQUEST_DELAY_S,
) -> str | None:
    """Best-effort human-readable place name for a run's bbox, or None.

    Samples 3 points across the bbox (centroid + 2 opposite corners) and
    tries the finest tier (city) first, accepting a value once a MAJORITY of
    the resolved points agree on it, then county. State is deliberately NOT
    checked for majority (see module docstring: it's nearly always trivially
    true within one state and would mask genuine city/county disagreement).
    If neither city nor county reaches a majority, falls back to the
    centroid's own finest available field (city, county, or state) rather
    than returning nothing. Never raises.

    `transport` and `request_delay_s=0` are test-only (tests pass 0 to avoid
    real sleeps); production callers (``parse_manifest`` via
    ``publish_from_dir``) call this as ``resolve_location(bbox)``.
    """
    try:
        if not bbox or len(bbox) != 4:
            return None
        points = _sample_points(bbox)
        # Positionally aligned with `points` (None where a point failed), so
        # the centroid fallback below can't accidentally pick a corner's
        # address just because the centroid itself happened to fail.
        raw = []
        for i, (lat, lon) in enumerate(points):
            if i > 0 and request_delay_s:
                time.sleep(request_delay_s)
            raw.append(_reverse_geocode(lat, lon, transport))
        addresses_by_point = [r.get("address", {}) if r else None for r in raw]
        resolved = [a for a in addresses_by_point if a is not None]
        if not resolved:
            return None
        # addresses_by_point[0] is the centroid's address, or None if it alone
        # failed to resolve -- fall back to whichever point DID resolve.
        centroid_address = addresses_by_point[0] or resolved[0]

        # Deliberately excludes "state" -- see module docstring.
        for tier in ("city", "county"):
            values = [v for v in (_field_value(a, tier) for a in resolved) if v]
            if not values:
                continue
            value, count = Counter(values).most_common(1)[0]
            if count * 2 >= len(resolved):  # majority among points that resolved
                state = centroid_address.get("state")
                return _format_location(tier, value, state)

        # No city/county majority (e.g. the run spans genuinely different
        # counties) -- use the centroid's own finest available field rather
        # than falling back to a majority-only "state" that's true of nearly
        # every run.
        state = centroid_address.get("state")
        for tier in _FIELD_TIERS:
            value = _field_value(centroid_address, tier)
            if value:
                return _format_location(tier, value, state)
        return None
    except Exception:
        return None
