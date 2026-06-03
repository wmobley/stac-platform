"""PgSTAC writer — upsert collections/items and prune, via pgstac SQL functions.

The bridge writes straight to PgSTAC (it runs server-side, with DB creds) using
pgstac's stable SQL API (``pgstac.upsert_collection``, ``pgstac.upsert_item``,
``pgstac.delete_item``) rather than the pypgstac Loader, whose Python signatures
drift between releases.

Connection: psycopg reads the standard ``PG*`` libpq env vars (``PGHOST``,
``PGPORT``, ``PGDATABASE``, ``PGUSER``, ``PGPASSWORD``, ``PGSSLMODE``, and
``PGSSLNEGOTIATION=direct`` for the Tapis pods tunnel). No URL is assembled here.
"""

from __future__ import annotations

import json
from typing import Any


class PgstacWriter:
    def __init__(self, conninfo: str = ""):
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("psycopg is required (pip install 'psycopg[binary]')") from exc
        import psycopg

        # Empty conninfo -> libpq uses PG* env vars (incl. PGSSLNEGOTIATION).
        self._conn = psycopg.connect(conninfo, autocommit=True)
        # search_path so unqualified pgstac.* still resolves if schema moves.
        with self._conn.cursor() as cur:
            cur.execute("SET search_path TO pgstac, public")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PgstacWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert_collection(self, collection: dict[str, Any]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT pgstac.upsert_collection(%s::jsonb)", (json.dumps(collection),)
            )

    def upsert_item(self, item: dict[str, Any]) -> None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT pgstac.upsert_item(%s::jsonb)", (json.dumps(item),))

    def existing_item_ids(self, collection_id: str) -> set[str]:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM pgstac.items WHERE collection = %s", (collection_id,)
            )
            return {row[0] for row in cur.fetchall()}

    def delete_item(self, item_id: str, collection_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT pgstac.delete_item(%s, %s)", (item_id, collection_id)
            )

    def collection_exists(self, collection_id: str) -> bool:
        with self._conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pgstac.collections WHERE id = %s", (collection_id,)
            )
            return cur.fetchone() is not None
