"""Install / upgrade the PgSTAC schema in the target database.

Wraps ``pypgstac migrate`` against the standard PG* env vars (incl.
``PGSSLNEGOTIATION=direct`` for the Tapis pods 443 tunnel). Run once when the
``stacpostgres`` pod is fresh, and again after bumping the pypgstac version.

    python -m pgstac.migrate
"""

from __future__ import annotations

import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def main() -> int:
    from pypgstac.db import PgstacDB
    from pypgstac.migrate import Migrate

    # Empty DSN -> libpq reads PG* env vars.
    with PgstacDB(dsn="") as db:
        migrator = Migrate(db)
        version = migrator.run_migration()
    print(f"pgstac migrated to {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
