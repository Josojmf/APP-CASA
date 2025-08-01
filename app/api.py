from flask import Blueprint, jsonify, request, current_app, session
from app import mongo
from bson import ObjectId
from pywebpush import webpush, WebPushException
import json
from app.socket_utils import notificar_tarea_a_usuario
from urllib.parse import urlparse
from datetime import datetime, timedelta
import traceback
import base64
import os
import requests
from functools import wraps


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
# Decorador para autenticación
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return jsonify({"error": "Autenticación requerida"}), 401
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# Utilidad para obtener claims VAPID
# ==========================================
def get_vapid_claims(endpoint_url):
    """
    Genera el diccionario de claims VAPID para webpush.
    """
    try:
        parsed = urlparse(endpoint_url)
        return {
            "aud": f"{parsed.scheme}://{parsed.netloc}",
            "sub": "mailto:joso.jmf@gmail.com"
        }
    except Exception as e:
        print(f"[ERROR] get_vapid_claims: {e}")
        return None

# ==========================================
# Definición del Blueprint API
# ==========================================
api = Blueprint('api', __name__)

# ===================================================
# Endpoint: Obtener lista de usuarios
# ===================================================
@api.route('/api/users', methods=['GET'])
@login_required
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
@login_required
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
    
    # Actualizar con timestamp
    mongo.db.users.update_one(
        {"_id": obj_id}, 
        {
            "$set": {
                "encasa": new_status,
                "last_status_change": datetime.now()
            }
        }
    )
    
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
@login_required
def add_task():
    """
    Añade una tarea a un usuario y le envía notificación push solo a él.
    """
    data = request.get_json()
    titulo = data.get('titulo')
    asignee = data.get('asignee')
    due_date = data.get('due_date')
    pasos = data.get('pasos', '')
    prioridad = data.get('prioridad', 'normal')

    if not titulo or not asignee or not due_date:
        return jsonify({"error": "Faltan campos obligatorios"}), 400

    # Validar fecha
    try:
        datetime.strptime(due_date, '%Y-%m-%d')
    except ValueError:
        return jsonify({"error": "Formato de fecha inválido"}), 400

    user = mongo.db.users.find_one({"nombre": asignee})
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404

    nueva_tarea = {
        "titulo": titulo,
        "due_date": due_date,
        "pasos": pasos,
        "asignado": asignee,
        "prioridad": prioridad,
        "created_by": session.get('user'),
        "created_at": datetime.now(),
        "completed": False
    }

    mongo.db.users.update_one(
        {"_id": user["_id"]},
        {"$push": {"tareas": nueva_tarea}}
    )

    # Enviar notificación solo al usuario asignado
    send_push_to_user(
        user_name=asignee,
        title="Nueva tarea asignada 📋",
        body=f"Se te ha asignado: {titulo}",
        url="/tareas"
    )

    return jsonify({"success": True})

# ===================================================
# Endpoint: Completar tarea
# ===================================================
@api.route('/api/completar_tarea', methods=['POST'])
@login_required
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
        # Registrar tarea completada antes de eliminar
        user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            completed_task = {
                "titulo": tarea['titulo'],
                "due_date": tarea['due_date'],
                "completed_by": user['nombre'],
                "completed_at": datetime.now(),
                "completed_by_session": session.get('user')
            }
            mongo.db.completed_tasks.insert_one(completed_task)

        # Eliminar de tareas activas
        mongo.db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$pull": {
                "tareas": {
                    "titulo": tarea['titulo'],
                    "due_date": tarea['due_date']
                }
            }}
        )
        
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[ERROR completar_tarea]: {traceback.format_exc()}")
        return jsonify({"error": "Error al completar tarea"}), 500

# ===================================================
# Endpoint: Guardar suscripción de notificaciones
# ===================================================
@api.route("/api/save_subscription", methods=["POST"])
@login_required
def save_subscription():
    """
    Guarda la suscripción push de un usuario logueado.
    Permite múltiples suscripciones por usuario.
    """
    user = session.get("user")
    subscription = request.get_json()
    
    if not subscription or not subscription.get("endpoint"):
        return jsonify({"error": "Suscripción inválida"}), 400

    existing = mongo.db.subscriptions.find_one({"user": user})

    if existing:
        # Evitar duplicados
        if not any(s["endpoint"] == subscription["endpoint"] for s in existing["subscriptions"]):
            mongo.db.subscriptions.update_one(
                {"user": user},
                {"$push": {"subscriptions": subscription}}
            )
    else:
        mongo.db.subscriptions.insert_one({
            "user": user,
            "subscriptions": [subscription],
            "created_at": datetime.now()
        })

    return jsonify({"message": "Suscripción guardada correctamente"})

