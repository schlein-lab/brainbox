
const CACHE = 'brainarbeit-connect-v1';
const SHELL = [
  './', './index.html', './app.js', './style.css', './manifest.webmanifest',
  '../core/src/index.js', '../core/src/client.js', '../core/src/contract.js',
  '../core/src/connection.js', '../core/src/keystore.js', '../core/src/pairing.js',
  '../core/src/media.js', '../core/src/intake.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);

  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/') ||
      url.pathname.startsWith('/share')) {
    return;
  }

  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request).then((res) => {
      if (e.request.method === 'GET' && res.ok && url.origin === self.location.origin) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match('./index.html')))
  );
});

self.addEventListener('push', (e) => {
  let data = {}; try { data = e.data ? e.data.json() : {}; } catch {}
  const title = data.title || 'Brainarbeit needs your approval';
  e.waitUntil(self.registration.showNotification(title, {
    body: data.body || 'Tap to review and approve.',
    data: { url: data.url || '/?view=approvals' },
    tag: data.tag || 'approval',
  }));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data?.url || '/?view=approvals'));
});
