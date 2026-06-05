import { useEffect, useState } from 'react'

import { MapView } from './MapView.jsx'
import {
  listCollections, listItems, register, overlayHref, cogHref,
} from './stac'

const TOKEN_KEY = 'stac.tapisToken'

export function App() {
  const [collections, setCollections] = useState([])
  const [activeColl, setActiveColl] = useState(null)
  const [items, setItems] = useState([])
  const [activeItem, setActiveItem] = useState(null)
  const [bbox, setBbox] = useState(null)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('browse')   // 'browse' | 'register'

  useEffect(() => {
    listCollections().then(setCollections).catch((e) => setError(e.message))
  }, [])

  // (Re)load items when a collection is picked or the viewport moves.
  useEffect(() => {
    if (!activeColl) return
    listItems(activeColl, { bbox }).then(setItems).catch((e) => setError(e.message))
  }, [activeColl, bbox])

  return (
    <div style={S.app}>
      <aside style={S.side}>
        <h1 style={S.h1}>TACC STAC</h1>
        <nav style={S.tabs}>
          <button style={S.tab(tab === 'browse')} onClick={() => setTab('browse')}>Browse</button>
          <button style={S.tab(tab === 'register')} onClick={() => setTab('register')}>Register</button>
        </nav>
        {error && <div style={S.err}>{error}</div>}
        {tab === 'browse'
          ? <Browse {...{ collections, activeColl, setActiveColl, items, activeItem, setActiveItem }} />
          : <Register />}
      </aside>
      <main style={S.main}>
        <MapView item={activeItem} onBbox={setBbox} onError={setError} />
        {activeItem && <ItemDetail item={activeItem} onClose={() => setActiveItem(null)} />}
      </main>
    </div>
  )
}

function Browse({ collections, activeColl, setActiveColl, items, activeItem, setActiveItem }) {
  return (
    <>
      <h2 style={S.h2}>Collections ({collections.length})</h2>
      <ul style={S.list}>
        {collections.map((c) => (
          <li key={c.id}>
            <button style={S.row(activeColl === c.id)} onClick={() => { setActiveColl(c.id); setActiveItem(null) }}>
              {c.title || c.id}
            </button>
          </li>
        ))}
        {!collections.length && <li style={S.muted}>No collections yet.</li>}
      </ul>
      {activeColl && (
        <>
          <h2 style={S.h2}>Items in view ({items.length})</h2>
          <ul style={S.list}>
            {items.map((it) => (
              <li key={it.id}>
                <button style={S.row(activeItem?.id === it.id)} onClick={() => setActiveItem(it)}>
                  {it.id}
                </button>
              </li>
            ))}
            {!items.length && <li style={S.muted}>No items in the current map view.</li>}
          </ul>
        </>
      )}
    </>
  )
}

function ItemDetail({ item, onClose }) {
  const ov = overlayHref(item)
  const p = item.properties || {}
  const when = p.datetime || `${p.start_datetime || ''} … ${p.end_datetime || ''}`
  return (
    <div style={S.detail}>
      <button style={S.close} onClick={onClose}>×</button>
      <div style={{ fontWeight: 600 }}>{item.id}</div>
      <div style={S.muted}>{when}</div>
      {ov && <img src={ov} alt="overlay" style={{ width: '100%', marginTop: 6, borderRadius: 4 }} />}
      <div style={{ marginTop: 6, fontSize: 12 }}>
        assets: {Object.keys(item.assets || {}).join(', ') || '—'}
        {cogHref(item) && <div style={S.muted}>COG rendered on map.</div>}
      </div>
    </div>
  )
}

