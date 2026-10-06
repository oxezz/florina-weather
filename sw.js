/* ==========================================================================
   Καιρός · Φλώρινα — service worker
   --------------------------------------------------------------------------
   Exists for two reasons:

     1. Android Chrome only offers the "Install app" prompt to a site with a
        service worker that handles fetch. Without it, users can still use the
        browser menu, but they never get the prompt.
     2. A cached app shell makes repeat launches instant, and gives a readable
        offline page instead of the browser's error screen.

   Delivery strategy is deliberately conservative, because stale weather is
   worse than no weather:

     /api/weather   network-first, cache only as an offline fallback
     navigations    network-first, cache as an offline fallback
     static assets  stale-while-revalidate: instant from cache, refreshed in
                    the background so the next load picks up a new deploy

   Bump CACHE_VERSION on releases that change the shell.
   ========================================================================== */
"use strict";

var CACHE_VERSION = "florina-v1";
var SHELL = [
  "/",
  "/style.css",
  "/app.js",
  "/theme.js",
  "/manifest.webmanifest",
  "/favicon.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
  "/icon-180.png"
];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE_VERSION).then(function (cache) {
      // addAll fails the whole install if any single URL 404s, so add them
      // individually and tolerate a miss.
      return Promise.all(SHELL.map(function (url) {
        return cache.add(new Request(url, { cache: "reload" })).catch(function () {
          return undefined;
        });
      }));
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        return key === CACHE_VERSION ? undefined : caches.delete(key);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname === "/api/weather") {
    event.respondWith(networkFirst(request));
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(staleWhileRevalidate(request));
});

function networkFirst(request) {
  return fetch(request).then(function (response) {
    if (response && response.ok) {
      var copy = response.clone();
      caches.open(CACHE_VERSION).then(function (cache) { cache.put(request, copy); });
    }
    return response;
  }).catch(function () {
    return caches.match(request).then(function (cached) {
      if (cached) return cached;
      // Last resort for a navigation with nothing cached.
      if (request.mode === "navigate") return caches.match("/");
      return new Response(JSON.stringify({ error: "offline" }), {
        status: 503,
        headers: { "Content-Type": "application/json" }
      });
    });
  });
}

function staleWhileRevalidate(request) {
  return caches.match(request).then(function (cached) {
    var network = fetch(request).then(function (response) {
      if (response && response.ok) {
        var copy = response.clone();
        caches.open(CACHE_VERSION).then(function (cache) { cache.put(request, copy); });
      }
      return response;
    }).catch(function () { return cached; });
    return cached || network;
  });
}
