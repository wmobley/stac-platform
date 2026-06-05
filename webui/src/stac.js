// Same-origin client for the STAC API (served under /api/v1 by stac_api).
// STAC responds with application/geo+json, so we use fetch directly.

const BASE = (import.meta.env?.VITE_STAC_API_BASE || '/api/v1').replace(/\/$/, '')

async function getJson(path) {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${path}`)
  return r.json()
}

export async function listCollections() {
  const d = await getJson('/collections')
  return d.collections || []
}

export async function getCollection(id) {
  return getJson(`/collections/${encodeURIComponent(id)}`)
}

// Items in a collection, optionally filtered by [w,s,e,n] bbox + datetime.
export async function listItems(collectionId, { bbox, datetime, limit = 50 } = {}) {
  const p = new URLSearchParams({ limit: String(limit) })
  if (bbox) p.set('bbox', bbox.join(','))
  if (datetime) p.set('datetime', datetime)
  const d = await getJson(`/collections/${encodeURIComponent(collectionId)}/items?${p}`)
  return d.features || []
}

// Register (write) a Collection or Item via the Transactions extension.
// `token` is the user's Tapis access token (validated server-side).
export async function register({ kind, body, token }) {
  const isItem = kind === 'item'
  const path = isItem
    ? `/collections/${encodeURIComponent(body.collection)}/items`
    : '/collections'
  const r = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  const text = await r.text()
  let detail = text
  try { detail = JSON.parse(text).detail || text } catch { /* keep text */ }
  if (!r.ok) throw new Error(`${r.status}: ${detail}`)
  return { status: r.status }
}

// Asset helpers (keys from stacmap/assets.py: cog / overlay / metadata / source).
export const cogHref = (it) => it?.assets?.cog?.href || null
export const overlayHref = (it) => it?.assets?.overlay?.href || null
export const cogRange = (it) => {
  const s = it?.assets?.cog?.['raster:bands']?.[0]?.statistics
  return s ? { vmin: s.minimum, vmax: s.maximum } : null
}
