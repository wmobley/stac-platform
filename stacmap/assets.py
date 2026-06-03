"""Map a published file (name / href / format) to a STAC Asset dict.

Keeps role + media-type inference in one place so the publish task and the bridge
classify assets identically. Roles follow STAC conventions; the COG gets both
`data` and `visual` plus the cloud-optimized media-type profile so clients and
tilers treat it as a COG.
"""

from __future__ import annotations

from typing import Any

COG_MEDIA_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
PNG_MEDIA_TYPE = "image/png"
JSON_MEDIA_TYPE = "application/json"
NETCDF_MEDIA_TYPE = "application/x-netcdf"


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


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
) -> dict[str, Any]:
    """Build a single STAC Asset dict.

    If ``display_range`` (``{"vmin","vmax"}``) is given it is attached as
    ``raster:bands`` statistics so a tiler can auto-rescale the COG.
    """
    asset: dict[str, Any] = {"href": href}
    if media_type:
        asset["type"] = media_type
    if roles:
        asset["roles"] = roles
    if title:
        asset["title"] = title
    if display_range and "vmin" in display_range and "vmax" in display_range:
        asset["raster:bands"] = [
            {
                "statistics": {
                    "minimum": display_range["vmin"],
                    "maximum": display_range["vmax"],
                }
            }
        ]
    return asset


def asset_for_resource(
    name: str,
    href: str,
    *,
    title: str | None = None,
    display_range: dict[str, float] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (asset_key, asset_dict) for a resource by inferring its type."""
    key, roles, media_type = classify(name)
    dr = display_range if key == "cog" else None
    return key, make_asset(
        href, title=title or name, media_type=media_type, roles=roles, display_range=dr
    )
