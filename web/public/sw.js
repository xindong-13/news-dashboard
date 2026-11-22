// Service worker：讓網站可以「加到主畫面」，而且沒網路時也能看之前開過的頁面。
// 策略是「網路優先」：有網路一律抓最新的（新聞要即時），抓不到才拿上次存的。
// 改了這個檔案的快取邏輯時，把 CACHE 的版本號加一，舊的快取會被清掉。
var CACHE = "news-v1";
var PRECACHE = ["./", "index.html", "archive.html", "favicon.svg", "read.js", "manifest.webmanifest"];

self.addEventListener("install", function (ev) {
  ev.waitUntil(
    caches
      .open(CACHE)
      .then(function (c) {
        return c.addAll(PRECACHE);
      })
      .then(function () {
        return self.skipWaiting();
      }),
  );
});

self.addEventListener("activate", function (ev) {
  ev.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (k) {
              return k !== CACHE;
            })
            .map(function (k) {
              return caches.delete(k);
            }),
        );
      })
      .then(function () {
        return self.clients.claim();
      }),
  );
});

self.addEventListener("fetch", function (ev) {
  var req = ev.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  ev.respondWith(
    fetch(req)
      .then(function (res) {
        if (res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) {
            c.put(req, copy);
          });
        }
        return res;
      })
      .catch(function () {
        return caches.match(req, { ignoreSearch: true }).then(function (hit) {
          if (hit) return hit;
          // 沒存過的頁面：離線時先顯示首頁（最後一次看到的最新一刊）
          if (req.mode === "navigate") return caches.match("index.html");
          return Response.error();
        });
      }),
  );
});
