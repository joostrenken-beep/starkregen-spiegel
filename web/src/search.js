// Adresssuche: zuerst eigener Index (ALKIS, offline, kein Rate-Limit),
// danach Nominatim als Rückfallebene (max. 1 Anfrage/s, nur auf Enter – keine Live-Vorschläge,
// wie es die Nominatim-Nutzungsrichtlinie verlangt).

const norm = (s) => s.toLowerCase()
  .replace(/ß/g, 'ss').replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue')
  .replace(/str\.?(\s|$)/g, 'strasse$1').replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();

let lastNominatim = 0;

export function createSearch({ map, addresses, bounds, onSelect }) {
  const input = document.getElementById('q');
  const list = document.getElementById('suggest');
  const form = document.getElementById('search');
  const idx = addresses.map(([street, nr, lon, lat, id]) => ({
    street, nr, lon, lat, id, label: `${street} ${nr}`, key: norm(`${street} ${nr}`), skey: norm(street),
  }));
  let hits = [];
  let active = -1;

  function find(q) {
    const nq = norm(q);
    if (nq.length < 2) return [];
    const m = nq.match(/^(.*?)\s*(\d+\s*[a-z]?)?$/);
    const streetPart = (m?.[1] || nq).trim();
    const nrPart = (m?.[2] || '').replace(/\s/g, '');
    let res = idx.filter((a) => a.skey.startsWith(streetPart) || a.skey.includes(streetPart));
    if (nrPart) res = res.filter((a) => norm(a.nr).replace(/\s/g, '').startsWith(nrPart));
    // je Straße ohne Hausnummer nur einen Eintrag
    if (!nrPart) {
      const seen = new Set();
      res = res.filter((a) => (seen.has(a.street) ? false : seen.add(a.street)));
    }
    res.sort((a, b) => (a.skey.startsWith(streetPart) ? 0 : 1) - (b.skey.startsWith(streetPart) ? 0 : 1));
    return res.slice(0, 8);
  }

  function render() {
    list.innerHTML = hits.map((h, i) =>
      `<li role="option" data-i="${i}" class="${i === active ? 'active' : ''}">${h.nr && hasNr() ? h.label : h.street}</li>`).join('');
    list.hidden = hits.length === 0;
  }
  const hasNr = () => /\d/.test(input.value);

  function choose(h) {
    list.hidden = true;
    if (!hasNr()) { // Straße gewählt -> Hausnummer ergänzen lassen
      input.value = `${h.street} `;
      input.focus();
      hits = find(input.value);
      render();
      map.flyTo({ center: [h.lon, h.lat], zoom: 16 });
      return;
    }
    input.value = h.label;
    input.blur();
    onSelect(h);
  }

  input.addEventListener('input', () => { hits = find(input.value); active = -1; render(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { active = Math.min(active + 1, hits.length - 1); render(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); render(); e.preventDefault(); }
    if (e.key === 'Escape') { list.hidden = true; }
  });
  list.addEventListener('click', (e) => {
    const li = e.target.closest('li');
    if (li) choose(hits[+li.dataset.i]);
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (hits.length) return choose(hits[Math.max(active, 0)]);
    await nominatim(input.value);
  });

  async function nominatim(q) {
    if (q.trim().length < 3) return;
    const wait = 1100 - (Date.now() - lastNominatim);
    if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    lastNominatim = Date.now();
    const [w, s, e, n] = bounds;
    const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=de&q=${encodeURIComponent(q)}`
      + `&viewbox=${w - 0.1},${n + 0.1},${e + 0.1},${s - 0.1}`;
    try {
      const r = await fetch(url, { headers: { 'Accept-Language': 'de' } });
      const [hit] = await r.json();
      if (!hit) return toast('Adresse nicht gefunden.');
      const lon = +hit.lon; const lat = +hit.lat;
      map.flyTo({ center: [lon, lat], zoom: 17 });
      const inside = lon >= w && lon <= e && lat >= s && lat <= n;
      if (!inside) toast('Diese Adresse liegt außerhalb des Pilotgebiets – dort gibt es noch keine Berechnung.');
    } catch {
      toast('Suche zurzeit nicht möglich.');
    }
  }
}

export function toast(msg) {
  let el = document.querySelector('.toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'toast';
    el.setAttribute('role', 'status');
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), 4500);
}
