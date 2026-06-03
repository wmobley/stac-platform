# stac-platform

A small, **project-agnostic STAC API** that sits *beside* CKAN. CKAN stays the
human-facing catalog and asset store; this service gives machines a
standards-compliant spatiotemporal search API (`pystac-client`, `odc-stac`,
TiTiler-PgSTAC, …) over the same data.

It was first built for [SUBSIDE](https://github.com/wmobley/modflow-suite) (OPERA
DISP-S1 subsidence rasters) but is deliberately decoupled so any TACC project can
register its own CKAN dataset as a STAC Collection.

## How it fits together

```
your pipeline ─► publish task ─┬─► CKAN  (upload COG/PNG/manifest; link NetCDF)
                               └─► STAC API (Transactions: PUT item)  ─► appears in /search now

CKAN ──(safety net, scheduled)──► bridge reconcile ──► PgSTAC
PgSTAC ◄──► stac-fastapi-pgstac  (/, /collections, /search, transactions)
                                          └─(later)─► TiTiler-PgSTAC (tiles/mosaics)
```

- **One CKAN dataset = one STAC Collection.** Each job's outputs are CKAN
  *resources* tagged with a `stac_item_id` and grouped into one STAC Item.
- **Assets** point at public CKAN resource download URLs. The source NetCDF is
  *not* uploaded — it is a `source` **link asset** (back-populatable).
- **Publishing dual-writes** to CKAN and the STAC API in one step (no polling lag).
- The **bridge** reconciles CKAN → PgSTAC on a schedule as a backstop / backfill.

## Components

| Path | What |
|------|------|
| `stacmap/` | Shared core: manifest→granule, asset/role inference, STAC Item/Collection builders, CKAN + STAC HTTP clients, and `publish.py` (the **dual-write** entrypoint). Imported by the publish step and the bridge. |
| `stac_api/` | Thin launcher around `stac-fastapi-pgstac` (reads config, enables extensions + Transactions). |
| `bridge/` | Reconcile / backfill CKAN → PgSTAC (CLI; runs on a schedule). |
| `pgstac/` | `pypgstac migrate` runner + bootstrap notes. |
| `deploy/` | Dockerfiles, `register_pods.py`, env sample. |

Publishing runs as a **lightweight Tapis Workflows FunctionTask** (`python:3.11`,
`pip install git+…stac-platform`, no image) — see `stac-publish` in
[SUBSIDE's pipeline](https://github.com/wmobley/subside) and `stacmap/publish.py`.
This repo is pip-installable (`pyproject.toml`) so that task can install it.

## Quick start (local)

```bash
cp .env.sample .env            # fill in PG*, CKAN_URL/TOKEN, STAC_URL/TOKEN
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-api.txt          # API + bridge
pip install -e .                              # the stacmap library (publish core)

# 1. bring up a pgstac Postgres (any Postgres ≥ 14 with the pgstac schema)
python -m pgstac.migrate                      # runs pypgstac migrate against PG*

# 2. run the STAC API
uvicorn stac_api.app:app --reload --port 8081

# 3. dual-write a granule (CKAN + STAC) from a SUBSIDE manifest
python -m stacmap.publish \
  --collection subsidence-rates --item-id job-123 \
  --manifest ./run-manifest.json \
  --cog ./disp_displacement.tif --overlay ./disp_overlay.png

# 4. reconcile CKAN → STAC (backfill / drift repair)
python -m bridge.cli reconcile --dataset subsidence-rates
```

## Testing the API

**No-dependency unit tests** (pure mapping logic — no DB/CKAN/network):

```bash
python tests/test_mapping.py        # or: python -m pytest tests/ -q
```

**Full API, end-to-end with Docker** (recommended — avoids local Python/PgSTAC setup):

```bash
docker compose up -d db                                    # PostGIS
docker compose run --rm stac-api python -m pgstac.migrate   # install pgstac schema (once)
docker compose up -d stac-api                              # serve on :8081

# smoke-test the endpoints
curl -s localhost:8081/healthz
curl -s localhost:8081/                  | python -m json.tool   # landing page / conformance
curl -s localhost:8081/collections       | python -m json.tool

# create a collection + item via the Transactions extension (writes are open locally)
curl -s -X POST localhost:8081/collections \
  -H 'Content-Type: application/json' \
  -d '{"type":"Collection","stac_version":"1.0.0","id":"demo",
       "description":"demo","license":"proprietary",
       "extent":{"spatial":{"bbox":[[-180,-90,180,90]]},
                 "temporal":{"interval":[[null,null]]}},"links":[]}'

curl -s -X PUT localhost:8081/collections/demo/items/item-1 \
  -H 'Content-Type: application/json' \
  -d '{"type":"Feature","stac_version":"1.0.0","id":"item-1","collection":"demo",
       "geometry":{"type":"Point","coordinates":[-95.4,29.6]},
       "bbox":[-95.4,29.6,-95.4,29.6],
       "properties":{"datetime":"2024-06-01T00:00:00Z"},"assets":{},"links":[]}'

# search it back
curl -s -X POST localhost:8081/search \
  -H 'Content-Type: application/json' \
  -d '{"collections":["demo"],"bbox":[-96,29,-95,30]}' | python -m json.tool
```

`docker compose down -v` tears it down (the `-v` drops the database volume).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the data model and design rationale.
