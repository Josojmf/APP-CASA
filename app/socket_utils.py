from app import socketio, mongo
from app.globals import user_sockets
from flask import current_app, session
from datetime import datetime
import json
from pywebpush import webpush, WebPushException

# ============================================================
#   FUNCIONES PUSH
# ============================================================
def send_push_to_all(title, body, url="/", icon="/static/icons/house-icon.png"):
    """Envía notificación push a todos los usuarios con suscripción."""
    subscripciones = mongo.db.subscriptions.find({})
    for sub_doc in subscripciones:
        for sub in sub_doc.get("subscriptions", []):
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({
                        "title": title,
                        "body": body,
                        "icon": icon,
                        "badge": icon,
                        "url": url
                    }),
                    vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
                    vapid_claims=current_app.config["VAPID_CLAIMS"]
                )
                print(f"📲 Push enviado a {sub_doc['user']} ({sub['endpoint']})")
            except WebPushException as ex:
                print(f"❌ Error push a {sub_doc['user']} ({sub['endpoint']}):", repr(ex))
                if ex.response and ex.response.status_code == 410:
                    print("🧹 Eliminando subscripción expirada...")
                    mongo.db.subscriptions.update_one(
                        {"user": sub_doc["user"]},
                        {"$pull": {"subscriptions": {"endpoint": sub["endpoint"]}}}
                    )

# ============================================================
#   NOTIFICACIONES DE TAREAS
# ============================================================
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
                            "body": tarea.get("titulo", "Nueva tarea"),
                            "url": "/tareas"
                        }),
                        vapid_private_key=current_app.config["VAPID_PRIVATE_KEY"],
                        vapid_claims=current_app.config["VAPID_CLAIMS"]
                    )
                    print(f"📲 Notificación enviada a {username} ({sub['endpoint']})")
                except WebPushException as ex:
                    print(f"❌ Error enviando push a {username} ({sub['endpoint']}):", repr(ex))
                    if ex.response and ex.response.status_code == 410:
                        print("🧹 Eliminando subscripción expirada...")
                        mongo.db.subscriptions.update_one(
                            {"user": username},
                            {"$pull": {"subscriptions": {"endpoint": sub["endpoint"]}}}
                        )
        else:
            print(f"⚠️ Usuario {username} no tiene suscripción push registrada")

# ============================================================
#   EVENTOS DE CHAT
# ============================================================
def register_chat_events():
    """Registra eventos de chat en tiempo real con Socket.IO"""

    @socketio.on("send_message")
    def handle_send_message(data):
        # Usuario actual (preferir el que viene del cliente)
        user = data.get("user") or session.get("username", "Anónimo")
        
        # Foto que viene del cliente o de la sesión
        photo = (data.get("photo") or "").strip() or (session.get("photo") or "").strip()
        
        message = data.get("message", "").strip()
        if not message:
            return  # Evitar mensajes vacíos

        # 🔹 Si no tenemos foto, buscarla en Mongo
        if not photo or photo == "/static/images/default-avatar.png":
            user_doc = mongo.db.users.find_one({"nombre": user})
            if user_doc and user_doc.get("imagen"):
                if str(user_doc["imagen"]).startswith("data:image"):
                    photo = user_doc["imagen"]
                else:
                    photo = f"data:image/jpeg;base64,{user_doc['imagen']}"
            else:
                photo = "/static/images/default-avatar.png"

        # Guardar mensaje en MongoDB
        msg_doc = {
            "user": user,
            "photo": photo,
            "message": message,
            "timestamp": datetime.utcnow()
        }
        mongo.db.messages.insert_one(msg_doc)

        # Emitir mensaje a todos
        socketio.emit("chat_message", {
            "user": msg_doc["user"],
            "photo": msg_doc["photo"],
            "message": msg_doc["message"],
            "timestamp": msg_doc["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        })

        # Notificación push
        send_push_to_all(
            title=f"💬 Mensaje nuevo de {user}",
            body=message,
            url="/chat"
        )
