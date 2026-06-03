# PgSTAC bootstrap

The STAC API and the bridge both expect the [PgSTAC](https://github.com/stac-utils/pgstac)
schema to exist in the target database.

```bash
# 1. point PG* at the database (see ../.env.sample)
export PGHOST=stacpostgres.pods.portals.tapis.io PGPORT=443 \
       PGDATABASE=stac PGUSER=stac PGPASSWORD=... \
       PGSSLMODE=require PGSSLNEGOTIATION=direct

# 2. install / upgrade the schema
python -m pgstac.migrate
```

`PGSSLNEGOTIATION=direct` (libpq ≥ 17) is required against the Tapis pods 443
TLS-SNI tunnel — without it libpq fails with "SSL error: unexpected eof while
reading" (same gotcha as SUBSIDE's PostGIS pod).

The `stacpostgres` pod itself is created by `deploy/register_pods.py` (a stock
Postgres image is fine; PgSTAC is pure SQL installed by the migrate step above).
