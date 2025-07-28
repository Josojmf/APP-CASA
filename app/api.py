from flask import Blueprint, jsonify, request, current_app
from app import mongo
from bson import ObjectId
from pywebpush import webpush, WebPushException
import json
from app.sockets_utils import notificar_tarea_a_usuario
from flask import session
from urllib.parse import urlparse
from datetime import datetime


def get_vapid_claims(endpoint_url):
    parsed = urlparse(endpoint_url)
    return {
        "aud": f"{parsed.scheme}://{parsed.netloc}",
        "sub": "mailto:joso.jmf@gmail.com"  # o tu email
    }



api = Blueprint('api', __name__)

@api.route('/api/users', methods=['GET'])
def get_users():
    users = list(mongo.db.users.find())
    for user in users:
        user['_id'] = str(user['_id'])
    return jsonify(users)

@api.route('/api/toggle_encasa', methods=['POST'])
def toggle_encasa():
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

    mensaje = f"{user['nombre']} {'ha llegado a casa 🏠' if new_status else 'ha salido de casa 🚶‍♂️'}"

    # 🔐 Obtener clave privada
    vapid_private_key = current_app.config["VAPID_PRIVATE_KEY"]

    # 🔍 Buscar TODAS las subscripciones (de todos los usuarios)
    subscripciones = mongo.db.subscriptions.find()

    # 📤 Enviar notificación a cada una de las subs
    for sub_doc in subscripciones:
        for sub in sub_doc.get("subscriptions", []):
            try:
                endpoint = sub.get("endpoint")
                if not endpoint:
                    continue  # Skip si endpoint no válido

                claims = get_vapid_claims(endpoint)
                webpush(
                    subscription_info=sub,
                    data=json.dumps({
                        "title": "House App",
                        "body": mensaje,
                        "tag": "encasa-status"
                    }),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=claims
                )
                print(f"✅ Notificación enviada a {sub_doc['user']}: {endpoint}")
            except WebPushException as ex:
                print(f"❌ Error en {sub.get('endpoint', 'sin endpoint')}: {repr(ex)}")

    return jsonify({"success": True, "new_status": new_status})


@api.route('/api/add_task', methods=['POST'])
def add_task():
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
        "asignado": asignee  # solo con esto basta
    }

    mongo.db.users.update_one(
        {"_id": user["_id"]},
        {"$push": {"tareas": nueva_tarea}}
    )

    notificar_tarea_a_usuario(nueva_tarea)  # 🎯 Solo le llega al usuario asignado

    return jsonify({"success": True})

@api.route('/api/completar_tarea', methods=['POST'])
def completar_tarea():
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
        print(f"[ERROR al completar tarea]: {e}")
        return jsonify({"error": "Error al borrar tarea"}), 500

@api.route("/api/save_subscription", methods=["POST"])
def save_subscription():
    from flask import request, session
    user = session.get("user")
    if not user:
        return jsonify({"error": "Not logged in"}), 401

    subscription = request.get_json()

    # Verificar si ya existe alguna subscripción para ese usuario
    existing = mongo.db.subscriptions.find_one({"user": user})

    if existing:
        # Si ya tiene esa misma suscripción (por endpoint), no la volvemos a guardar
        if not any(s["endpoint"] == subscription["endpoint"] for s in existing["subscriptions"]):
            mongo.db.subscriptions.update_one(
                {"user": user},
                {"$push": {"subscriptions": subscription}}
            )
    else:
        # Primer registro de suscripciones para este usuario
        mongo.db.subscriptions.insert_one({
            "user": user,
            "subscriptions": [subscription]
        })

    return jsonify({"message": "Suscripción guardada correctamente"})


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

@api.route("/api/lista_compra_all", methods=["DELETE"])
def eliminar_todos_items():
    mongo.db.lista_compra.delete_many({})
    return jsonify({"success": True})
    
@api.route("/api/ubicacion", methods=["POST"])
def recibir_ubicacion():
    data = request.get_json()

    if not data or "lat" not in data or "lon" not in data or "tid" not in data:
        return jsonify({"error": "Faltan datos obligatorios"}), 400

    mongo.db.ubicaciones.insert_one({
        "usuario": data.get("tid"),  # Ej: "joso"
        "lat": data["lat"],
        "lon": data["lon"],
        "timestamp": datetime.utcnow()
    })

    return jsonify({"ok": True})

@api.route('/api/owntracks', methods=['POST'])
def recibir_ubicacion_owntracks():
    data = request.get_json()

    if not data or "lat" not in data or "lon" not in data or "_type" != "location":
        return jsonify({"error": "Datos inválidos"}), 400

    user = data.get("user") or session.get("user")
    if not user:
        return jsonify({"error": "Sin usuario"}), 400

    mongo.db.ubicaciones.update_one(
        {"user": user},
        {"$set": {
            "lat": data["lat"],
            "lon": data["lon"],
            "time": data.get("tst"),  # timestamp unix
        }},
        upsert=True
    )

    return jsonify({"ok": True})
