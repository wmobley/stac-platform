// Leaflet map that renders a selected STAC Item's COG asset (public CKAN URL)
// with a viridis ramp, and reports viewport bbox changes for item search.
import { useEffect } from 'react'
import { MapContainer, TileLayer, useMap, useMapEvents } from 'react-leaflet'

import { cogHref, cogRange } from './stac'

const loadGeoraster = () => Promise.all([
  import('georaster').then((m) => m.default),
  import('georaster-layer-for-leaflet').then((m) => m.default),
])

const VIRIDIS = ['#440154', '#482878', '#3e4989', '#31688e', '#26828e',
  '#1f9e89', '#35b779', '#6ece58', '#b5de2b', '#fde725']
const hexToRgb = (h) => { const n = parseInt(h.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255] }
function viridis(t) {
  const x = Math.max(0, Math.min(1, t)) * (VIRIDIS.length - 1)
  const a = Math.floor(x), b = Math.min(a + 1, VIRIDIS.length - 1), f = x - a
  const ca = hexToRgb(VIRIDIS[a]), cb = hexToRgb(VIRIDIS[b])
  const c = ca.map((v, i) => Math.round(v + (cb[i] - v) * f))
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

function CogLayer({ item, onError }) {
  const map = useMap()
  const href = item && cogHref(item)
  const range = item && cogRange(item)
  useEffect(() => {
    if (!href) return undefined
    let layer, cancelled = false
    fetch(href)
      .then((r) => { if (!r.ok) throw new Error(`COG ${r.status}`); return r.arrayBuffer() })
      .then((buf) => Promise.all([buf, loadGeoraster()]))
      .then(([buf, [parse, GeoRasterLayer]]) => parse(buf).then((g) => [g, GeoRasterLayer]))
      .then(([g, GeoRasterLayer]) => {
        if (cancelled) return
        const min = range?.vmin ?? g.mins?.[0] ?? 0
        const max = range?.vmax ?? g.maxs?.[0] ?? 1
        const span = (max - min) || 1
        const noData = g.noDataValue
        layer = new GeoRasterLayer({
          georaster: g, opacity: 0.85, resolution: 256,
          pixelValuesToColorFn: (v) => {
            const x = v[0]
            return (x == null || Number.isNaN(x) || x === noData) ? null : viridis((x - min) / span)
          },
        })
        layer.addTo(map)
        try { map.fitBounds(layer.getBounds()) } catch { /* ignore */ }
      })
      .catch((e) => { if (!cancelled) onError?.(e.message) })
    return () => { cancelled = true; if (layer) map.removeLayer(layer) }
  }, [map, href]) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

function ViewportBridge({ onBbox }) {
  const report = (m) => {
    const b = m.getBounds()
    onBbox([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((n) => +n.toFixed(5)))
  }
  const map = useMapEvents({ moveend: (e) => report(e.target), zoomend: (e) => report(e.target) })
  useEffect(() => { report(map) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return null
}

export function MapView({ item, onBbox, onError }) {
  return (
    <MapContainer center={[29.7, -95.4]} zoom={6} style={{ height: '100%', width: '100%' }} scrollWheelZoom>
      <TileLayer attribution="&copy; OpenStreetMap" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <ViewportBridge onBbox={onBbox} />
      <CogLayer item={item} onError={onError} />
    </MapContainer>
  )
}
