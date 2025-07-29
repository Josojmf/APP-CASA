self.addEventListener('push', function(event) {
    let data = {};
    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            console.error("❌ Error al parsear datos push:", e);
        }
    }

    const options = {
        body: data.body || "¡Tienes una nueva notificación!",
        icon: data.icon || '/static/icons/house-icon.png',
        badge: data.badge || '/static/icons/house-icon.png',
        vibrate: [200, 100, 200],
        data: {
            url: data.url || '/' // 👈 usamos la URL recibida
        }
    };

    event.waitUntil(
        self.registration.showNotification(data.title || "House App", options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then(function(clientList) {
            for (const client of clientList) {
                if (client.url.includes(event.notification.data.url) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(event.notification.data.url);
            }
        })
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then(function(clientList) {
            for (const client of clientList) {
                if (client.url === event.notification.data.url && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(event.notification.data.url);
            }
        })
    );
});
