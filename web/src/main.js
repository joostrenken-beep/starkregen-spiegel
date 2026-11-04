// Starkregen-Spiegel – Kartenanwendung (Stadtgebiet, Kachel- und Paketdaten)
import maplibregl from 'maplibre-gl';
import { Protocol } from 'pmtiles';
import { createCells, collect } from './cells.js';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import { createSearch } from './search.js';
import { renderBuilding, renderSink } from './info.js';
import { buildingPdf, mapPdf } from './pdf.js';

const DATA = './data/';
const BASEMAP =
  'https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_grau/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png';
const AMPEL = { 0: '#3a9d5d', 1: '#f2b705', 2: '#d7301f' };
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const state = { meta: null, sid: null, view: 'gef', classes: new Set([1, 2, 3, 4]), buildings: new Map(), selected: null, v: '', cells: null,
  sb: new Map(), sbCur: undefined, einzug: false, is3d: false, map: null, mk: null };
const MARK_ROT = '#e3001b';
export const RISK = ['#3a9d5d', '#f2b705', '#e8590c', '#a50f15'];
export const RISK_TXT = ['gering', 'mäßig', 'hoch', 'sehr hoch'];
const GEF = ['#3a9d5d', '#f2b705', '#e8590c', '#a50f15'];
const SK = { 1: '#9e9e9e', 2: '#9575cd', 3: '#673ab7', 4: '#311b92' };
const SK_TXT = { 1: 'S1 gering – Garage, Schuppen, Nebengebäude', 2: 'S2 mäßig – Wohngebäude',
  3: 'S3 hoch – Gewerbe, Handel, Verwaltung, Versammlung', 4: 'S4 sehr hoch – Schule, Kita, Pflege, Einsatzkräfte, unterirdisch' };

function store(key, val) {
  try {
    if (val === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, val);
  } catch { return null; }
  return null;
}

const abs = (u) => new URL(u, window.location.href).href;
const EMPTY = { type: 'FeatureCollection', features: [] };

