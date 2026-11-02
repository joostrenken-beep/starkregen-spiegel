// Service Worker: App offline nutzbar, Daten zwischengespeichert.
const VERSION = 'v2';
const APP = `app-${VERSION}`;
const TILES = 'tiles-v1';
const MAX_TILES = 1500;

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(APP).then((c) => c.addAll(['./', './index.html', './manifest.webmanifest'])).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith('app-') && k !== APP).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function trim(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_TILES; i++) await cache.delete(keys[i]);
}

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.hostname === 'sgx.geodatenzentrum.de') {           // Hintergrundkarte: Cache zuerst
    e.respondWith(caches.open(TILES).then(async (c) => {
      const hit = await c.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) { c.put(req, res.clone()); trim(c); }
      return res;
    }));
    return;
  }
  if (url.origin !== self.location.origin) return;            // z. B. Nominatim: nie cachen
  if (req.mode === 'navigate') {                              // Seite: Netz zuerst
    e.respondWith(fetch(req).then((res) => { caches.open(APP).then((c) => c.put(req, res.clone())); return res; })
      .catch(() => caches.match('./index.html')));
    return;
  }
  // Daten und Assets: Cache, im Hintergrund aktualisieren
  e.respondWith(caches.open(APP).then(async (c) => {
    const hit = await c.match(req);
    const net = fetch(req).then((res) => { if (res.ok) c.put(req, res.clone()); return res; }).catch(() => hit);
    return hit || net;
  }));
});
