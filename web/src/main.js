// Starkregen-Spiegel – Kartenanwendung (Meilenstein 2)
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import { createSearch } from './search.js';
import { renderBuilding, renderSink } from './info.js';

const DATA = './data/';
const BASEMAP =
  'https://sgx.geodatenzentrum.de/wmts_basemapde/tile/1.0.0/de_basemapde_web_raster_grau/default/GLOBAL_WEBMERCATOR/{z}/{y}/{x}.png';
const AMPEL = { 0: '#3a9d5d', 1: '#f2b705', 2: '#d7301f' };
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const state = { meta: null, sid: null, flowCache: {}, buildings: new Map(), selected: null };

function store(key, val) {
  try {
    if (val === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, val);
  } catch { return null; }
  return null;
}

async function main() {
  const meta = await fetch(DATA + 'meta.json').then((r) => r.json());
  state.meta = meta;
  document.getElementById('stand').textContent = meta.stand;
  const scen = meta.szenarien;
  const startIdx = Math.min(1, scen.length - 1);
  state.sid = scen[startIdx].id;

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
    maxBounds: [[w - 0.05, s - 0.03], [e + 0.05, n + 0.03]],
    maxZoom: 20,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
  map.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-left');
  window.__map = map; // für Tests

  map.on('load', async () => {
    const t = meta.dateien.tiefe[state.sid];
    // Pilotgebiet-Umriss
    map.addSource('gebiet', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } },
    });
    map.addLayer({ id: 'gebiet', type: 'line', source: 'gebiet',
      paint: { 'line-color': '#0b2f7a', 'line-width': 1.5, 'line-dasharray': [3, 2], 'line-opacity': 0.6 } });

    map.addSource('senken', { type: 'geojson', data: DATA + 'senken.json' });
    map.addLayer({ id: 'senken', type: 'fill', source: 'senken', layout: { visibility: 'none' },
      paint: { 'fill-color': ['interpolate', ['linear'], ['get', 'tmax'], 0.1, '#e9d8f5', 0.5, '#b07cd6', 1.5, '#5b2a86'],
        'fill-opacity': 0.55, 'fill-outline-color': '#5b2a86' } });

    map.addSource('tiefe', { type: 'image', url: t.url, coordinates: t.coords });
    map.addLayer({ id: 'tiefe', type: 'raster', source: 'tiefe',
      paint: { 'raster-opacity': 0.9, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 } });

    const gb = await fetch(DATA + 'gebaeude.json').then((r) => r.json());
    gb.features.forEach((f) => state.buildings.set(f.properties.id, f));
    map.addSource('gebaeude', { type: 'geojson', data: gb, promoteId: 'id' });
    map.addLayer({ id: 'gebaeude', type: 'fill', source: 'gebaeude', minzoom: 13,
      paint: { 'fill-color': ampelColor(state.sid), 'fill-opacity': ampelOpacity(state.sid) } });
    map.addLayer({ id: 'gebaeude-linie', type: 'line', source: 'gebaeude', minzoom: 15,
      paint: { 'line-color': '#444', 'line-width': 0.4, 'line-opacity': 0.6 } });
    map.addLayer({ id: 'gebaeude-auswahl', type: 'line', source: 'gebaeude',
      paint: { 'line-color': '#111', 'line-width': ['case', ['boolean', ['feature-state', 'sel'], false], 3, 0] } });

    map.addSource('fliesswege', { type: 'geojson', data: await flowData(state.sid) });
    const width = ['interpolate', ['linear'], ['ln', ['max', ['get', 'v'], 1]], 3.4, 1, 6.9, 2.5, 9.2, 5];
    map.addLayer({ id: 'fliesswege', type: 'line', source: 'fliesswege',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': '#0a6fb5', 'line-width': width, 'line-opacity': 0.35 } });
    map.addLayer({ id: 'fliesswege-anim', type: 'line', source: 'fliesswege',
      layout: { 'line-cap': 'butt', 'line-join': 'round' },
      paint: { 'line-color': '#00c2ff', 'line-width': width, 'line-dasharray': [0, 4, 3] } });
    if (!reducedMotion) animateFlow(map);

    setupLayerPanel(map);
    setupScenario(map);
    setupClicks(map);
    const adr = await fetch(DATA + 'adressen.json').then((r) => r.json());
    createSearch({ map, addresses: adr, bounds: meta.gebiet.bounds, onSelect: (hit) => selectBuilding(map, hit.id, hit) });
    setupLocate(map);
    document.body.classList.add('ready');
  });

  setupDisclaimer();
  setupSheet(map);
  if ('serviceWorker' in navigator && import.meta.env.PROD) {
    navigator.serviceWorker.register('./sw.js').catch(() => {});
  }
}

