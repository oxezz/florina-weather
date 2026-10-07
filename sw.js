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

   Anything served from the cache is tagged with X-SW-Source, so the page can
   say so rather than passing old numbers off as current.

   Bump CACHE_VERSION on releases that change the shell.
   ========================================================================== */
"use strict";

var CACHE_VERSION = "florina-v2";
/* How long a network-first request waits before giving up and using the cache.
   Without a ceiling, a connection that hangs leaves the user staring at
   nothing while a perfectly good cached copy sits unread. */
var NETWORK_TIMEOUT = 4000;

var SHELL = [
  "/",
  "/manifest.webmanifest",
  "/favicon.svg",
  "/icon-192.png",
  "/icon-512.png",
  "/icon-maskable-512.png",
  "/icon-180.png",
  // The page shell also needs the scripts and the stylesheet, or an offline
  // launch renders unstyled and says nothing. The radar grid is deliberately
  // NOT here: it is stale within minutes, and a cached grid would draw weather
  // that has already fallen.
  "/style.css",
  "/app.js",
  "/theme.js",
  "/radar.js"
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
    event.respondWith(networkFirst(request, url.origin + "/api/weather"));
    return;
  }
  if (request.mode === "navigate") {
    // Every navigation shares one cache key. Keying on the full URL meant
    // /?mode=dark, /?mode=light and / each kept their own copy of the same
    // page, and the cache grew with every theme the user tried.
    event.respondWith(networkFirst(request, "/"));
    return;
  }
  event.respondWith(staleWhileRevalidate(request));
});

function withTimeout(promise, ms) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () { reject(new Error("timeout")); }, ms);
    promise.then(function (value) {
      clearTimeout(timer); resolve(value);
    }, function (error) {
      clearTimeout(timer); reject(error);
    });
  });
}

function store(key, response) {
  if (!response || !response.ok) return;
  var copy = response.clone();
  caches.open(CACHE_VERSION).then(function (cache) {
    cache.put(key, copy);
  }).catch(function () { /* quota or private mode: not worth failing over */ });
}

/* Tag a cached response so the page can tell it apart from a live one. */
function tagAsCached(response) {
  if (!response) return response;
  var headers = new Headers(response.headers);
  headers.set("X-SW-Source", "cache");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: headers
  });
}

function networkFirst(request, cacheKey) {
  var key = cacheKey || request;
  var network = fetch(request);

  // The fetch is left running even after a timeout, so a slow success still
  // refreshes what we hold for next time.
  network.then(function (response) {
    store(key, response);
  }).catch(function () { /* offline: the catch below handles it */ });

  return withTimeout(network, NETWORK_TIMEOUT).catch(function () {
    return caches.match(key).then(function (cached) {
      if (cached) return tagAsCached(cached);
      // Last resort for a navigation with nothing cached.
      if (request.mode === "navigate") {
        return caches.match("/").then(tagAsCached);
      }
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
      store(request, response);
      return response;
    }).catch(function () { return cached ? tagAsCached(cached) : cached; });
    return cached ? tagAsCached(cached) : network;
  });
}
