const CACHE_NAME = 'cronograma-v1';
const ASSETS = [
    '/cronograma/',
    '/static/img/unemi.png',
    '/static/img/icon-512.png',
    '/manifest.json'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(ASSETS))
    );
});

self.addEventListener('fetch', event => {
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
