const CACHE_NAME = 'ryan-pacific-asistencia-v4';
const ASSETS_TO_CACHE = [
  '/',
  '/static/index.html',
  '/static/manifest.json',
  '/static/logo.png',
  '/static/icon.png',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/jsQR.min.js'
];

// Instalación y guardado inicial en caché
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Guardando componentes esenciales en caché offline...');
      return cache.addAll(ASSETS_TO_CACHE);
    }).then(() => self.skipWaiting())
  );
});

// Activación y limpieza de versiones viejas de caché
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            console.log('[SW] Limpiando caché obsoleta:', key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Estrategia Network-First con fallback a Caché
self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // IMPORTANTE: Las llamadas al panel admin y a las APIs no deben ser interceptadas por el SW
  if (url.includes('/admin') || url.includes('/api/')) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        // Si hay red, actualizamos la copia de caché
        if (networkResponse && networkResponse.status === 200 && event.request.method === 'GET') {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return networkResponse;
      })
      .catch(() => {
        // Si no hay red (sótano, losa), devolver desde la memoria local del teléfono
        return caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          if (event.request.mode === 'navigate') {
            if (url.includes('/admin')) {
              return;
            }
            return caches.match('/');
          }
        });
      })
  );
});
