#!/usr/bin/env python3
"""Backfill `subside:location` onto already-published SUBSIDE STAC items.

New runs get `subside:location` automatically at publish time (see
`stacmap/geocode.py` + `stacmap/manifest.py::parse_manifest`'s opt-in
`resolve_location` param, wired into the three Tapis pipelines). This script
is the one-off catch-up for items published *before* that existed.

SAFETY: by default this is a DRY RUN -- it resolves locations and prints what
would change, but writes nothing. Pass --apply to perform the real STAC write
(external write; see this project's approval-gate rules). STAC-only, per the
design spec's decision to drop CKAN mirroring for v1 (the UI never reads CKAN
extras, so there's nothing there to keep in sync) -- see
`modflow-suite/subside/docs/design/2026-07-24-run-location-labels.md`.

Idempotent: items that already carry `subside:location` are skipped, so this
is safe to re-run (e.g. after a partial run, or once new items accumulate
without a location for some reason).

Usage:
    # dry run (no external write) -- prints what would change
    STAC_URL=https://stacapi.pods.portals.tapis.io/api/v1 \\
        python scripts/backfill_locations.py

    # real write
    STAC_URL=... STAC_TOKEN=... python scripts/backfill_locations.py --apply

    # a single item, for a controlled test before a full run
    python scripts/backfill_locations.py --item-id subside-werc-...-007 --apply
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacmap.geocode import resolve_location
from stacmap.stac_client import StacClient

DEFAULT_COLLECTION = "subsidence-rates"
DEFAULT_DELAY_S = 1.5  # between items that actually hit Nominatim (3 calls/item)


def _backfill_one(client: StacClient, collection: str, item_id: str, *, apply: bool) -> str:
    """Returns one of: 'already-had', 'no-bbox', 'unresolved', 'resolved'."""
    item = client.get_item(collection, item_id)
    if item is None:
        print(f"[skip]      {item_id}: not found (listed but since deleted?)")
        return "no-bbox"

    props = item.setdefault("properties", {})
    if props.get("subside:location"):
        print(f"[skip]      {item_id}: already has subside:location={props['subside:location']!r}")
        return "already-had"

    bbox = item.get("bbox")
    if not bbox or len(bbox) != 4:
        print(f"[skip]      {item_id}: no usable bbox")
        return "no-bbox"

    location = resolve_location(bbox)
    if not location:
        print(f"[unresolved] {item_id}: no 3-point agreement at any tier for bbox={bbox}")
        return "unresolved"

    if apply:
        # PATCH only the changed field (JSON Merge Patch) rather than
        # get-mutate-PUT the whole item: GET returns server-injected
        # self/parent/root/collection `links` (see stac.py's build_item()),
        # and re-submitting those verbatim on a full PUT gets rejected with
        # 400 -- confirmed live. PATCH sidesteps the whole class of bug.
        client.patch_item(collection, item_id, {"properties": {"subside:location": location}})
        print(f"[applied]   {item_id}: subside:location={location!r}")
    else:
        print(f"[dry-run]   {item_id}: would set subside:location={location!r}")
    return "resolved"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help=f"STAC collection id (default: {DEFAULT_COLLECTION})")
    parser.add_argument("--stac-url", default=None, help="STAC API base URL (default: $STAC_URL)")
    parser.add_argument("--stac-token", default=None, help="STAC bearer token (default: $STAC_TOKEN)")
    parser.add_argument("--item-id", default=None, help="Backfill a single item id instead of scanning the whole collection")
    parser.add_argument("--limit", type=int, default=1000, help="Max items to list from the collection (default: 1000)")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY_S, help=f"Seconds to sleep between geocoded items (default: {DEFAULT_DELAY_S})")
    parser.add_argument("--apply", action="store_true", help="Perform the real STAC write. Default is dry-run (no external write).")
    args = parser.parse_args()

    client = StacClient(url=args.stac_url, token=args.stac_token)
    try:
        if args.item_id:
            item_ids = [args.item_id]
        else:
            item_ids = client.list_item_ids(args.collection, limit=args.limit)
            print(f"Found {len(item_ids)} item(s) in collection '{args.collection}'.")

        print(f"Mode: {'APPLY (writing to STAC)' if args.apply else 'DRY RUN (no writes)'}\n")

        counts = {"already-had": 0, "no-bbox": 0, "unresolved": 0, "resolved": 0}
        for i, item_id in enumerate(item_ids):
            outcome = _backfill_one(client, args.collection, item_id, apply=args.apply)
            counts[outcome] += 1
            # Only items that actually called Nominatim (3 requests) need throttling.
            if outcome in ("unresolved", "resolved") and i < len(item_ids) - 1:
                time.sleep(args.delay)

        print(
            f"\nDone. {len(item_ids)} scanned — "
            f"{counts['resolved']} resolved, {counts['already-had']} already had a location, "
            f"{counts['unresolved']} unresolved (no agreement), {counts['no-bbox']} skipped (no bbox)."
        )
        if not args.apply and counts["resolved"]:
            print("This was a dry run -- re-run with --apply to write these locations to STAC.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
