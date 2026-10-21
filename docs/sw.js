// Работа без сети: страница и данные берутся из сети, а если сети нет — из последней сохранённой копии.
// Только свои файлы (тот же сайт); запросы к Мосбирже и другим сайтам не трогаем.
const CACHE = 'rynki-v1';
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const key = req.url.split('?')[0];              // ?v=… и ?номер-времени — одна копия на файл
    try {
      const res = await fetch(req);
      if (res && res.ok) cache.put(key, res.clone());
      return res;
    } catch (err) {
      const hit = await cache.match(key);
      if (hit) return hit;
      throw err;
    }
  })());
});