function Register() {
  const [token, setToken] = useState(sessionStorage.getItem(TOKEN_KEY) || '')
  const [kind, setKind] = useState('item')
  const [json, setJson] = useState('{\n  "type": "Feature",\n  "stac_version": "1.0.0",\n  "id": "demo-1",\n  "collection": "subsidence-rates",\n  "geometry": {"type": "Point", "coordinates": [-95.4, 29.6]},\n  "bbox": [-95.4, 29.6, -95.4, 29.6],\n  "properties": {"datetime": "2024-06-01T00:00:00Z"},\n  "assets": {}, "links": []\n}')
  const [result, setResult] = useState(null)

  const saveToken = (v) => { setToken(v); sessionStorage.setItem(TOKEN_KEY, v) }
  const submit = async () => {
    setResult(null)
    let body
    try { body = JSON.parse(json) } catch (e) { setResult({ ok: false, msg: `Invalid JSON: ${e.message}` }); return }
    try {
      const r = await register({ kind, body, token })
      setResult({ ok: true, msg: `OK (${r.status})` })
    } catch (e) { setResult({ ok: false, msg: e.message }) }
  }

  return (
    <div>
      <h2 style={S.h2}>Register</h2>
      <label style={S.label}>Tapis token</label>
      <input style={S.input} type="password" value={token} placeholder="paste your Tapis access token"
             onChange={(e) => saveToken(e.target.value)} />
      <label style={S.label}>Kind</label>
      <select style={S.input} value={kind} onChange={(e) => setKind(e.target.value)}>
        <option value="item">Item</option>
        <option value="collection">Collection</option>
      </select>
      <label style={S.label}>{kind === 'item' ? 'Item' : 'Collection'} JSON</label>
      <textarea style={S.textarea} value={json} onChange={(e) => setJson(e.target.value)} rows={14} />
      <button style={S.submit} onClick={submit}>POST</button>
      {result && <div style={result.ok ? S.ok : S.err}>{result.msg}</div>}
      <p style={S.muted}>Reads are public; writes require a valid Tapis token (any tenant user).</p>
    </div>
  )
}

const S = {
  app: { display: 'flex', height: '100vh', font: '14px system-ui, sans-serif' },
  side: { width: 320, padding: 12, overflow: 'auto', borderRight: '1px solid #ddd', boxSizing: 'border-box' },
  main: { flex: 1, position: 'relative' },
  h1: { fontSize: 18, margin: '0 0 8px' },
  h2: { fontSize: 13, textTransform: 'uppercase', color: '#555', margin: '14px 0 6px' },
  tabs: { display: 'flex', gap: 6, marginBottom: 8 },
  tab: (on) => ({ flex: 1, padding: '6px 8px', cursor: 'pointer', border: '1px solid #ccc', borderRadius: 4, background: on ? '#1f6feb' : '#fff', color: on ? '#fff' : '#333' }),
  list: { listStyle: 'none', margin: 0, padding: 0 },
  row: (on) => ({ display: 'block', width: '100%', textAlign: 'left', border: 'none', padding: '6px 8px', cursor: 'pointer', borderRadius: 4, background: on ? '#e6f0ff' : 'transparent', font: 'inherit' }),
  muted: { color: '#888', fontSize: 12, padding: '2px 0' },
  err: { color: '#b00020', fontSize: 12, padding: '6px 0' },
  ok: { color: '#0a7d28', fontSize: 12, padding: '6px 0' },
  detail: { position: 'absolute', top: 12, right: 12, width: 280, background: 'rgba(255,255,255,0.96)', padding: 10, borderRadius: 6, boxShadow: '0 1px 6px rgba(0,0,0,0.3)', zIndex: 1000 },
  close: { position: 'absolute', top: 4, right: 6, border: 'none', background: 'none', fontSize: 18, cursor: 'pointer' },
  label: { display: 'block', fontSize: 12, color: '#555', margin: '8px 0 2px' },
  input: { width: '100%', padding: 6, boxSizing: 'border-box', border: '1px solid #ccc', borderRadius: 4 },
  textarea: { width: '100%', padding: 6, boxSizing: 'border-box', border: '1px solid #ccc', borderRadius: 4, fontFamily: 'monospace', fontSize: 12 },
  submit: { marginTop: 8, padding: '8px 12px', background: '#1f6feb', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' },
}