# ===================================================
# CRUD Lista de la compra (MEJORADO)
# ===================================================
@api.route("/api/lista_compra", methods=["GET"])
@login_required
def obtener_lista():
    """Obtener todos los items de la lista de compra"""
    try:
        items = list(mongo.db.lista_compra.find().sort("created_at", -1))
        for item in items:
            item["_id"] = str(item["_id"])
        return jsonify(items)
    except Exception as e:
        print(f"[ERROR] obtener_lista: {e}")
        return jsonify({"error": "Error al obtener la lista"}), 500

@api.route("/api/lista_compra", methods=["POST"])
@login_required
def agregar_item():
    """Agregar nuevo item a la lista de compra"""
    try:
        data = request.get_json()
        nombre = data.get("nombre", "").strip()
        cantidad = data.get("cantidad", "1")
        unidad = data.get("unidad", "").strip()

        if not nombre:
            return jsonify({"error": "El nombre del producto es obligatorio"}), 400

        # Verificar si ya existe el producto
        existing = mongo.db.lista_compra.find_one({"nombre": {"$regex": f"^{nombre}$", "$options": "i"}})
        if existing:
            return jsonify({"error": "Este producto ya está en la lista"}), 409

        new_item = {
            "nombre": nombre,
            "cantidad": str(cantidad),
            "unidad": unidad,
            "comprado": False,
            "created_by": session.get('user'),
            "created_at": datetime.now()
        }

        result = mongo.db.lista_compra.insert_one(new_item)
        
        # Notificar a todos los usuarios
        send_push_to_all(
            title="Lista de compra actualizada 🛒",
            body=f"{session.get('user')} añadió: {nombre}",
            url="/lista_compra"
        )

        return jsonify({"success": True, "id": str(result.inserted_id)})
    except Exception as e:
        print(f"[ERROR] agregar_item: {e}")
        return jsonify({"error": "Error al agregar el item"}), 500

@api.route("/api/lista_compra/<item_id>", methods=["PUT"])
@login_required
def editar_item(item_id):
    """Editar un item de la lista de compra"""
    try:
        data = request.get_json()
        nombre = data.get("nombre", "").strip()
        cantidad = data.get("cantidad", "1")
        unidad = data.get("unidad", "").strip()

        if not nombre:
            return jsonify({"error": "El nombre del producto es obligatorio"}), 400

        result = mongo.db.lista_compra.update_one(
            {"_id": ObjectId(item_id)},
            {"$set": {
                "nombre": nombre,
                "cantidad": str(cantidad),
                "unidad": unidad,
                "updated_by": session.get('user'),
                "updated_at": datetime.now()
            }}
        )

        if result.matched_count == 0:
            return jsonify({"error": "Item no encontrado"}), 404

        return jsonify({"success": True})
    except Exception as e:
        print(f"[ERROR] editar_item: {e}")
        return jsonify({"error": "Error al editar el item"}), 500

@api.route("/api/lista_compra/<item_id>", methods=["DELETE"])
@login_required
def eliminar_item(item_id):
    """Eliminar un item de la lista de compra"""
    try:
        # Obtener el item antes de eliminarlo para notificación
        item = mongo.db.lista_compra.find_one({"_id": ObjectId(item_id)})
        if not item:
            return jsonify({"error": "Item no encontrado"}), 404

        mongo.db.lista_compra.delete_one({"_id": ObjectId(item_id)})
        
        # Notificar eliminación
        send_push_to_all(
            title="Producto eliminado de la lista 🗑️",
            body=f"{session.get('user')} eliminó: {item['nombre']}",
            url="/lista_compra"
        )

        return jsonify({"success": True})
    except Exception as e:
        print(f"[ERROR] eliminar_item: {e}")
        return jsonify({"error": "Error al eliminar el item"}), 500

@api.route("/api/lista_compra_all", methods=["DELETE"])
@login_required
def eliminar_toda_lista():
    """Eliminar todos los items de la lista de compra"""
    try:
        result = mongo.db.lista_compra.delete_many({})
        
        # Notificar limpieza completa
        send_push_to_all(
            title="Lista de compra vaciada 🧹",
            body=f"{session.get('user')} vació completamente la lista",
            url="/lista_compra"
        )

        return jsonify({"success": True, "deleted_count": result.deleted_count})
    except Exception as e:
        print(f"[ERROR] eliminar_toda_lista: {e}")
        return jsonify({"error": "Error al vaciar la lista"}), 500

