#!/usr/bin/env bash
# Entrypoint for the stac-publish Tapis batch app. Maps Tapis env-variable inputs
# + mounted file inputs into the `tasks.publish.publish` CLI.
#
# Required env:  COLLECTION, ITEM_ID, CKAN_URL, CKAN_TOKEN, CKAN_ORG, STAC_URL
# Optional env:  STAC_TOKEN, COLLECTION_TITLE, COLLECTION_DESCRIPTION
# File inputs (mounted into the job dir):
#   inputs/manifest.json   (required)   inputs/displacement.tif   inputs/overlay.png
set -euo pipefail

# Gate: when publishing isn't configured (no CKAN token or STAC URL), no-op so a
# pipeline can include this step unconditionally and just skip it on unconfigured runs.
if [ -z "${CKAN_TOKEN:-}" ] || [ -z "${STAC_URL:-}" ]; then
  echo "stac-publish: disabled (CKAN_TOKEN and/or STAC_URL unset) — skipping."
  exit 0
fi

: "${COLLECTION:?COLLECTION is required}"
: "${ITEM_ID:?ITEM_ID is required}"
: "${MANIFEST_PATH:=inputs/manifest.json}"

args=(--collection "$COLLECTION" --item-id "$ITEM_ID" --manifest "$MANIFEST_PATH")
[ -f "${COG_PATH:=inputs/displacement.tif}" ] && args+=(--cog "$COG_PATH")
[ -f "${OVERLAY_PATH:=inputs/overlay.png}" ] && args+=(--overlay "$OVERLAY_PATH")
[ -n "${COLLECTION_TITLE:-}" ] && args+=(--collection-title "$COLLECTION_TITLE")
[ -n "${COLLECTION_DESCRIPTION:-}" ] && args+=(--collection-description "$COLLECTION_DESCRIPTION")

# CKAN_*/STAC_* are read from the environment by stacmap.{ckan,stac_client}.
exec python -m tasks.publish.publish "${args[@]}"
