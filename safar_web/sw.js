/* Public shell only. Private APIs, media and mutations bypass CacheStorage. */
'use strict';
const CACHE = 'safar-public-shell-v031-20261009';
const SHELL = ['/safar/app.js?v=20261009-031', '/safar/style.css?v=20261009-031', '/safar/icon.svg',
  '/safar/manifest.webmanifest', '/safar/offline.html', '/safar/icon-192.png',
  '/safar/icon-512.png', '/safar/icon-maskable-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(names => Promise.all(names
    .filter(name => name.startsWith('safar-public-shell-') && name !== CACHE)
    .map(name => caches.delete(name)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (request.mode === 'navigate' && ['/safar', '/safar/'].includes(url.pathname)) {
    // Never persist a navigation response or its user-dependent URL.
    event.respondWith(fetch(request).catch(async () => {
      const cache = await caches.open(CACHE);
      return (await cache.match('/safar/offline.html')) || Response.error();
    }));
    return;
  }
  // Only the exact current-version public shell may be cached. Private API,
  // media and all other query URLs stay out of CacheStorage.
  if (!SHELL.includes(url.pathname + url.search)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    try {
      const response = await fetch(request);
      if (response.ok && !/no-store|private/i.test(response.headers.get('Cache-Control') || '')) {
        await cache.put(request, response.clone());
      }
      return response;
    } catch (error) {
      const cached = await cache.match(request);
      if (cached) return cached;
      throw error;
    }
  })());
});
