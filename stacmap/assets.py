"""Map a published file (name / href / format) to a STAC Asset dict.

Keeps role + media-type inference in one place so the publish task and the bridge
classify assets identically. Roles follow STAC conventions; the COG gets both
`data` and `visual` plus the cloud-optimized media-type profile so clients and
tilers treat it as a COG.
"""

from __future__ import annotations

import math
from typing import Any

COG_MEDIA_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
PNG_MEDIA_TYPE = "image/png"
JSON_MEDIA_TYPE = "application/json"
NETCDF_MEDIA_TYPE = "application/x-netcdf"

# Multihash prefixes (varint code + varint length, hex) keyed by hex-digest length.
# CKAN stores a bare hex digest with no algorithm tag, so we infer the algorithm
# from the digest length: 32=md5, 40=sha1, 64=sha2-256. Anything else -> skip.
_MULTIHASH_PREFIX = {32: "d50110", 40: "1114", 64: "1220"}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _multihash(hexdigest: str | None) -> str | None:
    """Encode a bare hex digest (md5/sha1/sha2-256) as a STAC `file:checksum`.

    `file:checksum` is a Multihash in hex. CKAN gives us only the bare digest, so
    we recognize the algorithm by length and prepend its multihash prefix.
    """
    h = (hexdigest or "").strip().lower()
    if not h or any(c not in "0123456789abcdef" for c in h):
        return None
    prefix = _MULTIHASH_PREFIX.get(len(h))
    return prefix + h if prefix else None


def _byte_size(value: object) -> int | None:
    """CKAN `size` is sometimes a string; coerce to a positive int, else None."""
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def classify(name: str) -> tuple[str, list[str], str]:
    """Return (asset_key, roles, media_type) for a file name.

    The asset *key* is the dict key under Item.assets; roles drive client UIs.
    Unknown types fall back to a generic data asset keyed by extension.
    """
    ext = _ext(name)
    if ext in ("tif", "tiff"):
        return "cog", ["data", "visual"], COG_MEDIA_TYPE
    if ext == "png":
        return "overlay", ["overlay", "visual"], PNG_MEDIA_TYPE
    if ext == "nc":
        return "source", ["data", "source"], NETCDF_MEDIA_TYPE
    if ext == "json":
        return "metadata", ["metadata"], JSON_MEDIA_TYPE
    return (ext or "data"), ["data"], "application/octet-stream"


def make_asset(
    href: str,
    *,
    title: str | None = None,
    media_type: str | None = None,
    roles: list[str] | None = None,
    display_range: dict[str, float] | None = None,
    byte_size: object | None = None,
    checksum: str | None = None,
) -> dict[str, Any]:
    """Build a single STAC Asset dict.

    If ``display_range`` (``{"vmin","vmax"}``) is given and both values are finite,
    it is attached as ``raster:bands`` statistics so a tiler can auto-rescale the
    COG. NaN/Infinity are not valid JSON (httpx's strict encoder rejects them, and
    RFC 8259 forbids them), so a non-finite value is dropped rather than passed
    through — a producer's stats bug should degrade to "no rescale hint", not crash
    the STAC publish. ``byte_size`` and ``checksum`` (a bare hex digest, e.g. from
    CKAN) populate the File extension's ``file:size`` / ``file:checksum`` when
    present.
    """
    asset: dict[str, Any] = {"href": href}
    if media_type:
        asset["type"] = media_type
    if roles:
        asset["roles"] = roles
    if title:
        asset["title"] = title
    if display_range and "vmin" in display_range and "vmax" in display_range:
        vmin, vmax = display_range["vmin"], display_range["vmax"]
        if math.isfinite(vmin) and math.isfinite(vmax):
            asset["raster:bands"] = [
                {
                    "statistics": {
                        "minimum": vmin,
                        "maximum": vmax,
                    }
                }
            ]
    size = _byte_size(byte_size)
    if size is not None:
        asset["file:size"] = size
    mh = _multihash(checksum)
    if mh is not None:
        asset["file:checksum"] = mh
    return asset


def asset_for_resource(
    name: str,
    href: str,
    *,
    title: str | None = None,
    display_range: dict[str, float] | None = None,
    byte_size: object | None = None,
    checksum: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (asset_key, asset_dict) for a resource by inferring its type."""
    key, roles, media_type = classify(name)
    dr = display_range if key == "cog" else None
    return key, make_asset(
        href, title=title or name, media_type=media_type, roles=roles,
        display_range=dr, byte_size=byte_size, checksum=checksum,
    )
