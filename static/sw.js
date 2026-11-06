const CACHE_NAME = 'ryan-pacific-asistencia-v8';
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

// Función auxiliar para no congelar la pantalla de inicio en Android si la red tarda
function fetchConTimeout(request, ms = 2500) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Timeout red')), ms);
    fetch(request).then(
      res => { clearTimeout(timer); resolve(res); },
      err => { clearTimeout(timer); reject(err); }
    );
  });
}

// Estrategia Network-First con fallback a Caché
self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // IMPORTANTE: Las llamadas al panel admin y a las APIs no deben ser interceptadas por el SW
  if (url.includes('/admin') || url.includes('/api/')) {
    return;
  }

  // Navegación principal (cargar la página de inicio sin pantalla negra):
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetchConTimeout(event.request, 2200)
        .then((res) => {
          if (res && res.status === 200) {
            const clone = res.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          }
          return res;
        })
        .catch(() => {
          return caches.match('/').then(cached => cached || fetch(event.request));
        })
    );
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && event.request.method === 'GET') {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return networkResponse;
      })
      .catch(() => {
        return caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          if (event.request.mode === 'navigate') {
            return caches.match('/');
          }
        });
      })
  );
});

// ================= NOTIFICACIONES PUSH Y ALERTAS DE CELULAR (Punto 11) =================
self.addEventListener('push', (event) => {
  let data = { title: '📢 AVISO DE EMPRESA', body: 'Tienes un nuevo comunicado oficial.', url: '/' };
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      data.body = event.data.text();
    }
  }
  const options = {
    body: data.body,
    icon: '/static/icons/icon-192.png',
    badge: '/static/icon.png',
    vibrate: [250, 100, 250, 100, 450],
    data: { url: data.url || '/' },
    requireInteraction: true,
    actions: [
      { action: 'open', title: '👀 Ver Aviso' }
    ]
  };
  event.waitUntil(
    self.registration.showNotification(data.title || '📢 AVISO DE EMPRESA', options)
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