async function main() {
  const meta = await fetch(DATA + 'meta.json', { cache: 'no-store' }).then((r) => r.json());
  state.meta = meta;
  state.v = `?v=${meta.version || meta.stand}`;
  document.getElementById('stand').textContent = meta.stand;
  const scen = meta.szenarien;
  state.sid = scen[Math.min(1, scen.length - 1)].id;
  // Markierung: höchstes Schutzgut mit ≥ 30 cm am Haus bei T ≈ 30 a (Szenario aus den Daten)
  state.mk = { szenario: 'p30', gefaehrdung_ab: 2, schutzklasse: 4, ...(meta.markierung || {}) };
  state.mk.sc = scen.find((x) => x.id === state.mk.szenario);

  const protocol = new Protocol();
  maplibregl.addProtocol('pmtiles', protocol.tile);

  const [w, s, e, n] = meta.gebiet.bounds;
  const map = new maplibregl.Map({
    container: 'map',
    style: {
      version: 8,
      sources: {
        basemap: {
          type: 'raster', tiles: [BASEMAP], tileSize: 256, maxzoom: 18,
          attribution: '© <a href="https://basemap.de" target="_blank">basemap.de / BKG</a> | DGM1, ALKIS © LGLN',
        },
      },
      layers: [{ id: 'basemap', type: 'raster', source: 'basemap' }],
    },
    bounds: [[w, s], [e, n]],
    fitBoundsOptions: { padding: 20 },
    maxBounds: [[w - 0.08, s - 0.05], [e + 0.08, n + 0.05]],
    maxZoom: 20,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  window.__map = map; // für Tests
  state.map = map;

  map.on('load', async () => {
    map.addSource('gebiet', { type: 'geojson', data: abs(meta.dateien.umriss + state.v) });
    map.addLayer({ id: 'gebiet', type: 'line', source: 'gebiet',
      paint: { 'line-color': '#0b2f7a', 'line-width': 1.5, 'line-dasharray': [3, 2], 'line-opacity': 0.6 } });

    map.addSource('senken', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'senken', type: 'fill', source: 'senken', layout: { visibility: 'none' },
      paint: { 'fill-color': ['interpolate', ['linear'], ['get', 'tmax'], 0.1, '#e9d8f5', 0.5, '#b07cd6', 1.5, '#5b2a86'],
        'fill-opacity': 0.55, 'fill-outline-color': '#5b2a86' } });

    // Wassertiefe: je Szenario eine Kachelpyramide (PMTiles), sichtbar ist nur das gewählte
    for (const sc of scen) {
      map.addSource(`tiefe_${sc.id}`, { type: 'raster', url: `pmtiles://${abs(meta.dateien.tiefe_pmtiles[sc.id] + state.v)}`, tileSize: 256 });
      map.addLayer({ id: `tiefe_${sc.id}`, type: 'raster', source: `tiefe_${sc.id}`,
        layout: { visibility: sc.id === state.sid ? 'visible' : 'none' },
        paint: { 'raster-opacity': 0.9, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 } });
    }

    // Vergleichsebene: amtliche Hinweiskarte Starkregengefahren (WMS des BKG)
    map.addSource('hwk', { type: 'raster', tileSize: 256, attribution: 'Hinweiskarte Starkregen © BKG/NLWKN',
      tiles: ['https://sgx.geodatenzentrum.de/wms_starkregen?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap'
        + '&LAYERS=ni_tiefe_extrem&STYLES=&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256'
        + '&FORMAT=image/png&TRANSPARENT=TRUE'] });
    map.addLayer({ id: 'hwk', type: 'raster', source: 'hwk', layout: { visibility: 'none' },
      paint: { 'raster-opacity': 0.75 } });

    // Einzugsgebiet des gewählten Gebäudes ("Woher kommt das Wasser?")
    map.addSource('einzug', { type: 'geojson', data: EMPTY });
    map.addLayer({ id: 'einzug', type: 'fill', source: 'einzug', paint: { 'fill-color': '#0a6fb5', 'fill-opacity': 0.18 } });
    map.addLayer({ id: 'einzug-linie', type: 'line', source: 'einzug',
      paint: { 'line-color': '#0a4f8a', 'line-width': 2, 'line-dasharray': [2, 1.5] } });

    map.addSource('gebaeude', { type: 'geojson', data: EMPTY, promoteId: 'id' });
    map.addLayer({ id: 'gebaeude', type: 'fill', source: 'gebaeude', minzoom: 13,
      paint: { 'fill-color': ampelColor(state.sid), 'fill-opacity': ampelOpacity(state.sid) } });
    map.addLayer({ id: 'gebaeude-s4', type: 'line', source: 'gebaeude', minzoom: 13,
      filter: ['==', ['get', 's'], 4], layout: { visibility: 'none' },
      paint: { 'line-color': '#6a1b9a', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 1.5, 18, 3.5] } });
    map.addLayer({ id: 'gebaeude-markiert-saum', type: 'line', source: 'gebaeude', minzoom: 13, filter: markFilter(),
      paint: { 'line-color': '#ffffff', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 4, 18, 9] } });
    map.addLayer({ id: 'gebaeude-markiert', type: 'line', source: 'gebaeude', minzoom: 13, filter: markFilter(),
      paint: { 'line-color': MARK_ROT, 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 2, 18, 5] } });
    map.addLayer({ id: 'gebaeude-linie', type: 'line', source: 'gebaeude', minzoom: 15,
      paint: { 'line-color': '#444', 'line-width': 0.4, 'line-opacity': 0.6 } });
    map.addLayer({ id: 'gebaeude-auswahl', type: 'line', source: 'gebaeude',
      paint: { 'line-color': '#111', 'line-width': ['case', ['boolean', ['feature-state', 'sel'], false], 3, 0] } });
    map.addLayer({ id: 'gebaeude-3d', type: 'fill-extrusion', source: 'gebaeude', minzoom: 13, layout: { visibility: 'none' },
      paint: { 'fill-extrusion-color': extrusionColor(state.sid), 'fill-extrusion-height': ['coalesce', ['get', 'h'], 6],
        'fill-extrusion-base': 0, 'fill-extrusion-opacity': 0.92 } });

    map.addSource('fliesswege', { type: 'geojson', data: EMPTY });
    const width = ['interpolate', ['linear'], ['ln', ['max', ['get', 'v'], 1]], 3.4, 1, 6.9, 2.5, 9.2, 5];
    map.addLayer({ id: 'fliesswege', type: 'line', source: 'fliesswege',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#0a6fb5', 'line-width': width, 'line-opacity': 0.35 } });
    map.addLayer({ id: 'fliesswege-anim', type: 'line', source: 'fliesswege',
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: { 'line-color': '#00c2ff', 'line-width': width, 'line-dasharray': [0, 4, 3] } });
    if (!reducedMotion) animateFlow(map);

    // Übersicht der markierten Gebäude (Punkte, solange Gebäude noch nicht geladen sind)
    map.addSource('markiert', { type: 'geojson', data: meta.markierung?.datei ? abs(meta.markierung.datei + state.v) : EMPTY });
    map.addLayer({ id: 'markiert-punkte', type: 'circle', source: 'markiert', maxzoom: 14,
      paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 4, 13.9, 7], 'circle-color': '#ffffff',
        'circle-stroke-color': MARK_ROT, 'circle-stroke-width': 3 } });

    // 1-km-Pakete nachladen
    state.cells = createCells({ map, meta, version: state.v, onUpdate: (cells) => showCells(map, cells) });
    const hint = document.getElementById('zoom-hint');
    const updHint = () => { hint.hidden = map.getZoom() >= meta.zellen_minzoom - 0.5; };
    map.on('zoomend', updHint);
    updHint();

    setupLayerPanel(map);
    setupViewSwitch(map);
    setupScenario(map);
    setupClicks(map);
    const adr = await fetch(abs(meta.dateien.adressen + state.v)).then((r) => r.json());
    createSearch({ map, addresses: adr, bounds: meta.gebiet.bounds, onSelect: (hit) => selectBuilding(map, hit.id, hit) });
    setupLocate(map);
    setup3d(map);
    setupExport(map);
    state.cells.refresh(true);
    document.body.classList.add('ready');
  });

  setupDisclaimer();
  setupBarToggle();
  setupSheet(map);
  if ('serviceWorker' in navigator && import.meta.env.PROD) {
    navigator.serviceWorker.register('./sw.js').catch(() => {});
  }
}