function ampelColor(sid) {
  return ['match', ['slice', ['get', sid], 0, 1], '2', AMPEL[2], '1', AMPEL[1], AMPEL[0]];
}
function ampelOpacity(sid) {
  return ['match', ['slice', ['get', sid], 0, 1], '2', 0.8, '1', 0.75, 0.35];
}

async function flowData(sid) {
  if (!state.flowCache[sid]) {
    state.flowCache[sid] = fetch(DATA + `fliesswege_${sid}.json`).then((r) => r.json());
  }
  return state.flowCache[sid];
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
  const groups = { tiefe: ['tiefe'], fliesswege: ['fliesswege', 'fliesswege-anim'], senken: ['senken'],
    gebaeude: ['gebaeude', 'gebaeude-linie'] };
  panel.querySelectorAll('input[data-layer]').forEach((cb) => {
    cb.addEventListener('change', () => {
      groups[cb.dataset.layer].forEach((id) => map.setLayoutProperty(id, 'visibility', cb.checked ? 'visible' : 'none'));
    });
  });
  const m = state.meta;
  document.getElementById('legend').innerHTML = `
    <h3>Max. Wassertiefe</h3>
    ${m.tiefenklassen.map((k, i) => {
      const next = m.tiefenklassen[i + 1];
      const txt = next ? `${Math.round(k.ab_m * 100)}–${Math.round(next.ab_m * 100)} cm` : `über ${Math.round(k.ab_m * 100)} cm`;
      return `<div class="lg"><span class="sw" style="background:${k.farbe}"></span>${txt}</div>`;
    }).join('')}
    <h3>Gebäude am Haus</h3>
    <div class="lg"><span class="sw" style="background:${AMPEL[2]}"></span>ab ${m.ampel.rot_ab_m * 100} cm</div>
    <div class="lg"><span class="sw" style="background:${AMPEL[1]}"></span>ab ${m.ampel.gelb_ab_m * 100} cm</div>
    <div class="lg"><span class="sw" style="background:${AMPEL[0]}"></span>darunter</div>
    <h3>Fließwege</h3>
    <div class="lg"><span class="sw line"></span>Richtung = Bewegung, Breite = Wassermenge</div>`;
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
    const t = state.meta.dateien.tiefe[sc.id];
    map.getSource('tiefe').updateImage({ url: t.url, coordinates: t.coords });
    map.setPaintProperty('gebaeude', 'fill-color', ampelColor(sc.id));
    map.setPaintProperty('gebaeude', 'fill-opacity', ampelOpacity(sc.id));
    map.getSource('fliesswege').setData(await flowData(sc.id));
    if (state.selected != null) showSheet(renderBuilding(state.buildings.get(state.selected), state.meta, state.sid));
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

function selectBuilding(map, id, hit, fly = true) {
  clearSelection(map);
  const f = state.buildings.get(id);
  if (!f) {
    showSheet(`<h2>${hit ? hit.label : 'Adresse'}</h2><p>Für diese Adresse liegt kein Gebäude im Pilotgebiet vor.</p>`);
  } else {
    state.selected = id;
    map.setFeatureState({ source: 'gebaeude', id }, { sel: true });
    showSheet(renderBuilding(f, state.meta, state.sid));
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
