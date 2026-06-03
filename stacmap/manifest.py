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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
