const CACHE_NAME = 'dtc-pwa-v11-9-shell';
const SHELL_FILES = [
  './',
  './index.html',
  './styles.css?v=20260821-v11_9',
  './runtime-config.js?v=20260821-v11_9',
  './app.js?v=20260821-v11_9',
  './news-config.js?v=20260821-v11_9',
  './pwa.js?v=20260821-v11_9',
  './manifest.webmanifest',
  './logo-dtc.svg?v=20260821-v11_9',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-512-maskable.png'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    Promise.all([
      caches.keys().then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      )),
      self.clients.claim()
    ])
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Never cache cross-origin requests (Android/GitHub fallback reads Firebase live data).
  if (url.origin !== self.location.origin) {
    event.respondWith(fetch(request));
    return;
  }

  // Market data and build metadata must stay fresh.
  if (url.pathname.includes('/data/') || url.pathname.endsWith('/build-info.json')) {
    event.respondWith(fetch(request, { cache: 'no-store' }));
    return;
  }

  // HTML/navigation: network first so a deployed UI update appears immediately.
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(request, { cache: 'no-store' });
        const cache = await caches.open(CACHE_NAME);
        cache.put(request, fresh.clone());
        return fresh;
      } catch (_) {
        return (await caches.match(request)) || (await caches.match('./index.html'));
      }
    })());
    return;
  }

  // Static shell: stale-while-revalidate for fast launch, with versioned URLs.
  event.respondWith((async () => {
    const cached = await caches.match(request);
    const networkPromise = fetch(request).then(async (response) => {
      if (response && response.ok) {
        const cache = await caches.open(CACHE_NAME);
        cache.put(request, response.clone());
      }
      return response;
    }).catch(() => null);

    return cached || (await networkPromise) || Response.error();
  })());
});
