// Pixel Office — service worker (CLOUD.md §10.2). Registered only in cloud mode.
// /api/*, /hook/* and /connector/* are never intercepted (network only). Navigations: network-first, offline fallback to the cached
// /index.html.
//
// Code and pages (*.js, *.css, *.html, the manifest) are network-first as well, with the cache only as the offline
// fallback (v2.md H16). With stale-while-revalidate the first load after a deploy could run a cached old main.js
// together with a freshly fetched new control.js (or the other way round): ES modules of two releases with
// different exports or protocols. Network-first loads every module of one page from the same deploy whenever the
// network is up; offline everything comes from the one cache of this VERSION, which is also consistent. Only the
// icons (never changed in place) stay stale-while-revalidate.
// After activation the worker tells open pages its VERSION; a page built for another version (an older tab) shows
// "Yeni sürüm hazır — Yenile" (cloud.js APP_VERSION, kept equal to VERSION by tests/test_release.py).
// Bump VERSION (and cloud.js APP_VERSION) on every release (Phase 10 checklist).

const VERSION = 'po-v3.0.8';
const SHELL = [
  '/', '/index.html', '/css/style.css',
  '/js/main.js', '/js/office.js', '/js/pathfinding.js', '/js/agents.js', '/js/renderer.js', '/js/ui.js',
  '/js/sprites.js', '/js/fx.js', '/js/audio.js', '/js/transport.js', '/js/cloud.js', '/js/pip.js',
  '/js/relay.js', '/js/e2e.js', '/js/idb.js', '/js/control.js', '/js/prompt.js', '/js/devices.js', '/js/usage.js',
  '/rehber.html', '/css/guide.css', '/js/guide.js',
  '/manifest.webmanifest',
  '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-maskable-512.png', '/icons/apple-touch-icon.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    await cache.addAll(SHELL.map((u) => new Request(u, { cache: 'reload' })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== VERSION) await caches.delete(k);
    await self.clients.claim();
    for (const c of await self.clients.matchAll({ type: 'window' })) c.postMessage({ type: 'po-sw-version', version: VERSION });
  })());
});

const NETWORK_FIRST = /\.(js|css|html|webmanifest)$/;

/** Network first, refreshing the cache; the cached copy only when the network fails (offline). */
async function networkFirst(req) {
  const cache = await caches.open(VERSION);
  try {
    const res = await fetch(req);
    if (res && res.ok && res.type === 'basic') cache.put(req, res.clone()).catch(() => {});
    return res;
  } catch {
    return (await cache.match(req, { ignoreSearch: true })) || Response.error();
  }
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/api' || url.pathname.startsWith('/hook/')
      || url.pathname.startsWith('/connector/')) return;  // network only
  if (url.pathname === '/sw.js') return;

  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return await fetch(req);
      } catch {
        const cache = await caches.open(VERSION);
        return (await cache.match('/index.html')) || (await cache.match('/')) || Response.error();
      }
    })());
    return;
  }

  if (url.pathname === '/' || NETWORK_FIRST.test(url.pathname)) {
    event.respondWith(networkFirst(req));
    return;
  }

  event.respondWith((async () => {
    const cache = await caches.open(VERSION);
    const cached = await cache.match(req, { ignoreSearch: true });
    const network = fetch(req).then((res) => {
      if (res && res.ok && res.type === 'basic') cache.put(req, res.clone()).catch(() => {});
      return res;
    }).catch(() => null);
    if (cached) {
      event.waitUntil(network);
      return cached;
    }
    return (await network) || Response.error();
  })());
});
