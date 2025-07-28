from app import socketio, mongo
from app.globals import user_sockets
from flask import current_app
import json
from pywebpush import webpush, WebPushException

def notificar_tarea_a_usuario(tarea):
    username = tarea.get("asignado", "").lower()
    if not username:
        print("⚠️ Tarea sin asignado")
        return

    sid = user_sockets.get(username)

    if sid:
        socketio.emit("nueva_tarea", tarea, to=sid)
        print(f"✅ Notificada tarea a {username} (socket {sid})")
    else:
        print(f"⚠️ Usuario {username} no conectado. Intentando push...")

        subscripcion = mongo.db.subscriptions.find_one({"user": username})
        if subscripcion:
            for sub in subscripcion.get("subscriptions", []):
                try:
                    webpush(
                        subscription_info=sub,
                        data=json.dumps({
                            "title": "Tarea asignada",
                            "body": tarea.get("titulo", "Nueva tarea")
                        }),
                        vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
                        vapid_claims=current_app.config["VAPID_CLAIMS"]
                    )
                    print(f"📲 Notificación enviada a {username} ({sub['endpoint']})")
                except WebPushException as ex:
                    print(f"❌ Error enviando push a {username} ({sub['endpoint']}):", repr(ex))

                    # Limpieza automática si está caducada (410 Gone)
                    if ex.response and ex.response.status_code == 410:
                        print("🧹 Eliminando subscripción expirada...")
                        mongo.db.subscriptions.update_one(
                            {"user": username},
                            {"$pull": {"subscriptions": {"endpoint": sub["endpoint"]}}}
                        )
        else:
            print(f"⚠️ Usuario {username} no tiene suscripción push registrada")
