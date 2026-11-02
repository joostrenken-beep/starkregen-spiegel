// Nachladen der 1-km-Pakete (Gebäude, Fließwege, Senken) passend zum Kartenausschnitt.
// Jedes Paket wird nur einmal geladen; angezeigt werden die Pakete im Ausschnitt
// (plus Rand), damit auch auf dem Handy wenig im Speicher liegt.

const MAX_CACHE = 150;

export function createCells({ map, meta, version, onUpdate }) {
  const index = meta.dateien.zellen;           // key -> [w, s, e, n]
  const base = meta.dateien.zellen_url;
  const cache = new Map();                     // key -> Promise<data>
  const loaded = new Map();                    // key -> data
  let current = [];

  function visibleKeys(margin = 0.25) {
    const b = map.getBounds();
    const dx = (b.getEast() - b.getWest()) * margin;
    const dy = (b.getNorth() - b.getSouth()) * margin;
    const w = b.getWest() - dx; const e = b.getEast() + dx; const s = b.getSouth() - dy; const n = b.getNorth() + dy;
    return Object.entries(index).filter(([, bb]) => bb[2] >= w && bb[0] <= e && bb[3] >= s && bb[1] <= n).map(([k]) => k);
  }

  function load(key) {
    if (!index[key]) return Promise.resolve(null);
    if (!cache.has(key)) {
      cache.set(key, fetch(`${base}${key}.json${version}`).then((r) => r.json()).then((d) => {
        loaded.set(key, d);
        return d;
      }).catch(() => { cache.delete(key); return null; }));
    }
    return cache.get(key);
  }

  function trim(keep) {
    if (loaded.size <= MAX_CACHE) return;
    for (const k of loaded.keys()) {
      if (!keep.has(k)) { loaded.delete(k); cache.delete(k); }
      if (loaded.size <= MAX_CACHE) break;
    }
  }

  async function refresh(force = false) {
    if (map.getZoom() < meta.zellen_minzoom - 0.5) {
      if (current.length) { current = []; onUpdate([]); }
      return;
    }
    const keys = visibleKeys();
    if (keys.length > 60) return;               // zu weit herausgezoomt
    await Promise.all(keys.map(load));
    const same = keys.length === current.length && keys.every((k, i) => k === current[i]);
    if (same && !force) return;
    current = keys;
    trim(new Set(keys));
    onUpdate(keys.map((k) => loaded.get(k)).filter(Boolean));
  }

  map.on('moveend', () => refresh());
  return { refresh, load, loadedData: () => current.map((k) => loaded.get(k)).filter(Boolean) };
}

export function collect(cells, sid) {
  const fc = (features) => ({ type: 'FeatureCollection', features });
  return {
    gebaeude: fc(cells.flatMap((c) => c.g.features)),
    fliesswege: fc(cells.flatMap((c) => c.f[sid]?.features || [])),
    senken: fc(cells.flatMap((c) => c.s.features)),
  };
}
