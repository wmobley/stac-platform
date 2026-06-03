# Architecture

## Why this exists

CKAN is a great *human* catalog (organizations, metadata editing, a publishing
workflow) and can store/serve files, but it is not a spatiotemporal search API and
is awkward for cloud-native geospatial tooling. STAC is exactly that machine API.
Rather than replace CKAN, this service runs **beside** it: CKAN owns the bytes and
the human catalog; STAC owns spatiotemporal discovery.

## Data model: CKAN ↔ STAC

**One CKAN dataset = one STAC Collection.** A dataset (e.g. `subsidence-rates`)
maps to a single, stable Collection at `/collections/subsidence-rates`. Every job's
output across all users lives as **resources** inside that dataset, grouped into
**STAC Items** by a per-resource `stac_item_id` custom field.

| CKAN | STAC | Notes |
|------|------|-------|
| dataset (package) | **Collection** | `id` = dataset name. Extent recomputed from member items. |
| resources sharing a `stac_item_id` | **Item** | one granule = one job. `geometry`/`bbox`/`datetime` come from that group's manifest resource. |
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

1. **Dual-write (hot path).** The `publish` task uploads files to CKAN, builds the
   STAC Item from the manifest + the resulting CKAN URLs, ensures the Collection
   exists, and `PUT`s the Item via the STAC **Transactions** extension. The Item is
   searchable immediately — no polling lag. CKAN is written first; if the STAC write
   fails the granule is still in CKAN and the bridge will pick it up.

2. **Reconcile (backstop).** The `bridge` reads CKAN on a schedule, rebuilds Items
   from resources grouped by `stac_item_id`, upserts via `pypgstac`, and prunes STAC
   Items whose CKAN resources have disappeared. This makes CKAN the source of truth
   and backfills datasets that predate / bypass the task.

Both paths build identical Item/Collection dicts through `stacmap/stac.py`, so the
two write paths can never disagree on shape.

## Shared core (`stacmap/`)

| Module | Responsibility |
|--------|----------------|
| `manifest.py` | Parse a SUBSIDE manifest into a generic `Granule` (item id, bbox, datetimes, source URLs, display range). The only SUBSIDE-specific code. |
| `assets.py` | Map a (name, href, format) to a STAC Asset dict with the right role + media type. |
| `stac.py` | Pure builders: `bbox_to_geometry`, `build_item`, `build_collection`. No I/O. |
| `ckan.py` | `ckanapi` wrapper: `ensure_dataset`, `upload_resource`, `link_resource`, `iter_items` (resources grouped by `stac_item_id`). |
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
