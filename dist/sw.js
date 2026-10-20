const CACHE = 'wine-server-v55-portal-icon';
const SHELL = ['/', '/app.js', '/app.css', '/chat.css', '/wine-card.css', '/portal-shell.css', '/scanner.css', '/scanner-reel.js', '/collection.js', '/collection.css', '/shelves.css', '/cabinet.css', '/cabinet-drag.js', '/cabinet-render.css', '/community.css', '/taste.js', '/taste-gesture.js', '/taste.css', '/interaction.css', '/assets/taste/engravings.png', '/assets/achievements/first.webp', '/assets/achievements/grapes.webp', '/assets/achievements/journey.webp', '/assets/achievements/cellar.webp', '/assets/achievements/five.webp', '/assets/achievements/atlas.webp', '/assets/achievements/winery.webp', '/assets/achievements/palette.webp', '/assets/cellar/original-desktop.webp', '/assets/cellar/original-mobile.webp', '/assets/cellar/alcove-desktop.webp', '/assets/cellar/alcove-mobile.webp', '/age-gate.js', '/assets/portal-logo.svg', '/assets/portal-card-data.json', '/assets/portal-card/public-rating.svg', '/assets/portal-card/public-rating-empty.svg', '/assets/portal-card/public-rating-wine-in-glass.svg', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png', '/icon-maskable.png', '/assets/playfair.woff2', '/assets/no-bottle.svg'];
SHELL.push('/favicon.svg', '/favicon.ico', '/favicon-32.png', '/apple-touch-icon.png');
self.addEventListener('install', event => { event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => (k.startsWith('wine-shell-')||k.startsWith('wine-server-')) && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) return;
  if (!SHELL.includes(url.pathname)) return;
  event.respondWith(fetch(event.request).then(async response => {
    // A disconnected HTTPS tunnel returns HTML 503 instead of a network error.
    // Keep the installed app and its history visible; never cache this error page.
    if (response.status >= 500) return (await caches.match(url.pathname)) || response;
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(c => c.put(url.pathname, copy)); }
    return response;
  }).catch(() => caches.match(url.pathname)));
});
