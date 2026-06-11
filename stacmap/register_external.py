"""Register *external* reference datasets into CKAN (the human catalog).

Some layers the SUBSIDE map references aren't SUBSIDE run products — they are
third-party services we link to (e.g. TWDB's ArcGIS Well Reports FeatureServer).
Those still belong in the CKAN catalog so they're discoverable, but they must NOT
be tagged into a STAC Collection's ``stac_collection`` namespace: the reconcile
bridge rebuilds a collection from its CKAN run-datasets and *prunes* anything it
can't reconstruct, which would wipe directly-authored context Items. So these are
**standalone** datasets carrying only link resources.

Field layout mirrors the existing ``twdb-subside`` datasets on
ckan.tacc.utexas.edu (e.g. ``twdb-groundwater-models``), which use the plain CKAN
``dataset`` type: dataset-level ``title`` / ``notes`` / ``tag_string`` /
``maintainer`` / ``maintainer_email`` / ``license_id`` with ``private: false``, and
standard resource fields ``name`` / ``description`` / ``format`` / ``url``. The
richer SUBSIDE-schema terms (``collection_method``, ``quality_control_level``,
``categories``, ``program_area``, …) plus ckanext-spatial's ``spatial`` GeoJSON go
in package **extras** (``extras`` object values are JSON-encoded to strings as CKAN
stores them; ``spatial`` is read by ckanext-spatial and surfaces top-level in
``package_show``).

The map-overlay side of the same layer is registered separately as a STAC context
Item — edit ``context_layers.json`` and run ``python -m stacmap.register_context``.

Specs file (JSON) shape::

    {
      "datasets": [
        {
          "name": "submitted-drillers-report-database",  # CKAN slug
          "type": "dataset",
          "owner_org": "twdb-subside",
          "private": false,
          "dataset_fields": {"title": "...", "notes": "...", "tag_string": "A,B",
                             "maintainer": "...", "maintainer_email": "...",
                             "license_id": "notspecified"},
          "extras": {"spatial": {<GeoJSON>}, "collection_method": "Administrative Record",
                     "quality_control_level": "Raw/Provisional/Un-reviewed",
                     "categories": "Groundwater"},
          "resources": [
            {"name": "...", "url": "https://.../FeatureServer/0", "format": "Esri REST",
             "fields": {"description": "..."}}
          ]
        }
      ]
    }

Usage::

    python -m stacmap.register_external --specs external_datasets.json
    # honors CKAN_URL / CKAN_TOKEN / CKAN_ORG from the environment (see CkanClient)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .ckan import CkanClient

# Load .env so local runs pick up CKAN_*/TAPIS_* (no-op in the pod, where these
# come from the container env and there's no .env).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

#: Shipped default specs (the TWDB Well Reports external service).
DEFAULT_SPECS = Path(__file__).resolve().parents[1] / "external_datasets.json"


def register(specs: dict, *, ckan: CkanClient | None = None) -> list[str]:
    """Upsert each standalone dataset + its link resources. Returns the dataset names."""
    own = ckan is None
    ckan = ckan or CkanClient()
    try:
        names: list[str] = []
        for ds in specs.get("datasets", []):
            # Dataset-level schema fields. Object/array values (e.g. ckanext-spatial's
            # `spatial` GeoJSON) are JSON-encoded to strings, matching CKAN storage.
            fields = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                      for k, v in (ds.get("dataset_fields") or {}).items()}
            if ds.get("private") is not None:
                fields["private"] = ds["private"]
            # Extra package fields (SUBSIDE schema terms + ckanext-spatial `spatial`)
            # become CKAN extras; object/array values are JSON-encoded to strings,
            # matching how CKAN stores them.
            extras = {k: (json.dumps(v) if isinstance(v, (dict, list)) else str(v))
                      for k, v in (ds.get("extras") or {}).items()}
            pkg = ckan.ensure_dataset(
                ds["name"],
                dataset_type=ds.get("type"),
                owner_org=ds.get("owner_org"),
                fields=fields,
                extras=extras or None,
            )
            name = pkg["name"]
            for res in ds.get("resources", []):
                ckan.link_resource(
                    name, res["url"],
                    name=res.get("name"),
                    fmt=res.get("format"),
                    extra_fields=res.get("fields"),
                )
            names.append(name)
        return names
    finally:
        if own:
            ckan.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Register external reference datasets into CKAN.")
    p.add_argument(
        "--specs",
        default=str(DEFAULT_SPECS),
        help=f"Path to the external-datasets specs JSON (default: {DEFAULT_SPECS.name}).",
    )
    args = p.parse_args(argv)

    # CKAN at ckan.tacc is Tapis-fronted: prefer an explicit CKAN_TOKEN, else mint
    # a Tapis JWT via password grant from TAPIS_BASE_URL/TAPIS_USERNAME/TAPIS_PASSWORD
    # (prompts for a missing password). The minted JWT is sent as a bearer token.
    token = os.environ.get("CKAN_TOKEN")
    if not token:
        from .tapis_auth import mint_tapis_jwt
        token = mint_tapis_jwt()
    ckan = CkanClient(token=token)

    specs = json.loads(Path(args.specs).read_text())
    try:
        names = register(specs, ckan=ckan)
    finally:
        ckan.close()
    print(f"registered {len(names)} external CKAN dataset(s): {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
