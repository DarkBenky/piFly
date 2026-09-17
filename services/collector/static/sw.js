self.addEventListener("install", function(e) {
  self.skipWaiting();
});

self.addEventListener("fetch", function(e) {
  e.respondWith(fetch(e.request));
});
