from flask import Blueprint, jsonify, request, current_app, session
from app import mongo
from bson import ObjectId
from pywebpush import webpush, WebPushException
import json
from app.socket_utils import notificar_tarea_a_usuario
from urllib.parse import urlparse
from datetime import datetime
import traceback

# ==========================================
# Mapeo entre TID de OwnTracks y nombre de usuario real
# ==========================================
TID_TO_USERNAME = {
    "jo": "Joso",
    "an": "Ana",
    "pa": "Papa",
    "ma": "Mama"
}

# ==========================================
# Utilidad para obtener claims VAPID
# ==========================================
def get_vapid_claims(endpoint_url):
    """
    Genera el diccionario de claims VAPID para webpush.
    """
    parsed = urlparse(endpoint_url)
    return {
        "aud": f"{parsed.scheme}://{parsed.netloc}",
        "sub": "mailto:joso.jmf@gmail.com"
    }

# ==========================================
# Definición del Blueprint API
# ==========================================
api = Blueprint('api', __name__)

# ===================================================
# Endpoint: Obtener lista de usuarios
# ===================================================
@api.route('/api/users', methods=['GET'])
def get_users():
    """
    Devuelve todos los usuarios de la base de datos.
    """
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])
        return jsonify(users)
    except Exception as e:
        print(f"[ERROR] get_users: {e}")
        return jsonify({"error": "No se pudieron obtener los usuarios"}), 500

# ===================================================
# Endpoint: Alternar estado 'en casa'
# ===================================================
@api.route('/api/toggle_encasa', methods=['POST'])
def toggle_encasa():
    """
    Alterna el estado 'encasa' de un usuario por su ID.
    Envía notificación push a todos los usuarios.
    """
    user_id = request.json.get('user_id')
    if not user_id:
        return jsonify({"error": "Falta user_id"}), 400

    try:
        obj_id = ObjectId(user_id)
    except Exception:
        return jsonify({"error": "ID no válido"}), 400

    user = mongo.db.users.find_one({"_id": obj_id})
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404

    new_status = not user.get('encasa', False)
    mongo.db.users.update_one({"_id": obj_id}, {"$set": {"encasa": new_status}})
    hora = datetime.now().strftime("%H:%M") 
    hora = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    mensaje = f"{user['nombre']} {'ha llegado a casa 🏠' if new_status else 'ha salido de casa 🚶‍♂️'} a las {hora}"
    send_push_to_all(
        title="House App",
        body=mensaje,
        url="/usuarios"
    )

    return jsonify({"success": True, "new_status": new_status})

# ===================================================
# Endpoint: Añadir tarea a usuario
# ===================================================
@api.route('/api/add_task', methods=['POST'])
def add_task():
    """
    Añade una tarea a un usuario y le envía notificación push solo a él.
    """
    data = request.get_json()
    titulo = data.get('titulo')
    asignee = data.get('asignee')
    due_date = data.get('due_date')
    pasos = data.get('pasos', '')

    if not titulo or not asignee or not due_date:
        return jsonify({"error": "Faltan campos obligatorios"}), 400

    user = mongo.db.users.find_one({"nombre": asignee})
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404

    nueva_tarea = {
        "titulo": titulo,
        "due_date": due_date,
        "pasos": pasos,
        "asignado": asignee
    }

    mongo.db.users.update_one(
        {"_id": user["_id"]},
        {"$push": {"tareas": nueva_tarea}}
    )

    notificar_tarea_a_usuario(nueva_tarea)

    return jsonify({"success": True})

# ===================================================
# Endpoint: Completar tarea
# ===================================================
@api.route('/api/completar_tarea', methods=['POST'])
def completar_tarea():
    """
    Marca una tarea como completada y la elimina de la base de datos.
    """
    data = request.get_json()
    user_id = data.get('user_id')
    tarea = data.get('tarea')

    if not user_id or not tarea:
        return jsonify({"error": "Datos incompletos"}), 400

    try:
        mongo.db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$pull": {
                "tareas": {
                    "titulo": tarea['titulo'],
                    "due_date": tarea['due_date']
                }
            }}
        )
        mongo.db.tareas.delete_one({
            "usuario": mongo.db.users.find_one({"_id": ObjectId(user_id)})["nombre"],
            "titulo": tarea['titulo'],
            "due_date": tarea['due_date']
        })
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[ERROR completar_tarea]: {traceback.format_exc()}")
        return jsonify({"error": "Error al borrar tarea"}), 500

# ===================================================
# Endpoint: Guardar suscripción de notificaciones
# ===================================================
@api.route("/api/save_subscription", methods=["POST"])
def save_subscription():
    """
    Guarda la suscripción push de un usuario logueado.
    Permite múltiples suscripciones por usuario.
    """
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not logged in"}), 401

    subscription = request.get_json()
    existing = mongo.db.subscriptions.find_one({"user": user})

    if existing:
        if not any(s["endpoint"] == subscription["endpoint"] for s in existing["subscriptions"]):
            mongo.db.subscriptions.update_one(
                {"user": user},
                {"$push": {"subscriptions": subscription}}
            )
    else:
        mongo.db.subscriptions.insert_one({
            "user": user,
            "subscriptions": [subscription]
        })

    return jsonify({"message": "Suscripción guardada correctamente"})

