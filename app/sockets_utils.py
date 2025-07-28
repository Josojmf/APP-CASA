from app import socketio, mongo
from app.globals import user_sockets
from flask import current_app
import json
from pywebpush import webpush, WebPushException

def notificar_tarea_a_usuario(tarea):
    username = tarea.get("asignado")
    if not username:
        print("⚠️ Tarea sin asignado")
        return

    sid = user_sockets.get(username)

    if sid:
        socketio.emit("nueva_tarea", tarea, to=sid)
        print(f"✅ Notificada tarea a {username} (socket {sid})")
    else:
        print(f"⚠️ Usuario {username} no conectado. Intentando push...")

        subscripcion = mongo.db.subscriptions.find_one({"usuario": username})
        if subscripcion:
            try:
                webpush(
                    subscription_info=subscripcion,
                    data=json.dumps({
                        "title": "Tarea asignada",
                        "body": tarea.get("titulo", "Nueva tarea")
                    }),
                    vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
                    vapid_claims=current_app.config["VAPID_CLAIMS"]
                )
                print(f"📲 Notificación push enviada a {username}")
            except WebPushException as ex:
                print(f"❌ Error enviando push a {username}:", repr(ex))
        else:
            print(f"⚠️ Usuario {username} no tiene suscripción push registrada")
