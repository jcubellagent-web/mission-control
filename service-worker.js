const CACHE_NAME = "mission-control-pwa-v10";
const FILES_TO_CACHE = [
  "./manifest.webmanifest",
  "./assets/logo.jpg",
  "./assets/logo-192.png",
  "./assets/logo-512.png"
  // index.html intentionally excluded — always fetched fresh (network-first)
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(FILES_TO_CACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
          return null;
        })
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }

  const url = new URL(event.request.url);

  // Always network-first for: index.html, root, and all data JSON files
  // This ensures the dashboard always loads the latest code and data
  const alwaysFresh = (
    url.pathname.endsWith("/data/dashboard-data.json") ||
    url.pathname.endsWith("/data/brain-feed.json") ||
    url.pathname.endsWith("/data/modelUsage.json") ||
    url.pathname.endsWith("/data/newsfeed.json") ||
    url.pathname.endsWith("/index.html") ||
    url.pathname === "/mission-control/" ||
    url.pathname.endsWith("/mission-control/")
  );

  if (alwaysFresh) {
    // Network-first: always try network, only fall back to cache if offline
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Cache-first for static assets (images, fonts, icons)
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      if (cachedResponse) {
        return cachedResponse;
      }
      return fetch(event.request)
        .then((response) => {
          if (!response || response.status !== 200 || response.type !== "basic") {
            return response;
          }
          const responseToCache = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
          return response;
        })
        .catch(() => null);
    })
  );
});
