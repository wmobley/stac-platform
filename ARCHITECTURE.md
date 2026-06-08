# Architecture

## Why this exists

CKAN is a great *human* catalog (organizations, metadata editing, a publishing
workflow) and can store/serve files, but it is not a spatiotemporal search API and
is awkward for cloud-native geospatial tooling. STAC is exactly that machine API.
Rather than replace CKAN, this service runs **beside** it: CKAN owns the bytes and
the human catalog; STAC owns spatiotemporal discovery.

## Data model: CKAN ↔ STAC

**One CKAN dataset = one run = one STAC Item.** Each job's output is its own CKAN
dataset (named `{collection}--{item_id}`) holding just that run's handful of
resources, so the CKAN catalog page stays browsable instead of being one dataset
with hundreds of resources. The **STAC Collection is not a CKAN dataset** — it is a
logical grouping that spans many per-run datasets, joined by a `stac_collection`
package extra, and surfaced as a single stable Collection at
`/collections/subsidence-rates`. Its extent is recomputed from member items.

| CKAN | STAC | Notes |
|------|------|-------|
| per-run dataset (package) | **Item** | one dataset = one job. Carries `stac_collection` + `stac_item_id` extras. `geometry`/`bbox`/`datetime` come from its manifest resource. |
| `stac_collection` extra (spans datasets) | **Collection** | `id` = the extra's value. Discovered via `package_search` `fq=stac_collection:…`; extent recomputed from members. |
| resource (file) | **Asset** | role/media-type inferred from name/format (see `stacmap/assets.py`). |

Asset roles:

- `*.tif` COG → `data` + `visual`, `image/tiff; application=geotiff; profile=cloud-optimized`
- `*.png` overlay → `overlay`, `image/png`
- `*.json` manifest → `metadata`, `application/json`
- NetCDF → `source` **link asset** (`application/x-netcdf`); href points at the
  product's existing location (ASF / Tapis archive). It is registered in CKAN as a
  link-type resource so it is recorded and back-populatable, **not** uploaded.

The displacement display range (`artifacts.display_range.vmin/vmax` in the SUBSIDE
manifest) is carried onto the COG asset as `raster:bands` statistics so a tiler can
auto-rescale.

## Write paths

1. **Dual-write (hot path).** The `publish` task ensures a per-run CKAN dataset
   (tagged with `stac_collection` + `stac_item_id`), uploads the run's files into
   it, builds the STAC Item from the manifest + the resulting CKAN URLs, ensures the
   Collection exists, and `PUT`s the Item via the STAC **Transactions** extension.
   The Item is searchable immediately — no polling lag. CKAN is written first; if the
   STAC write fails the granule is still in CKAN and the bridge will pick it up.

2. **Reconcile (backstop).** The `bridge` reads CKAN on a schedule, finds every
   per-run dataset tagged with a collection (`package_search` on `stac_collection`),
   rebuilds one Item per dataset, upserts the Collection + Items, and prunes STAC
   Items whose CKAN dataset has disappeared. This makes CKAN the source of truth and
   backfills runs that predate / bypass the task.

Both paths build identical Item/Collection dicts through `stacmap/stac.py`, so the
two write paths can never disagree on shape.

## Shared core (`stacmap/`)

| Module | Responsibility |
|--------|----------------|
| `manifest.py` | Parse a SUBSIDE manifest into a generic `Granule` (item id, bbox, datetimes, source URLs, display range). The only SUBSIDE-specific code. |
| `assets.py` | Map a (name, href, format) to a STAC Asset dict with the right role + media type. |
| `stac.py` | Pure builders: `bbox_to_geometry`, `build_item`, `build_collection`. No I/O. |
| `ckan.py` | CKAN Action API client: `ensure_run_dataset` (per-run package + extras), `upload_resource`, `link_resource`, `iter_collection_items` (per-run datasets in a collection, each → one Item). |
| `stac_client.py` | STAC Transactions HTTP client: `ensure_collection`, `upsert_item`. |

To onboard a new project: add (or reuse) a manifest parser that yields a `Granule`,
pick a Collection id, and call the publish task. Everything else is project-agnostic.

## Deployment

Mirrors SUBSIDE: GHCR images via GitHub Actions, registered as Tapis pods with
`deploy/register_pods.py`.

- `stac-api` pod — `stac-fastapi-pgstac` (`:8081`).
- `stacpostgres` pod — PgSTAC Postgres (its own pod; not shared with `subsidepostgres`).
- `stac-bridge` image — the reconcile/backfill CLI (scheduled; no long-running pod).
- **publish** — no image: a lightweight Tapis Workflows **FunctionTask** that
  `pip install git+…stac-platform` and calls `stacmap.publish`. The orchestrator
  (which holds Tapis creds) fetches the `tapis://` COG/overlay/manifest and the
  task dual-writes to CKAN + STAC.
- later: `stac-titiler` pod — TiTiler-PgSTAC for dynamic tiles/mosaics.