function showCells(map, cells) {
  const d = collect(cells, state.sid);
  state.buildings = new Map(d.gebaeude.features.map((f) => [f.properties.id, f]));
  map.getSource('gebaeude').setData(d.gebaeude);
  map.getSource('fliesswege').setData(d.fliesswege);
  map.getSource('senken').setData(d.senken);
  if (state.selected != null && state.buildings.has(state.selected)) {
    map.setFeatureState({ source: 'gebaeude', id: state.selected }, { sel: true });
  }
}

function ampelColor(sid) {
  if (state.view === 'risiko') return ['match', ['get', `r_${sid}`], 3, RISK[3], 2, RISK[2], 1, RISK[1], RISK[0]];
  if (state.view === 'schaden') return ['match', ['get', 's'], 4, SK[4], 3, SK[3], 2, SK[2], SK[1]];
  return ['match', ['get', `g_${sid}`], 3, GEF[3], 2, GEF[2], 1, GEF[1], GEF[0]];
}
function markFilter() {
  const m = state.mk;
  return ['all', ['==', ['get', 's'], m.schutzklasse], ['>=', ['get', `g_${m.szenario}`], m.gefaehrdung_ab]];
}
function isMarked(p) {
  const m = state.mk;
  return p.s === m.schutzklasse && (p[`g_${m.szenario}`] ?? 0) >= m.gefaehrdung_ab
    ? { mm: m.sc?.mm ?? 30, jaehrlichkeit: m.sc?.jaehrlichkeit } : null;
}

function extrusionColor(sid) {
  return ['case', ['boolean', ['feature-state', 'sel'], false], '#3b4a5c', ampelColor(sid)];
}
function ampelOpacity(sid) {
  if (state.view === 'risiko') return ['match', ['get', `r_${sid}`], 3, 0.9, 2, 0.85, 1, 0.75, 0.3];
  if (state.view === 'schaden') return ['match', ['get', 's'], 1, 0.45, 0.85];
  return ['match', ['get', `g_${sid}`], 3, 0.9, 2, 0.85, 1, 0.75, 0.35];
}

function classFilter() {
  const sel = [...state.classes];
  return sel.length === 4 ? null : ['in', ['coalesce', ['get', 's'], 2], ['literal', sel]];
}