# ===================================================
# CRUD Lista de la compra
# ===================================================
@api.route("/api/lista_compra", methods=["GET"])
def obtener_lista():
    items = list(mongo.db.lista_compra.find())
    for item in items:
        item["_id"] = str(item["_id"])
    return jsonify(items)

@api.route("/api/lista_compra", methods=["POST"])
def agregar_item():
    data = request.get_json()
    mongo.db.lista_compra.insert_one({
        "nombre": data["nombre"],
        "cantidad": data.get("cantidad", "1"),
        "comprado": False
    })
    return jsonify({"success": True})

@api.route("/api/lista_compra/<item_id>", methods=["DELETE"])
def eliminar_item(item_id):
    mongo.db.lista_compra.delete_one({"_id": ObjectId(item_id)})
    return jsonify({"success": True})

@api.route("/api/lista_compra/<item_id>/toggle", methods=["PATCH"])
def marcar_comprado(item_id):
    item = mongo.db.lista_compra.find_one({"_id": ObjectId(item_id)})
    if not item:
        return jsonify({"error": "No encontrado"}), 404
    mongo.db.lista_compra.update_one(
        {"_id": ObjectId(item_id)},
        {"$set": {"comprado": not item["comprado"]}}
    )
    return jsonify({"success": True})

# ===================================================
# Endpoint: Recibir ubicación OwnTracks (histórico)
# ===================================================
@api.route("/api/ubicacion", methods=["POST"])
def recibir_ubicacion():
    """
    Guarda la ubicación recibida de OwnTracks, manteniendo histórico.
    """
    data = request.get_json()
    if not data or "lat" not in data or "lon" not in data or "tid" not in data:
        return jsonify({"error": "Faltan datos obligatorios"}), 400

    tid = data.get("tid")
    usuario = TID_TO_USERNAME.get(tid)
    if not usuario:
        return jsonify({"error": "Usuario no reconocido"}), 400

    lat = data["lat"]
    lon = data["lon"]
    ts = datetime.utcnow()

    mongo.db.ubicaciones.insert_one({
        "usuario": usuario,
        "lat": lat,
        "lon": lon,
        "timestamp": ts
    })

    mongo.db.users.update_one(
        {"nombre": usuario},
        {"$set": {"last_location": {"lat": lat, "lon": lon, "time": ts}}}
    )

    return jsonify({"ok": True})

# ===================================================
# Endpoint: Recibir posición (última ubicación)
# ===================================================
@api.route("/api/owntracks", methods=["POST"])
def recibir_posicion():
    data = request.get_json()
    lat = data.get("lat")
    lon = data.get("lon")
    user = data.get("user")
    time = data.get("tst") or datetime.utcnow()

    if lat is None or lon is None or not user:
        return jsonify({"error": "Datos incompletos"}), 400

    mongo.db.ubicaciones.update_one(
        {"usuario": user},
        {"$set": {"lat": lat, "lon": lon, "timestamp": time}},
        upsert=True
    )

    mongo.db.users.update_one(
        {"nombre": user},
        {"$set": {
            "last_location": {
                "lat": lat,
                "lon": lon,
                "time": time
            }
        }}
    )

    return jsonify({"success": True})

# ===================================================
# Endpoint: Obtener ubicaciones para el mapa
# ===================================================
@api.route('/api/ubicaciones')
def obtener_ubicaciones():
    ubicaciones = list(mongo.db.ubicaciones.find().sort("tst", -1))
    resultado = []
    for ubicacion in ubicaciones:
        time_value = ubicacion.get("tst")
        if isinstance(time_value, datetime):
            time_value = time_value.isoformat()
        
        resultado.append({
            "user": ubicacion.get("user"),
            "lat": ubicacion.get("lat"),
            "lon": ubicacion.get("lon"),
            "time": time_value
        })
    print("📤 Enviando ubicaciones al mapa:", resultado)
    return jsonify(resultado)

# ===================================================
# Función: Enviar push a todas las suscripciones
# ===================================================
def send_push_to_all(title, body, url="/", icon="/static/icons/house-icon.png"):
    vapid_private_key = current_app.config["VAPID_PRIVATE_KEY"]
    subscripciones = mongo.db.subscriptions.find()

    for sub_doc in subscripciones:
        for sub in sub_doc.get("subscriptions", []):
            try:
                endpoint = sub.get("endpoint")
                if not endpoint:
                    continue
                claims = get_vapid_claims(endpoint)
                webpush(
                    subscription_info=sub,
                    data=json.dumps({
                        "title": title,
                        "body": body,
                        "icon": icon,
                        "url": url
                    }),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=claims
                )
                print(f"✅ Notificación enviada a {sub_doc['user']}")
            except WebPushException as ex:
                print(f"❌ Error al enviar push a {sub_doc['user']}: {repr(ex)}")
            except Exception as e:
                print(f"❌ Error inesperado al enviar push: {traceback.format_exc()}")
