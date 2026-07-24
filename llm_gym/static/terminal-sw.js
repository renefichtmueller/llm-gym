const CACHE_NAME = "llm-gym-terminal-20260724-1";
const STATIC_ASSETS = [
  "/static/xterm/xterm.css",
  "/static/xterm/xterm.js",
  "/static/xterm/addon-fit.js",
  "/static/terminal.webmanifest",
  "/static/terminal-offline.html",
  "/static/terminal-icons/icon-192.png",
  "/static/terminal-icons/icon-512.png",
  "/static/terminal-icons/apple-touch-icon-180.png",
];
const STATIC_PATHS = new Set(STATIC_ASSETS);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(
        names
          .filter((name) => name.startsWith("llm-gym-terminal-") && name !== CACHE_NAME)
          .map((name) => caches.delete(name)),
      ))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data?.type === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .catch(() => caches.match("/static/terminal-offline.html")),
    );
    return;
  }

  if (!STATIC_PATHS.has(url.pathname)) return;
  event.respondWith(
    caches.match(request)
      .then((cached) => cached || fetch(request)),
  );
});
