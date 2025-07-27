from flask import Blueprint, jsonify, request, current_app
from app import mongo
from bson import ObjectId
from pywebpush import webpush, WebPushException
import json

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

    # Obtener claves y claims desde config
    vapid_private_key = current_app.config["VAPID_PRIVATE_KEY"]
    vapid_claims = current_app.config["VAPID_CLAIMS"]

    # Obtener suscripciones
    subs = mongo.db.subscriptions.find()
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=json.dumps({"title": "House App", "body": mensaje}),
                vapid_private_key=vapid_private_key,
                vapid_claims=vapid_claims
            )
            print(f"✅ Notificación enviada: {mensaje}")
        except WebPushException as ex:
            print("❌ Error enviando notificación:", repr(ex))

    return jsonify({"success": True, "new_status": new_status})

@api.route('/api/completar_tarea', methods=['POST'])
def completar_tarea():
    data = request.json
    user_id = data.get('user_id')
    tarea = data.get('tarea')

    if not user_id or not tarea:
        return jsonify({"error": "Datos incompletos"}), 400

    try:
        obj_id = ObjectId(user_id)
    except Exception:
        return jsonify({"error": "ID no válido"}), 400

    result = mongo.db.users.update_one(
        {"_id": obj_id},
        {"$pull": {"tareas": tarea}}
    )

    if result.modified_count == 0:
        return jsonify({"error": "Tarea no encontrada"}), 404

    return jsonify({"success": True})

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
        "pasos": pasos
    }

    mongo.db.users.update_one(
        {"_id": user["_id"]},
        {"$push": {"tareas": nueva_tarea}}
    )

    return jsonify({"success": True})

@api.route('/api/save_subscription', methods=['POST'])
def save_subscription():
    subscription = request.get_json()
    if not subscription:
        return jsonify({"error": "No se recibió suscripción"}), 400
    mongo.db.subscriptions.insert_one(subscription)
    return jsonify({"success": True})

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
    