@api.route("/api/lista_compra/<item_id>/toggle", methods=["PATCH"])
@login_required
def marcar_comprado(item_id):
    """Marcar/desmarcar item como comprado"""
    try:
        item = mongo.db.lista_compra.find_one({"_id": ObjectId(item_id)})
        if not item:
            return jsonify({"error": "Item no encontrado"}), 404
            
        new_status = not item.get("comprado", False)
        
        mongo.db.lista_compra.update_one(
            {"_id": ObjectId(item_id)},
            {"$set": {
                "comprado": new_status,
                "marked_by": session.get('user'),
                "marked_at": datetime.now()
            }}
        )
        
        return jsonify({"success": True, "comprado": new_status})
    except Exception as e:
        print(f"[ERROR] marcar_comprado: {e}")
        return jsonify({"error": "Error al marcar el item"}), 500

# ===================================================
# Funciones de notificaciones mejoradas
# ===================================================
def send_push_to_user(user_name, title, body, url="/", icon="/static/icons/house-icon.png"):
    """Enviar notificación push a un usuario específico"""
    try:
        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            print("❌ VAPID_PRIVATE_KEY no configurada")
            return False

        subscription_doc = mongo.db.subscriptions.find_one({"user": user_name})
        if not subscription_doc:
            print(f"❌ No hay suscripciones para {user_name}")
            return False

        success_count = 0
        for sub in subscription_doc.get("subscriptions", []):
            try:
                endpoint = sub.get("endpoint")
                if not endpoint:
                    continue
                    
                claims = get_vapid_claims(endpoint)
                if not claims:
                    continue

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
                success_count += 1
                print(f"✅ Notificación enviada a {user_name}")
                
            except WebPushException as ex:
                print(f"❌ Error al enviar push a {user_name}: {repr(ex)}")
                # Limpiar suscripciones inválidas
                if ex.response and ex.response.status_code in [410, 404]:
                    mongo.db.subscriptions.update_one(
                        {"user": user_name},
                        {"$pull": {"subscriptions": sub}}
                    )
            except Exception as e:
                print(f"❌ Error inesperado: {e}")

        return success_count > 0
        
    except Exception as e:
        print(f"❌ Error en send_push_to_user: {e}")
        return False

def send_push_to_all(title, body, url="/", icon="/static/icons/house-icon.png"):
    """Enviar notificación push a todos los usuarios suscritos"""
    try:
        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            print("❌ VAPID_PRIVATE_KEY no configurada")
            return

        subscripciones = mongo.db.subscriptions.find()
        total_sent = 0

        for sub_doc in subscripciones:
            user_name = sub_doc.get("user", "Unknown")
            for sub in sub_doc.get("subscriptions", []):
                try:
                    endpoint = sub.get("endpoint")
                    if not endpoint:
                        continue
                        
                    claims = get_vapid_claims(endpoint)
                    if not claims:
                        continue

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
                    total_sent += 1
                    print(f"✅ Notificación enviada a {user_name}")
                    
                except WebPushException as ex:
                    print(f"❌ Error al enviar push a {user_name}: {repr(ex)}")
                    # Limpiar suscripciones inválidas
                    if ex.response and ex.response.status_code in [410, 404]:
                        mongo.db.subscriptions.update_one(
                            {"user": user_name},
                            {"$pull": {"subscriptions": sub}}
                        )
                except Exception as e:
                    print(f"❌ Error inesperado al enviar push: {e}")

        print(f"📊 Total notificaciones enviadas: {total_sent}")
        
    except Exception as e:
        print(f"❌ Error en send_push_to_all: {e}")

# ===================================================
# Endpoint: Perfil de usuario
# ===================================================
@api.route("/api/user/profile/<nombre>", methods=["GET"])
@login_required
def get_user_profile(nombre):
    """Obtener perfil de usuario con avatar"""
    try:
        user = mongo.db.users.find_one({"nombre": nombre})
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404

        imagen_data = user.get("imagen")
        if not imagen_data:
            return jsonify({
                "avatar": "/static/images/default-avatar.png",
                "nombre": user.get("nombre"),
                "encasa": user.get("encasa", False)
            })

        return jsonify({
            "avatar": f"data:image/jpeg;base64,{imagen_data}",
            "nombre": user.get("nombre"),
            "encasa": user.get("encasa", False),
            "last_status_change": user.get("last_status_change")
        })
    except Exception as e:
        print(f"[ERROR] get_user_profile: {e}")
        return jsonify({"error": "Error al obtener perfil"}), 500

