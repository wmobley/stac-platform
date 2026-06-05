"""Manifest -> Granule normalization.

The only project-specific module in ``stacmap``. A *Granule* is the generic,
STAC-agnostic description of one job's output that the pure STAC builders in
``stac.py`` consume. Today the single parser understands the SUBSIDE manifest
(written by ``analysis/etl/manifest.py`` in modflow-suite); onboard another
project by adding a parser that returns a :class:`Granule`.

SUBSIDE manifest shape (the fields we read)::

    {
      "bbox": {"lon_min", "lat_min", "lon_max", "lat_max"},
      "config": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", ...},
      "frame_ids": [8882, ...],
      "product_count": 2,
      "product_urls": ["https://.../*.nc", ...],     # source NetCDFs (link assets)
      "artifacts": {"display_range": {"vmin": ..., "vmax": ...}, ...}
    }
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class CogSpec:
    """One COG asset to publish: which file, its STAC asset key, and value range."""

    filename: str                       # basename in the job's output dir
    key: str = "cog"                    # STAC asset key (cog / cumulative / velocity)
    title: str | None = None
    display_range: dict[str, float] | None = None   # {"vmin","vmax"} for tiler rescale


@dataclass
class Granule:
    """Generic, STAC-agnostic description of one job's output."""

    item_id: str
    # [west, south, east, north] in EPSG:4326 (STAC order).
    bbox: list[float]
    # RFC3339 instants. For a date *range* we set start/end and leave datetime None
    # (STAC requires either `datetime` or both `start_datetime`+`end_datetime`).
    datetime: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    # NetCDF source product URLs -> `source` link assets (not uploaded to CKAN).
    source_urls: list[str] = field(default_factory=list)
    # Extra STAC Item properties (frame ids, product count, display range, …).
    properties: dict[str, Any] = field(default_factory=dict)
    # Optional per-band min/max carried onto the COG asset for tiler auto-rescale.
    display_range: dict[str, float] | None = None


def _rfc3339_from_date(value: str | None) -> str | None:
    """`YYYY-MM-DD` (or already-RFC3339) -> RFC3339 UTC instant, or None."""
    if not value:
        return None
    text = str(value)
    # Already a full timestamp? Trust it (normalize a trailing Z).
    if "T" in text:
        return text
    try:
        d = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return d.isoformat().replace("+00:00", "Z")


def _bbox_list(bbox: dict | list | None) -> list[float]:
    """Accept SUBSIDE's {lon_min,lat_min,lon_max,lat_max} dict or a [w,s,e,n] list."""
    if bbox is None:
        raise ValueError("manifest has no bbox; cannot build a STAC geometry")
    if isinstance(bbox, dict):
        return [
            float(bbox["lon_min"]),
            float(bbox["lat_min"]),
            float(bbox["lon_max"]),
            float(bbox["lat_max"]),
        ]
    vals = [float(v) for v in bbox]
    if len(vals) != 4:
        raise ValueError(f"bbox list must have 4 values, got {len(vals)}")
    return vals


def granule_from_subside_manifest(manifest: dict, item_id: str) -> Granule:
    """Normalize a SUBSIDE run/preflight manifest dict into a :class:`Granule`."""
    config = manifest.get("config") or {}
    start = _rfc3339_from_date(config.get("start_date"))
    end = _rfc3339_from_date(config.get("end_date"))

    artifacts = manifest.get("artifacts") or {}
    display_range = artifacts.get("display_range")

    props: dict[str, Any] = {}
    if manifest.get("frame_ids"):
        props["subside:frame_ids"] = manifest["frame_ids"]
    if manifest.get("product_count") is not None:
        props["subside:product_count"] = manifest["product_count"]

    return Granule(
        item_id=item_id,
        bbox=_bbox_list(manifest.get("bbox")),
        # Date *range* product -> start/end (datetime stays null per the STAC spec).
        datetime=None if (start and end) else (start or end),
        start_datetime=start,
        end_datetime=end,
        source_urls=list(manifest.get("product_urls") or []),
        properties=props,
        display_range=display_range,
    )


def load_granule(manifest_path: str | Path, item_id: str) -> Granule:
    """Read a SUBSIDE manifest JSON file and normalize it."""
    data = json.loads(Path(manifest_path).read_text())
    return granule_from_subside_manifest(data, item_id)


def _range(pair) -> dict[str, float] | None:
    """[lo, hi] -> {'vmin','vmax'}, or None."""
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        return {"vmin": float(pair[0]), "vmax": float(pair[1])}
    return None


def parse_manifest(manifest: dict, item_id: str) -> tuple[Granule, list[CogSpec], str | None]:
    """Normalize either SUBSIDE manifest shape into (Granule, COGs, overlay filename).

    Auto-detects:
      * **H2I** (``run-manifest.json``): top-level ``bbox``; one COG
        (``artifacts.cog_tif``, default ``disp_displacement.tif``) with
        ``artifacts.display_range``; an overlay PNG (``artifacts.overlay_png``);
        NetCDF ``product_urls`` -> source link assets.
      * **WERC** (``werc-run-manifest.json``): bbox from the GeoTIFF ``bounds``;
        two COGs — cumulative (``clip_range_mm``) and velocity
        (``p02_p98_mm_per_year``); no overlay.
    """
    artifacts = manifest.get("artifacts") or {}
    config = manifest.get("config") or {}
    start = _rfc3339_from_date(config.get("start_date"))
    end = _rfc3339_from_date(config.get("end_date"))
    source_urls = list(manifest.get("product_urls") or [])
    props: dict[str, Any] = {}

    is_werc = ("cumulative_displacement_geotiff" in artifacts) or ("velocity_geotiff" in artifacts)
    if is_werc:
        cum = artifacts.get("cumulative_displacement_geotiff") or {}
        vel = artifacts.get("velocity_geotiff") or {}
        bounds = cum.get("bounds") or vel.get("bounds")
        if not bounds:
            raise ValueError("WERC manifest has no GeoTIFF bounds; cannot build geometry")
        bbox = [float(x) for x in bounds]
        cogs: list[CogSpec] = []
        if cum.get("path"):
            cogs.append(CogSpec(os.path.basename(cum["path"]), key="cumulative",
                                title="Cumulative displacement (mm)",
                                display_range=_range(cum.get("clip_range_mm"))))
        if vel.get("path"):
            cogs.append(CogSpec(os.path.basename(vel["path"]), key="velocity",
                                title="Velocity (mm/yr)",
                                display_range=_range(vel.get("p02_p98_mm_per_year"))))
        overlay = None
        if manifest.get("frame_id") is not None:
            props["subside:frame_id"] = manifest["frame_id"]
    else:
        bbox = _bbox_list(manifest.get("bbox"))
        cog_tif = artifacts.get("cog_tif")
        cogs = [CogSpec(os.path.basename(cog_tif) if cog_tif else "disp_displacement.tif",
                        key="cog", title="Displacement (COG)",
                        display_range=artifacts.get("display_range"))]
        ov = artifacts.get("overlay_png")
        overlay = os.path.basename(ov) if ov else "disp_overlay.png"
        if manifest.get("frame_ids"):
            props["subside:frame_ids"] = manifest["frame_ids"]
        if manifest.get("product_count") is not None:
            props["subside:product_count"] = manifest["product_count"]

    granule = Granule(
        item_id=item_id, bbox=bbox,
        datetime=None if (start and end) else (start or end),
        start_datetime=start, end_datetime=end,
        source_urls=source_urls, properties=props,
    )
    return granule, cogs, overlay
