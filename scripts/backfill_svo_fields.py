#!/usr/bin/env python3
"""Backfill `mint_standard_variables` onto already-published SUBSIDE CKAN resources.

New runs get the field at publish time (see `stacmap.publish`, `role_variables`).
This is the one-off catch-up for the datasets published before that existed.

Which variable a resource gets is decided from its own `subside_asset_key` field
(`cog` / `cumulative` / `velocity`) -- NOT the file name (cumulative and
velocity are both `.tif`) and NOT `subside_asset_role` (that is `data` for every
COG). Only TIFF resources with `subside_asset_role == data` are considered.

SAFETY
  * DRY RUN by default: reads CKAN, prints a summary table, writes nothing.
  * `--apply --yes` is required to write (external write; ckan-publisher gate).
  * Before ANY write, the full pre-image of every resource it would touch is
    written to a rollback manifest (JSON).
  * Writes are JSON `resource_patch` with the minimal payload
    `{"id", "mint_standard_variables"}` -- never the multipart full-field path.
  * After each patch the resource is read back; `url`, `url_type`, `format`,
    `size`, `stac_item_id`, `subside_asset_role` must be unchanged and the new
    field present, else the run aborts on the first mismatch.
  * `--rollback <manifest>` restores `mint_standard_variables` to its pre-image
    value (clearing it when the pre-image had none).
  * Idempotent: resources already carrying the right value are skipped.

Usage:
    python scripts/backfill_svo_fields.py                          # dry run
    CKAN_API_TOKEN=... python scripts/backfill_svo_fields.py --apply --yes \\
        --canary subsidence-rates--subside-h2i-...-007            # one dataset
    CKAN_API_TOKEN=... python scripts/backfill_svo_fields.py --apply --yes
    CKAN_API_TOKEN=... python scripts/backfill_svo_fields.py --rollback rollback.json --yes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stacmap.ckan import COLLECTION_FIELD, ITEM_ID_FIELD, STDVAR_FIELD, CkanClient

DEFAULT_URL = "https://ckan.tacc.utexas.edu"
DEFAULT_ORG = "stac-datasets"
DEFAULT_COLLECTION = "subsidence-rates"
# asset key (`subside_asset_key`) -> standard-variable short names.
DEFAULT_ROLE_VARIABLES: dict[str, list[str]] = {
    "cog": ["land_surface__displacement"],               # H2I, metres
    "cumulative": ["land_surface__displacement"],        # WERC, millimetres
    "velocity": ["land_surface__displacement_rate"],     # WERC, mm/year
}
# Fields that a patch must not change (checked on read-back).
UNCHANGED_FIELDS = ("url", "url_type", "format", "size", ITEM_ID_FIELD, "subside_asset_role")

ACTIONS = ("set", "skip-present", "skip-untyped", "skip-nontif")


class VerificationError(RuntimeError):
    pass


def classify_resource(res: dict, role_variables: dict[str, list[str]]) -> tuple[str, str]:
    """Return (action, desired value). Action is one of ACTIONS; value is '' unless 'set'."""
    if str(res.get("format") or "").upper() not in ("TIFF", "GEOTIFF", "TIF"):
        return "skip-nontif", ""
    if res.get("subside_asset_role") != "data":
        return "skip-untyped", ""
    names = role_variables.get(res.get("subside_asset_key") or "")
    if not names:
        return "skip-untyped", ""
    want = ",".join(names)
    if (res.get(STDVAR_FIELD) or "") == want:
        return "skip-present", want
    return "set", want


def plan(client, org: str, collection: str, role_variables, canary: str | None = None) -> list[dict]:
    """Rows describing every SUBSIDE TIFF resource and what would happen to it."""
    rows = []
    for pkg in _datasets(client, org, collection, canary):
        for res in pkg.get("resources", []):
            action, want = classify_resource(res, role_variables)
            if action == "skip-nontif":
                continue
            rows.append({"dataset": pkg["name"], "resource": res, "action": action, "value": want})
    return rows


def _datasets(client, org, collection, canary):
    if canary:
        return [client._action_json("package_show", {"id": canary})]
    out, start = [], 0
    while True:
        result = client._action_json("package_search", {
            "fq": f'organization:{org} AND {COLLECTION_FIELD}:"{collection}"',
            "rows": 100, "start": start, "include_private": True,
        })
        out.extend(result["results"])
        start += len(result["results"])
        if not result["results"] or start >= result["count"]:
            return out


def write_rollback_manifest(path: Path, rows: list[dict]) -> None:
    """Persist the FULL pre-image of every resource that would be patched."""
    todo = [r for r in rows if r["action"] == "set"]
    path.write_text(json.dumps({
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "resources": [{"dataset": r["dataset"], "pre_image": r["resource"], "new_value": r["value"]}
                      for r in todo],
    }, indent=2, sort_keys=True))


def _norm(value):
    return None if value in (None, "") else str(value)


def patch_and_verify(client, resource: dict, value: str | None) -> dict:
    """Minimal JSON patch of the field, then read back and verify nothing else moved.

    ``value`` None/'' clears the field (rollback of a resource that had none).
    """
    client._action_json("resource_patch", {"id": resource["id"], STDVAR_FIELD: value or ""})
    after = client._action_json("resource_show", {"id": resource["id"]})
    for field in UNCHANGED_FIELDS:
        # CKAN returns ``size`` as int from package_search and str from
        # resource_show; compare normalised text so a type flip is not a change.
        if _norm(after.get(field)) != _norm(resource.get(field)):
            raise VerificationError(
                f"{resource['id']}: {field} changed {resource.get(field)!r} -> {after.get(field)!r}")
    if (after.get(STDVAR_FIELD) or "") != (value or ""):
        raise VerificationError(
            f"{resource['id']}: {STDVAR_FIELD} is {after.get(STDVAR_FIELD)!r}, expected {value!r}")
    return after


def apply_plan(client, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if r["action"] != "set":
            continue
        patch_and_verify(client, r["resource"], r["value"])
        n += 1
        print(f"[patched] {r['dataset']} :: {r['resource'].get('subside_asset_key')} -> {r['value']}")
    return n


def rollback(client, manifest_path: Path) -> int:
    data = json.loads(manifest_path.read_text())
    n = 0
    for entry in data["resources"]:
        pre = entry["pre_image"]
        # Only undo our own write: a value a later publish set stays.
        current = client._action_json("resource_show", {"id": pre["id"]})
        if (current.get(STDVAR_FIELD) or "") != (entry.get("new_value") or ""):
            print(f"[kept] {entry['dataset']} :: {pre['id']} has a newer value "
                  f"{current.get(STDVAR_FIELD)!r}; not restoring")
            continue
        patch_and_verify(client, pre, pre.get(STDVAR_FIELD))
        n += 1
        print(f"[restored] {entry['dataset']} :: {pre['id']}")
    return n


def print_table(rows: list[dict]) -> None:
    print(f"{'dataset':<60} {'resource':<34} {'role':<6} {'key':<11} {'pipeline':<20} {'variable(s)':<34} action")
    for r in rows:
        res = r["resource"]
        print(f"{r['dataset'][:60]:<60} {str(res.get('name'))[:34]:<34} "
              f"{str(res.get('subside_asset_role'))[:6]:<6} {str(res.get('subside_asset_key'))[:11]:<11} "
              f"{str(res.get('subside_pipeline'))[:20]:<20} {r['value'][:34]:<34} {r['action']}")
    counts = {a: sum(1 for r in rows if r["action"] == a) for a in ACTIONS}
    print("\nsummary:", ", ".join(f"{a}={c}" for a, c in counts.items() if c),
          f"| datasets={len({r['dataset'] for r in rows})}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("CKAN_URL", DEFAULT_URL))
    p.add_argument("--org", default=DEFAULT_ORG)
    p.add_argument("--collection", default=DEFAULT_COLLECTION)
    p.add_argument("--role-variables", default=None,
                   help="JSON mapping subside_asset_key -> [variable names]. Default: "
                        + json.dumps(DEFAULT_ROLE_VARIABLES))
    p.add_argument("--canary", metavar="DATASET", help="process only this dataset name")
    p.add_argument("--apply", action="store_true", help="write to CKAN (also needs --yes)")
    p.add_argument("--yes", action="store_true", help="confirm the write")
    p.add_argument("--rollback", metavar="MANIFEST", help="restore pre-images from a rollback manifest")
    p.add_argument("--manifest-out", default=None, help="rollback manifest path (default: timestamped)")
    args = p.parse_args(argv)

    role_variables = json.loads(args.role_variables) if args.role_variables else DEFAULT_ROLE_VARIABLES
    token = os.environ.get("CKAN_API_TOKEN")
    writing = bool(args.rollback or args.apply)
    if writing and not (args.yes and token):
        p.error("writes need --yes and CKAN_API_TOKEN in the environment")
    client = CkanClient(url=args.url, token=token if writing else None)
    try:
        if args.rollback:
            print(f"restored {rollback(client, Path(args.rollback))} resources")
            return 0
        rows = plan(client, args.org, args.collection, role_variables, args.canary)
        print_table(rows)
        if not args.apply:
            print("dry run: nothing written (use --apply --yes to write)")
            return 0
        out = Path(args.manifest_out or f"svo-backfill-rollback-{time.strftime('%Y%m%dT%H%M%S')}.json")
        write_rollback_manifest(out, rows)
        print(f"rollback manifest: {out}")
        print(f"patched {apply_plan(client, rows)} resources")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