function applyBuildingStyle(map) {
  map.setPaintProperty('gebaeude', 'fill-color', ampelColor(state.sid));
  map.setPaintProperty('gebaeude', 'fill-opacity', ampelOpacity(state.sid));
  const f = classFilter();
  map.setFilter('gebaeude', f);
  map.setFilter('gebaeude-linie', f);
  if (map.getLayer('gebaeude-3d')) {
    map.setPaintProperty('gebaeude-3d', 'fill-extrusion-color', extrusionColor(state.sid));
    map.setFilter('gebaeude-3d', f);
  }
  const s4 = ['==', ['get', 's'], 4];
  map.setFilter('gebaeude-s4', state.classes.has(4) ? s4 : ['==', 1, 0]);
  updateBuildingVisibility(map);
  renderLegend();
}

function setupViewSwitch(map) {
  document.querySelectorAll('.view-switch button').forEach((b) => b.addEventListener('click', () => {
    state.view = b.dataset.view;
    document.querySelectorAll('.view-switch button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    applyBuildingStyle(map);
    renderSelected();
  }));
  document.querySelectorAll('.class-filter button').forEach((b) => b.addEventListener('click', () => {
    const k = +b.dataset.s;
    if (state.classes.has(k) && state.classes.size > 1) state.classes.delete(k); else state.classes.add(k);
    document.querySelectorAll('.class-filter button').forEach((x) =>
      x.setAttribute('aria-pressed', String(state.classes.has(+x.dataset.s))));
    applyBuildingStyle(map);
  }));
  applyBuildingStyle(map);
}

// Animierte Strichelung in Fließrichtung (Linien sind in Fließrichtung digitalisiert)
function animateFlow(map) {
  const seq = [[0, 4, 3], [0.5, 4, 2.5], [1, 4, 2], [1.5, 4, 1.5], [2, 4, 1], [2.5, 4, 0.5], [3, 4, 0],
    [0, 0.5, 3, 3.5], [0, 1, 3, 3], [0, 1.5, 3, 2.5], [0, 2, 3, 2], [0, 2.5, 3, 1.5], [0, 3, 3, 1], [0, 3.5, 3, 0.5]];
  let step = -1;
  function frame(ts) {
    const s = Math.floor((ts / 70) % seq.length);
    if (s !== step && !document.hidden && map.getLayer('fliesswege-anim')
      && map.getLayoutProperty('fliesswege-anim', 'visibility') !== 'none') {
      map.setPaintProperty('fliesswege-anim', 'line-dasharray', seq[s]);
      step = s;
    }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

function setupLayerPanel(map) {
  const btn = document.getElementById('layers-btn');
  const panel = document.getElementById('layers');
  btn.addEventListener('click', () => {
    panel.hidden = !panel.hidden;
    btn.setAttribute('aria-expanded', String(!panel.hidden));
  });
  const groups = { tiefe: state.meta.szenarien.map((x) => `tiefe_${x.id}`), fliesswege: ['fliesswege', 'fliesswege-anim'], senken: ['senken'],
    gebaeude: ['gebaeude', 'gebaeude-linie', 'gebaeude-s4'], hwk: ['hwk'] };
  panel.querySelectorAll('input[data-layer]').forEach((cb) => {
    cb.addEventListener('change', () => {
      if (cb.dataset.layer === 'hwk') document.getElementById('hwk-hinweis').hidden = !cb.checked;
      if (cb.dataset.layer === 'gebaeude') { updateBuildingVisibility(map); return; }
      groups[cb.dataset.layer].forEach((id) => {
        const on = cb.checked && (cb.dataset.layer !== 'tiefe' || id === `tiefe_${state.sid}`);
        map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
      });
    });
  });
  renderLegend();
}

function renderLegend() {
  const m = state.meta;
  document.getElementById('legend').innerHTML = `
    <h3>Max. Wassertiefe</h3>
    ${m.tiefenklassen.map((k, i) => {
      const next = m.tiefenklassen[i + 1];
      const txt = next ? `${Math.round(k.ab_m * 100)}–${Math.round(next.ab_m * 100)} cm` : `über ${Math.round(k.ab_m * 100)} cm`;
      return `<div class="lg"><span class="sw" style="background:${k.farbe}"></span>${txt}</div>`;
    }).join('')}
    ${legendBuildings(m)}
    <h3>Fließwege</h3>
    <div class="lg"><span class="sw line"></span>Richtung = Bewegung, Breite = Wassermenge</div>`;
}

function legendBuildings(m) {
  const sw = (c, t) => `<div class="lg"><span class="sw" style="background:${c}"></span>${t}</div>`;
  const s4 = '<div class="lg"><span class="sw s4"></span>Umrandung: Schadenspotenzial S4</div>'
    + `<div class="lg"><span class="sw mk"></span>Roter Rahmen: S4 mit ≥ 30 cm am Haus schon bei ${state.mk.sc?.mm ?? 30} mm `
    + `(${state.mk.sc?.jaehrlichkeit || 'T ≈ 30 a'})</div>`;
  const filt = state.classes.size < 4 ? `<div class="lg small">gezeigt: ${[...state.classes].sort().map((k) => `S${k}`).join(', ')}</div>` : '';
  if (state.view === 'schaden') {
    return `<h3>Schadenspotenzial (Gebäudeart, unabhängig vom Regen)</h3>${[4, 3, 2, 1].map((k) => sw(SK[k], SK_TXT[k])).join('')}`
      + `<div class="lg"><span class="sw mk"></span>Roter Rahmen: S4 mit ≥ 30 cm am Haus bei ${state.mk.sc?.mm ?? 30} mm</div>${filt}`;
  }
  if (state.view === 'risiko') {
    return `<h3>Risiko = Wasser am Gebäude × Schadenspotenzial</h3>${[3, 2, 1, 0].map((k) => sw(RISK[k], RISK_TXT[k])).join('')}${s4}${filt}`;
  }
  const g = m.risiko?.gefaehrdung_grenzen_m || [0.1, 0.3, 0.5];
  return `<h3>Wasser am Gebäude</h3>
    ${sw(GEF[3], `ab ${g[2] * 100} cm`)}${sw(GEF[2], `${g[1] * 100}–${g[2] * 100} cm`)}${sw(GEF[1], `${g[0] * 100}–${g[1] * 100} cm`)}${sw(GEF[0], `unter ${g[0] * 100} cm`)}${s4}${filt}`;
}

function setupScenario(map) {
  const scen = state.meta.szenarien;
  const range = document.getElementById('rain');
  const lbl = document.getElementById('rain-label');
  range.max = String(scen.length - 1);
  range.value = String(scen.findIndex((x) => x.id === state.sid));
  document.getElementById('rain-ticks-labels').innerHTML = scen.map((x) => `<span>${x.mm}</span>`).join('');
  lbl.textContent = `${scen[+range.value].mm} mm`;
  const jt = document.getElementById('rain-jt');
  jt.textContent = scen[+range.value].jaehrlichkeit ? `(${scen[+range.value].jaehrlichkeit}, KOSTRA-DWD-2020)` : '';
  range.addEventListener('input', async () => {
    const sc = scen[+range.value];
    lbl.textContent = `${sc.mm} mm`;
    jt.textContent = sc.jaehrlichkeit ? `(${sc.jaehrlichkeit}, KOSTRA-DWD-2020)` : '';
    state.sid = sc.id;
    const tiefeAn = document.querySelector('input[data-layer=tiefe]').checked;
    scen.forEach((x) => map.setLayoutProperty(`tiefe_${x.id}`, 'visibility', tiefeAn && x.id === sc.id ? 'visible' : 'none'));
    applyBuildingStyle(map);
    map.getSource('fliesswege').setData(collect(state.cells.loadedData(), sc.id).fliesswege);
    renderSelected();
  });
}

function setupClicks(map) {
  map.on('click', (e) => {
    const mp = map.queryRenderedFeatures(e.point, { layers: ['markiert-punkte'] })[0];
    if (mp) {
      const q = mp.properties;
      const [lon, lat] = mp.geometry.coordinates;
      return selectBuilding(map, q.id, { lon, lat, cell: q.c, label: q.a || q.art });
    }
    const hits = map.queryRenderedFeatures(e.point, { layers: ['gebaeude', 'gebaeude-3d', 'senken'].filter((l) => map.getLayer(l)) });
    const b = hits.find((h) => h.layer.id.startsWith('gebaeude'));
    if (b) return selectBuilding(map, b.id, null, false);
    const sk = hits.find((h) => h.layer.id === 'senken');
    if (sk) return showSheet(renderSink(sk.properties, state.meta, state.sid));
    clearSelection(map);
    hideSheet();
  });
  map.on('mouseenter', 'gebaeude', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseenter', 'gebaeude-3d', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseenter', 'markiert-punkte', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'markiert-punkte', () => { map.getCanvas().style.cursor = ''; });
  map.on('mouseleave', 'gebaeude-3d', () => { map.getCanvas().style.cursor = ''; });
  map.on('mouseleave', 'gebaeude', () => { map.getCanvas().style.cursor = ''; });
}

function clearSelection(map) {
  if (state.selected != null) map.setFeatureState({ source: 'gebaeude', id: state.selected }, { sel: false });
  state.selected = null;
  state.einzug = false;
  state.sbCur = undefined;
  map.getSource('einzug')?.setData(EMPTY);
}

async function selectBuilding(map, id, hit, fly = true) {
  clearSelection(map);
  let f = state.buildings.get(id);
  if (!f && hit?.cell) {
    const d = await state.cells.load(hit.cell);
    f = d?.g.features.find((x) => x.properties.id === id);
  }
  if (!f) {
    showSheet(`<h2>${hit ? hit.label : 'Adresse'}</h2><p>Für diese Adresse liegt kein berechnetes Gebäude vor.</p>`);
  } else {
    state.selected = id;
    if (state.buildings.has(id)) map.setFeatureState({ source: 'gebaeude', id }, { sel: true });
    else state.buildings.set(id, f);
    state.sbCur = undefined;
    renderSelected();
    loadSteckbrief(f).then((rec) => {
      if (state.selected !== id) return;
      state.sbCur = rec;
      renderSelected();
    });
  }
  if (fly && hit) {
    // Karte so verschieben, dass das Gebäude nicht unter dem Info-Fenster liegt
    const sheet = document.getElementById('sheet');
    const wide = window.innerWidth >= 760;
    const padding = wide ? { right: sheet.offsetWidth + 20, top: 0, bottom: 0, left: 0 }
      : { bottom: sheet.offsetHeight, top: 70, left: 0, right: 0 };
    map.flyTo({ center: [hit.lon, hit.lat], zoom: 18, speed: 1.6, padding });
  }
}

function setupSheet(map) {
  document.querySelector('.sheet-close').addEventListener('click', () => { hideSheet(); clearSelection(map); });
  document.getElementById('sheet-body').addEventListener('click', async (e) => {
    const pdfBtn = e.target.closest('[data-action=pdf]');
    if (pdfBtn) {
      const f = state.buildings.get(state.selected);
      if (!f) return;
      await busy(pdfBtn, 'PDF wird erstellt …', () => buildingPdf({ f, meta: state.meta, sid: state.sid,
        sb: state.sbCur, map, markiert: isMarked(f.properties) }));
      return;
    }
    if (!e.target.closest('[data-action=einzug]')) return;
    state.einzug = !state.einzug;
    showEinzug(map);
    renderSelected();
  });
}

// Steckbrief-Pakete (je 1-km-Zelle) erst bei Bedarf laden
function loadSteckbrief(f) {
  const key = f.properties.c;
  const base = state.meta.dateien.steckbrief_url;
  if (!key || !base) return Promise.resolve(null);
  if (!state.sb.has(key)) {
    state.sb.set(key, fetch(abs(`${base}${key}.json${state.v}`)).then((r) => (r.ok ? r.json() : {}))
      .catch(() => { state.sb.delete(key); return {}; }));
  }
  return state.sb.get(key).then((d) => d[String(f.properties.id)] || null);
}

function renderSelected() {
  if (state.selected == null) return;
  const f = state.buildings.get(state.selected);
  if (!f) return;
  showSheet(renderBuilding(f, state.meta, state.sid, state.view,
    { sb: state.sbCur, einzug: state.einzug, markiert: isMarked(f.properties) }));
}

async function busy(btn, txt, fn) {
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = txt;
  try { await fn(); } catch (err) { console.error(err); alert('Das PDF konnte nicht erstellt werden.'); }
  btn.disabled = false;
  btn.textContent = old;
}

const VIEW_TITEL = { gef: 'Ansicht: Wasser am Haus', schaden: 'Ansicht: Schadenspotenzial', risiko: 'Ansicht: Risiko' };

function legendItems() {
  const m = state.meta;
  const g = m.risiko?.gefaehrdung_grenzen_m || [0.1, 0.3, 0.5];
  const tiefe = m.tiefenklassen.map((k, i) => {
    const next = m.tiefenklassen[i + 1];
    return { farbe: k.farbe, text: next ? `${Math.round(k.ab_m * 100)}-${Math.round(next.ab_m * 100)} cm` : `über ${Math.round(k.ab_m * 100)} cm` };
  });
  let geb;
  if (state.view === 'schaden') geb = { titel: 'Schadenspotenzial', eintraege: [4, 3, 2, 1].map((k) => ({ farbe: SK[k], text: SK_TXT[k] })) };
  else if (state.view === 'risiko') geb = { titel: 'Risiko (Wasser × Schadenspotenzial)', eintraege: [3, 2, 1, 0].map((k) => ({ farbe: RISK[k], text: RISK_TXT[k] })) };
  else {
    geb = { titel: 'Wasser am Gebäude', eintraege: [
      { farbe: GEF[3], text: `ab ${g[2] * 100} cm` }, { farbe: GEF[2], text: `${g[1] * 100}-${g[2] * 100} cm` },
      { farbe: GEF[1], text: `${g[0] * 100}-${g[1] * 100} cm` }, { farbe: GEF[0], text: `unter ${g[0] * 100} cm` }] };
  }
  geb.eintraege.push({ farbe: MARK_ROT, rahmen: true,
    text: `Roter Rahmen: S4 mit >= 30 cm am Haus bei ${state.mk.sc?.mm ?? 30} mm (${state.mk.sc?.jaehrlichkeit || 'T ca. 30 a'})` });
  return [{ titel: 'Max. Wassertiefe', eintraege: tiefe }, geb,
    { titel: 'Fließwege', eintraege: [{ farbe: '#0a6fb5', text: 'Hauptfließwege (Breite = Wassermenge)' }] }];
}

function setupExport(map) {
  const btn = document.getElementById('pdf-karte');
  btn.addEventListener('click', () => busy(btn, 'PDF wird erstellt …', () =>
    mapPdf({ map, meta: state.meta, sid: state.sid, viewTitle: VIEW_TITEL[state.view], legend: legendItems() })));
}

// Bedienfeld unten ein-/ausklappen (Zustand je Gerät merken)
function setupBarToggle() {
  const bar = document.querySelector('.bottombar');
  const btn = document.getElementById('bar-toggle');
  const set = (c) => {
    bar.classList.toggle('collapsed', c);
    btn.setAttribute('aria-expanded', String(!c));
    btn.textContent = c ? '▴' : '▾';
    btn.title = c ? 'Bedienfeld ausklappen' : 'Bedienfeld einklappen';
  };
  set(store('leiste-zu') === '1');
  btn.addEventListener('click', () => {
    const c = !bar.classList.contains('collapsed');
    set(c);
    store('leiste-zu', c ? '1' : '0');
  });
}

function showEinzug(map) {
  const g = state.sbCur?.e?.g;
  if (!state.einzug || !g) { map.getSource('einzug').setData(EMPTY); return; }
  map.getSource('einzug').setData({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: g } });
  let [w, s, e, n] = [180, 90, -180, -90];
  for (const [x, y] of g[0]) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y); }
  const sheet = document.getElementById('sheet');
  const wide = window.innerWidth >= 760;
  const padding = wide ? { right: sheet.offsetWidth + 40, top: 40, bottom: 40, left: 40 }
    : { bottom: sheet.offsetHeight + 20, top: 80, left: 30, right: 30 };
  map.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });   // flyTo-Abstand nicht doppelt zählen
  map.fitBounds([[w, s], [e, n]], { padding, maxZoom: 18, duration: reducedMotion ? 0 : 800 });
}

