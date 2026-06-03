"""Make stac-fastapi-pgstac's asyncpg pool connect to the Tapis pods Postgres.

The pods Postgres endpoint is an SNI tunnel on :443 that expects a **PostgreSQL
direct-SSL** connection: TLS immediately on connect (no ``SSLRequest`` preamble)
*and* the TLS ClientHello must advertise ALPN protocol ``postgresql`` (required by
PostgreSQL 17+ direct SSL). libpq ≥ 17 does both with ``sslnegotiation=direct``,
which is why ``pypgstac migrate`` (psycopg/libpq) connects fine.

stac-fastapi-pgstac's runtime pool uses **asyncpg**, whose ``direct_tls=True`` path
does the immediate TLS but does **not** set ALPN — so the pod drops the connection
("connection was closed in the middle of operation"). The fix is to hand asyncpg an
SSL context with ``set_alpn_protocols(['postgresql'])`` and ``direct_tls=True``.

Upstream ``_create_pool`` exposes neither, so we monkeypatch that one function.
Gated on ``PGSSLNEGOTIATION=direct`` so local plaintext setups (docker-compose,
``PGSSLMODE=disable``) are untouched.
"""

from __future__ import annotations

import os
import ssl


def make_direct_tls_context() -> ssl.SSLContext:
    """SSL context for PostgreSQL direct SSL: ALPN 'postgresql', encrypt-don't-verify.

    CERT_NONE / check_hostname=False mirrors libpq ``sslmode=require`` (the pod
    cert is self-signed), which is what SUBSIDE's PostGIS pod connection uses.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(["postgresql"])
    return ctx


def apply() -> bool:
    """Patch stac_fastapi.pgstac.db._create_pool for pods direct SSL. Returns True if applied."""
    if os.environ.get("PGSSLNEGOTIATION", "").lower() != "direct":
        return False

    import stac_fastapi.pgstac.db as db
    from buildpg import asyncpg as bp

    ctx = make_direct_tls_context()

    async def _create_pool_direct_tls(settings):
        return await bp.create_pool(
            settings.connection_string,
            min_size=settings.db_min_conn_size,
            max_size=settings.db_max_conn_size,
            max_queries=settings.db_max_queries,
            max_inactive_connection_lifetime=settings.db_max_inactive_conn_lifetime,
            init=db.con_init,
            server_settings=settings.server_settings.model_dump(),
            ssl=ctx,
            direct_tls=True,
        )

    db._create_pool = _create_pool_direct_tls
    return True
