"""bridge CLI — reconcile / backfill CKAN per-run datasets into PgSTAC.

    python -m bridge.cli reconcile --collection subsidence-rates
    python -m bridge.cli reconcile --collection a --collection b --no-prune
    python -m bridge.cli backfill  --collection subsidence-rates   # alias for reconcile

Each ``--collection`` resolves to every per-run CKAN dataset tagged with that
``stac_collection`` extra. Designed to run on a schedule (a Tapis cron / pod cron).
Connection + CKAN config come from the standard PG*/CKAN_* environment variables.
"""

from __future__ import annotations

import argparse
import sys

from stacmap.ckan import CkanClient

from .loader import PgstacWriter
from .reconcile import reconcile_collection


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Reconcile CKAN -> PgSTAC (STAC).")
    p.add_argument("command", choices=("reconcile", "backfill"),
                   help="reconcile (upsert + prune) or backfill (alias)")
    p.add_argument("--collection", "--dataset", dest="collection", action="append",
                   required=True,
                   help="STAC Collection id (repeatable). Resolves to every per-run "
                        "CKAN dataset tagged with this stac_collection.")
    p.add_argument("--no-prune", action="store_true",
                   help="Do not delete STAC items missing from CKAN (backfill-only).")
    p.add_argument("--title", default=None, help="Collection title (optional)")
    p.add_argument("--description", default=None, help="Collection description (optional)")
    args = p.parse_args(argv)

    prune = not (args.no_prune or args.command == "backfill")
    ckan = CkanClient()
    rc = 0
    with PgstacWriter() as writer:
        for collection in args.collection:
            try:
                res = reconcile_collection(
                    collection, ckan=ckan, writer=writer,
                    collection_title=args.title,
                    collection_description=args.description,
                    prune=prune,
                )
                print(f"[{res.collection_id}] upserted={res.upserted} "
                      f"pruned={res.pruned} skipped={res.skipped}")
            except Exception as exc:  # keep going across collections
                print(f"[{collection}] ERROR: {exc}", file=sys.stderr)
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
