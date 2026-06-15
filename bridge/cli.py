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

# Load .env so local runs pick up PG*/CKAN_* (like pgstac.migrate). In the pod
# these come from the container env and there's no .env, so this is a no-op.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from stacmap.ckan import CkanClient  # noqa: E402  (after dotenv load)

from .loader import PgstacWriter  # noqa: E402
from .reconcile import reconcile_collection  # noqa: E402

# Collections whose Items are authored directly via the Transactions API (e.g.
# `stacmap.register_context` writing the `subside-context` overlays) — NOT rebuilt
# from per-run CKAN datasets. Reconciling one would find no CKAN run-datasets and
# prune every directly-authored Item, so the bridge refuses by default. These
# persist in PgSTAC and are maintained only by their register_* command.
PROTECTED_COLLECTIONS = frozenset({"subside-context"})


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
    p.add_argument("--force-protected", action="store_true",
                   help="Allow reconciling a PROTECTED_COLLECTIONS id (e.g. subside-context). "
                        "Dangerous: prunes directly-authored Items not backed by CKAN run-datasets.")
    args = p.parse_args(argv)

    blocked = set(args.collection) & PROTECTED_COLLECTIONS
    if blocked and not args.force_protected:
        print(f"refusing to reconcile protected collection(s) {sorted(blocked)}: their Items are "
              f"authored via the Transactions API (e.g. stacmap.register_context), not CKAN "
              f"run-datasets, so a reconcile would prune them. Re-run that register command to "
              f"update them, or pass --force-protected if you really mean to.", file=sys.stderr)
        return 2

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
