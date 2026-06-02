// Kita naikkan versinya jadi v2 biar HP lu otomatis ngebuang memori yang keracunan tadi
const CACHE_NAME = 'skripsikuu-v2'; 

// 🔥 HANYA SIMPAN FILE STATIS (GAMBAR/CSS) 🔥
// JANGAN PERNAH simpan rute dinamis kayak '/' atau '/dashboard' di sini!
const urlsToCache = [
    '/static/style.css',
    '/static/login.css',
    '/static/favicon.png',
    '/static/logo.png'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                return cache.addAll(urlsToCache);
            })
    );
});

// Bersihin memori (cache) versi v1 yang keracunan
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('Menghapus cache lama:', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        })
    );
});

// Strategi: "Tanya Server Dulu, Kalau Offline Baru Liat Memori"
self.addEventListener('fetch', event => {
    // Kalau yang diakses adalah halaman web (HTML)
    if (event.request.mode === 'navigate') {
        event.respondWith(
            fetch(event.request).catch(() => {
                // Biarin error kalau offline, jangan nampilin akun nyangkut
                return caches.match(event.request); 
            })
        );
    } else {
        // Kalau gambar/CSS, boleh ngambil dari cache biar cepet
        event.respondWith(
            caches.match(event.request).then(response => {
                return response || fetch(event.request);
            })
        );
    }
});