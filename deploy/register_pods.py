#!/usr/bin/env python3
"""Register (or update) the stac-platform pods on portals.tapis.io.

Creates:
  * stacpostgres  — a PgSTAC Postgres database pod (Tapis "postgres" template).
  * stac-api      — the stac-fastapi-pgstac service (custom GHCR image, :8081)
                    at https://stac-api.pods.portals.tapis.io

Modeled on subside/tapis/register_pods.py. After `stacpostgres` is up, run the
schema install once:  `python -m pgstac.migrate`  (with PG* pointed at the pod).

Usage:
    export TAPIS_USERNAME=... TAPIS_PASSWORD=...
    python deploy/register_pods.py --owner wmobley --image-tag latest
    python deploy/register_pods.py --pods api          # just the API
    python deploy/register_pods.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from getpass import getpass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Forwarded into the stac-api pod (only those actually set are sent).
API_ENV_KEYS = [
    "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSSLMODE", "PGSSLNEGOTIATION",
    "STAC_API_TITLE", "STAC_API_DESCRIPTION", "STAC_API_ROOT_PATH",
    "STAC_CORS_ORIGINS", "STAC_WRITE_TOKEN",
]
SECRET_KEYS = {"PGPASSWORD", "STAC_WRITE_TOKEN"}


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass


def _pods_domain(base_url: str) -> str:
    return base_url.rstrip("/").split("://", 1)[-1]


def build_specs(owner: str, tag: str, base_url: str) -> dict[str, dict]:
    domain = _pods_domain(base_url)
    api_url = f"https://stac-api.pods.{domain}"

    api_env = {k: os.environ[k] for k in API_ENV_KEYS if os.environ.get(k)}
    api_env.setdefault("STAC_CORS_ORIGINS", api_url)

    api = {
        "pod_id": "stac-api",
        "image": f"ghcr.io/{owner}/stac-api:{tag}",
        "description": "stac-fastapi-pgstac — STAC API beside CKAN",
        "networking": {"default": {"protocol": "http", "port": 8081}},
        "resources": {"cpu_request": 250, "cpu_limit": 1000,
                      "mem_request": 512, "mem_limit": 2048},
        "environment_variables": api_env,
        "time_to_stop_default": -1,
    }
    # PgSTAC database pod from the Tapis "postgres" template (not a custom image).
    postgres = {
        "pod_id": "stacpostgres",
        "pod_template": "template/postgres",
        "description": "PgSTAC Postgres database pod",
        "time_to_stop_default": -1,
    }
    return {"api": api, "postgres": postgres, "_urls": {"api": api_url}}


def upsert_pod(t, spec: dict, *, recreate: bool, start: bool) -> None:
    pid = spec["pod_id"]
    exists = True
    try:
        t.pods.get_pod(pod_id=pid)
    except Exception:
        exists = False

    if exists and recreate:
        print(f"  [{pid}] deleting existing pod (--recreate)…")
        t.pods.delete_pod(pod_id=pid)
        exists = False

    if exists:
        print(f"  [{pid}] updating…")
        t.pods.update_pod(**spec)
    else:
        print(f"  [{pid}] creating…")
        t.pods.create_pod(**spec)

    if start:
        try:
            t.pods.start_pod(pod_id=pid)
            print(f"  [{pid}] start requested")
        except Exception as exc:
            print(f"  [{pid}] start skipped: {exc}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Register stac-platform Tapis pods.")
    parser.add_argument("--base-url", default=os.environ.get("TAPIS_BASE_URL", "https://portals.tapis.io"))
    parser.add_argument("--owner", default=os.environ.get("GHCR_OWNER", "wmobley"))
    parser.add_argument("--image-tag", default="latest")
    parser.add_argument("--pods", choices=("both", "api", "postgres"), default="both")
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--no-start", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    _load_dotenv()
    specs = build_specs(args.owner, args.image_tag, args.base_url)
    urls = specs.pop("_urls")
    selected = ["postgres", "api"] if args.pods == "both" else [args.pods]

    leaked = sorted(k for k in SECRET_KEYS if os.environ.get(k))
    if leaked and "api" in selected:
        print("WARNING: these secrets will be stored in the stac-api pod env "
              "(visible to the pod owner): " + ", ".join(leaked) + "\n")

    if args.dry_run:
        for key in selected:
            spec = dict(specs[key])
            if "environment_variables" in spec:
                spec["environment_variables"] = {
                    k: ("***" if k in SECRET_KEYS else v)
                    for k, v in spec["environment_variables"].items()
                }
            print(f"--- {spec['pod_id']} ---")
            print(json.dumps(spec, indent=2))
        print(f"\nURL once running:\n  API: {urls['api']}")
        return 0

    try:
        from tapipy.tapis import Tapis
    except ImportError:
        raise SystemExit("tapipy is not installed (pip install tapipy).")

    username = os.environ.get("TAPIS_USERNAME") or input("Tapis username: ")
    password = os.environ.get("TAPIS_PASSWORD") or getpass("Tapis password: ")
    t = Tapis(base_url=args.base_url.rstrip("/"), username=username, password=password)
    t.get_tokens()

    for key in selected:
        upsert_pod(t, specs[key], recreate=args.recreate, start=not args.no_start)

    print("\nDone. Once started:")
    print(f"  API:      {urls['api']}   (health: {urls['api']}/healthz)")
    print( "  Postgres: stacpostgres.pods.<domain>:443  (then run `python -m pgstac.migrate`)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