function showSheet(html) {
  const body = document.getElementById('sheet-body');
  const sheet = document.getElementById('sheet');
  // geöffnete Abschnitte und Scrollposition beim Neuzeichnen behalten
  const open = new Map([...body.querySelectorAll('details.sec')].map((d) => [d.querySelector('summary span')?.textContent, d.open]));
  const top = sheet.scrollTop;
  body.innerHTML = html;
  body.querySelectorAll('details.sec').forEach((d) => {
    const k = d.querySelector('summary span')?.textContent;
    if (open.has(k)) d.open = open.get(k);
  });
  sheet.hidden = false;
  sheet.scrollTop = top;
}

// ---------------------------------------------------------------------------
// 3D-Ansicht: Gelände (DGM1) + Gebäude als Blöcke mit Höhe aus DOM1 − DGM1
function updateBuildingVisibility(map) {
  const on = document.querySelector('input[data-layer=gebaeude]')?.checked ?? true;
  const vis = (id, v) => map.getLayer(id) && map.setLayoutProperty(id, 'visibility', v ? 'visible' : 'none');
  vis('gebaeude', on && !state.is3d);
  vis('gebaeude-linie', on && !state.is3d);
  vis('gebaeude-auswahl', !state.is3d);
  vis('gebaeude-s4', on && !state.is3d && state.view !== 'schaden');
  vis('gebaeude-3d', on && state.is3d);
  vis('gebaeude-markiert', on && state.classes.has(state.mk.schutzklasse));
  vis('gebaeude-markiert-saum', on && state.classes.has(state.mk.schutzklasse));
  vis('markiert-punkte', on && state.classes.has(state.mk.schutzklasse));
}

