"""stacmap — shared, project-agnostic core for the STAC platform.

Both write paths (the `publish` task and the reconcile `bridge`) build their STAC
Collections/Items through these modules so they can never disagree on shape:

    manifest.py     SUBSIDE manifest  -> generic Granule          (only project-specific bit)
    assets.py       (name, href, fmt) -> STAC Asset dict
    stac.py         Granule + assets  -> STAC Item / Collection    (pure, no I/O)
    ckan.py         CKAN Action API client (ensure_run_dataset / upload / link / iter_collection_items)
    stac_client.py  STAC Transactions HTTP client (ensure_collection / upsert_item)
"""