# ===================================================
# Endpoint: Chat con IA familiar
# ===================================================
@api.route("/api/chatfd", methods=["POST"])
@login_required
def chat_familiar():
    """Chat con asistente familiar usando Groq"""
    try:
        data = request.get_json()
        q = data.get("prompt", "").strip()
        
        if not q:
            return jsonify({"error": "Prompt vacío"}), 400

        # Inicializar historial si no existe
        if "chat_history" not in session:
            session["chat_history"] = [{
                "role": "system", 
                "content": "Eres un asistente familiar útil y amigable. Ayudas con tareas domésticas, organización familiar y consejos del hogar. Responde en español de manera concisa y práctica."
            }]

        # Limitar historial a últimos 20 mensajes para evitar tokens excesivos
        if len(session["chat_history"]) > 20:
            session["chat_history"] = session["chat_history"][:1] + session["chat_history"][-19:]

        session["chat_history"].append({"role": "user", "content": q})

        # Configurar API
        API_KEY = os.getenv("GROQ_API_KEY")
        if not API_KEY:
            return jsonify({"error": "API key no configurada"}), 500

        headers = {"Authorization": f"Bearer {API_KEY}"}
        payload = {
            "model": "llama-3.1-8b-instant",  # Modelo actualizado
            "messages": session["chat_history"],
            "max_tokens": 500,
            "temperature": 0.7
        }

        # Llamada a Groq con timeout
        resp = requests.post(
            "https://api.groq.cloud/v1/chat/completions", 
            json=payload, 
            headers=headers,
            timeout=30
        )
        
        if resp.status_code != 200:
            print(f"[ERROR] Groq API: {resp.status_code} - {resp.text}")
            return jsonify({"error": "Error en el servicio de IA"}), 500

        answer = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        
        if not answer:
            return jsonify({"error": "Respuesta vacía del asistente"}), 500

        # Añadir respuesta al historial
        session["chat_history"].append({"role": "assistant", "content": answer})

        return jsonify({"answer": answer})
        
    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout en el servicio de IA"}), 504
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Request error: {e}")
        return jsonify({"error": "Error de conexión con el servicio de IA"}), 503
    except Exception as e:
        print(f"[ERROR] chat_familiar: {traceback.format_exc()}")
        return jsonify({"error": "Error interno del servidor"}), 500

# ===================================================
# Endpoint: Estadísticas básicas
# ===================================================
@api.route("/api/stats", methods=["GET"])
@login_required
def get_stats():
    """Obtener estadísticas básicas de la aplicación"""
    try:
        stats = {
            "total_users": mongo.db.users.count_documents({}),
            "users_at_home": mongo.db.users.count_documents({"encasa": True}),
            "total_tasks": sum(len(user.get("tareas", [])) for user in mongo.db.users.find()),
            "shopping_items": mongo.db.lista_compra.count_documents({}),
            "completed_tasks_today": mongo.db.completed_tasks.count_documents({
                "completed_at": {"$gte": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)}
            })
        }
        return jsonify(stats)
    except Exception as e:
        print(f"[ERROR] get_stats: {e}")
        return jsonify({"error": "Error al obtener estadísticas"}), 500

# ===================================================
# Endpoint: Limpiar suscripciones inválidas
# ===================================================
@api.route("/api/cleanup_subscriptions", methods=["POST"])
@login_required
def cleanup_subscriptions():
    """Limpiar suscripciones push inválidas"""
    try:
        # Solo admin puede hacer esto
        if session.get('user') != 'Joso':  # Ajusta según tu sistema de permisos
            return jsonify({"error": "Permisos insuficientes"}), 403

        removed_count = 0
        subscripciones = mongo.db.subscriptions.find()
        
        for sub_doc in subscripciones:
            user_name = sub_doc.get("user")
            valid_subs = []
            
            for sub in sub_doc.get("subscriptions", []):
                # Probar si la suscripción sigue siendo válida
                try:
                    endpoint = sub.get("endpoint")
                    if endpoint and get_vapid_claims(endpoint):
                        valid_subs.append(sub)
                    else:
                        removed_count += 1
                except:
                    removed_count += 1
            
            # Actualizar con solo las suscripciones válidas
            if len(valid_subs) != len(sub_doc.get("subscriptions", [])):
                mongo.db.subscriptions.update_one(
                    {"user": user_name},
                    {"$set": {"subscriptions": valid_subs}}
                )

        return jsonify({"success": True, "removed_count": removed_count})
    except Exception as e:
        print(f"[ERROR] cleanup_subscriptions: {e}")
        return jsonify({"error": "Error en limpieza"}), 500