function setup3d(map) {
  const btn = document.getElementById('btn-3d');
  const url = state.meta.dateien.gelaende_pmtiles;
  btn.addEventListener('click', () => {
    state.is3d = !state.is3d;
    btn.setAttribute('aria-pressed', String(state.is3d));
    if (state.is3d && url && !map.getSource('gelaende')) {
      const src = { type: 'raster-dem', url: `pmtiles://${abs(url + state.v)}`, encoding: 'terrarium', tileSize: 256 };
      map.addSource('gelaende', src);
      map.addSource('gelaende-schatten', { ...src });
      const first = map.getStyle().layers.find((l) => l.id.startsWith('tiefe_'))?.id;
      map.addLayer({ id: 'schummerung', type: 'hillshade', source: 'gelaende-schatten', layout: { visibility: 'none' },
        paint: { 'hillshade-exaggeration': 0.35, 'hillshade-shadow-color': '#4a5560', 'hillshade-highlight-color': '#ffffff' } }, first);
    }
    if (map.getSource('gelaende')) {
      map.setTerrain(state.is3d ? { source: 'gelaende', exaggeration: 1.5 } : null);
      map.setLayoutProperty('schummerung', 'visibility', state.is3d ? 'visible' : 'none');
    }
    updateBuildingVisibility(map);
    const dur = reducedMotion ? 0 : 900;
    if (state.is3d) map.easeTo({ pitch: 55, bearing: map.getBearing() || -20, zoom: Math.max(map.getZoom(), 15.5), duration: dur });
    else map.easeTo({ pitch: 0, bearing: 0, duration: dur });
  });
}
function hideSheet() { document.getElementById('sheet').hidden = true; }

function setupDisclaimer() {
  const dlg = document.getElementById('disclaimer');
  document.getElementById('disclaimer-link').addEventListener('click', () => dlg.showModal());
  if (store('hinweis-gelesen') !== '1') dlg.showModal();
  dlg.addEventListener('close', () => store('hinweis-gelesen', '1'));
}

function setupLocate(map) {
  let marker = null;
  document.getElementById('locate').addEventListener('click', () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((pos) => {
      const ll = [pos.coords.longitude, pos.coords.latitude];
      marker?.remove();
      marker = new maplibregl.Marker({ color: '#0b2f7a' }).setLngLat(ll).addTo(map);
      map.flyTo({ center: ll, zoom: 17 });
    }, () => alert('Standort nicht verfügbar.'), { enableHighAccuracy: true, timeout: 10000 });
  });
}

main().catch((err) => {
  console.error(err);
  document.body.insertAdjacentHTML('beforeend', '<div class="error">Die Daten konnten nicht geladen werden.</div>');
});
