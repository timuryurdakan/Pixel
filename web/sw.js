// Pixel Office — service worker (CLOUD.md §10.2). Registered only in cloud mode.
// /api/*, /hook/* and /connector/* are never intercepted (network only). Navigations: network-first, offline fallback to the cached
// /index.html. Other same-origin GETs: stale-while-revalidate.
// Bump VERSION on every release (Phase 10 checklist).

const VERSION = 'po-v3.0.5';
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
  })());
});

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
