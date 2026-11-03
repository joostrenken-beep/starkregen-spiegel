// Starkregen-Spiegel – Kartenanwendung (Stadtgebiet, Kachel- und Paketdaten)
import maplibregl from 'maplibre-gl';
import { Protocol } from 'pmtiles';
import { createCells, collect } from './cells.js';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import { createSearch } from './search.js';
import { renderBuilding, renderSink } from './info.js';

const DATA = './data/';
const BASEMAP =
  'https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_grau/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png';
const AMPEL = { 0: '#3a9d5d', 1: '#f2b705', 2: '#d7301f' };
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const state = { meta: null, sid: null, view: 'gef', classes: new Set([1, 2, 3, 4]), buildings: new Map(), selected: null, v: '', cells: null };
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

    map.addSource('gebaeude', { type: 'geojson', data: EMPTY, promoteId: 'id' });
    map.addLayer({ id: 'gebaeude', type: 'fill', source: 'gebaeude', minzoom: 13,
      paint: { 'fill-color': ampelColor(state.sid), 'fill-opacity': ampelOpacity(state.sid) } });
    map.addLayer({ id: 'gebaeude-s4', type: 'line', source: 'gebaeude', minzoom: 13,
      filter: ['==', ['get', 's'], 4], layout: { visibility: 'none' },
      paint: { 'line-color': '#6a1b9a', 'line-width': ['interpolate', ['linear'], ['zoom'], 13, 1.5, 18, 3.5] } });
    map.addLayer({ id: 'gebaeude-linie', type: 'line', source: 'gebaeude', minzoom: 15,
      paint: { 'line-color': '#444', 'line-width': 0.4, 'line-opacity': 0.6 } });
    map.addLayer({ id: 'gebaeude-auswahl', type: 'line', source: 'gebaeude',
      paint: { 'line-color': '#111', 'line-width': ['case', ['boolean', ['feature-state', 'sel'], false], 3, 0] } });

    map.addSource('fliesswege', { type: 'geojson', data: EMPTY });
    const width = ['interpolate', ['linear'], ['ln', ['max', ['get', 'v'], 1]], 3.4, 1, 6.9, 2.5, 9.2, 5];
    map.addLayer({ id: 'fliesswege', type: 'line', source: 'fliesswege',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#0a6fb5', 'line-width': width, 'line-opacity': 0.35 } });
    map.addLayer({ id: 'fliesswege-anim', type: 'line', source: 'fliesswege',
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: { 'line-color': '#00c2ff', 'line-width': width, 'line-dasharray': [0, 4, 3] } });
    if (!reducedMotion) animateFlow(map);

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
    state.cells.refresh(true);
    document.body.classList.add('ready');
  });

  setupDisclaimer();
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
  const s4 = ['==', ['get', 's'], 4];
  map.setFilter('gebaeude-s4', state.classes.has(4) ? s4 : ['==', 1, 0]);
  map.setLayoutProperty('gebaeude-s4', 'visibility', state.view === 'schaden' ? 'none' : 'visible');
  renderLegend();
}

function setupViewSwitch(map) {
  document.querySelectorAll('.view-switch button').forEach((b) => b.addEventListener('click', () => {
    state.view = b.dataset.view;
    document.querySelectorAll('.view-switch button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    applyBuildingStyle(map);
    if (state.selected != null) showSheet(renderBuilding(state.buildings.get(state.selected), state.meta, state.sid, state.view));
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
  const s4 = '<div class="lg"><span class="sw s4"></span>Umrandung: Schadenspotenzial S4</div>';
  const filt = state.classes.size < 4 ? `<div class="lg small">gezeigt: ${[...state.classes].sort().map((k) => `S${k}`).join(', ')}</div>` : '';
  if (state.view === 'schaden') {
    return `<h3>Schadenspotenzial (Gebäudeart, unabhängig vom Regen)</h3>${[4, 3, 2, 1].map((k) => sw(SK[k], SK_TXT[k])).join('')}${filt}`;
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
  range.addEventListener('input', async () => {
    const sc = scen[+range.value];
    lbl.textContent = `${sc.mm} mm`;
    state.sid = sc.id;
    const tiefeAn = document.querySelector('input[data-layer=tiefe]').checked;
    scen.forEach((x) => map.setLayoutProperty(`tiefe_${x.id}`, 'visibility', tiefeAn && x.id === sc.id ? 'visible' : 'none'));
    applyBuildingStyle(map);
    map.getSource('fliesswege').setData(collect(state.cells.loadedData(), sc.id).fliesswege);
    if (state.selected != null) showSheet(renderBuilding(state.buildings.get(state.selected), state.meta, state.sid, state.view));
  });
}

function setupClicks(map) {
  map.on('click', (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: ['gebaeude', 'senken'].filter((l) => map.getLayer(l)) });
    const b = hits.find((h) => h.layer.id === 'gebaeude');
    if (b) return selectBuilding(map, b.id, null, false);
    const sk = hits.find((h) => h.layer.id === 'senken');
    if (sk) return showSheet(renderSink(sk.properties, state.meta, state.sid));
    clearSelection(map);
    hideSheet();
  });
  map.on('mouseenter', 'gebaeude', () => { map.getCanvas().style.cursor = 'pointer'; });
  map.on('mouseleave', 'gebaeude', () => { map.getCanvas().style.cursor = ''; });
}

function clearSelection(map) {
  if (state.selected != null) map.setFeatureState({ source: 'gebaeude', id: state.selected }, { sel: false });
  state.selected = null;
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
    showSheet(renderBuilding(f, state.meta, state.sid, state.view));
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
}
function showSheet(html) {
  document.getElementById('sheet-body').innerHTML = html;
  document.getElementById('sheet').hidden = false;
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
