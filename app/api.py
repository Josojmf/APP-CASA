import base64
import json
import logging
import os
import traceback
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

import requests
from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request, session
from pywebpush import WebPushException, webpush

from app import mongo
from app.socket_utils import notificar_tarea_a_usuario

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==========================================
# Mapeo entre TID de OwnTracks y nombre de usuario real
# ==========================================
TID_TO_USERNAME = {"jo": "Joso", "an": "Ana", "pa": "Papa", "ma": "Mama"}


# ==========================================
# Decorador para autenticación
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user" not in session:
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
            "sub": "mailto:joso.jmf@gmail.com",
        }
    except Exception as e:
        logger.error(f"Error get_vapid_claims: {e}")
        return None


# ==========================================
# Definición del Blueprint API
# ==========================================
api = Blueprint("api", __name__)


# ===================================================
# Endpoint: Obtener lista de usuarios
# ===================================================
@api.route("/api/users", methods=["GET"])
@login_required
def get_users():
    """
    Devuelve todos los usuarios de la base de datos.
    """
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user["_id"] = str(user["_id"])
        return jsonify(users)
    except Exception as e:
        logger.error(f"Error get_users: {e}")
        return jsonify({"error": "No se pudieron obtener los usuarios"}), 500


# ===================================================
# Endpoint: Alternar estado 'en casa'
# ===================================================
@api.route("/api/toggle_encasa", methods=["POST"])
@login_required
def toggle_encasa():
    """
    Alterna el estado 'encasa' de un usuario por su ID.
    Envía notificación push a todos los usuarios.
    """
    user_id = request.json.get("user_id")
    if not user_id:
        return jsonify({"error": "Falta user_id"}), 400

    try:
        obj_id = ObjectId(user_id)
    except Exception:
        return jsonify({"error": "ID no válido"}), 400

    user = mongo.db.users.find_one({"_id": obj_id})
    if not user:
        return jsonify({"error": "Usuario no encontrado"}), 404

    new_status = not user.get("encasa", False)

    # Actualizar con timestamp
    mongo.db.users.update_one(
        {"_id": obj_id},
        {"$set": {"encasa": new_status, "last_status_change": datetime.now()}},
    )

    hora = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    mensaje = f"{user['nombre']} {'ha llegado a casa 🏠' if new_status else 'ha salido de casa 🚶‍♂️'} a las {hora}"

    send_push_to_all(title="House App", body=mensaje, url="/usuarios")

    return jsonify({"success": True, "new_status": new_status})


# ===================================================
# Endpoint: Añadir tarea a usuario
# ===================================================
@api.route("/api/add_task", methods=["POST"])
@login_required
def add_task():
    """
    Añade una tarea a un usuario y le envía notificación push solo a él.
    """
    data = request.get_json()
    titulo = data.get("titulo")
    asignee = data.get("asignee")
    due_date = data.get("due_date")
    pasos = data.get("pasos", "")
    prioridad = data.get("prioridad", "normal")

    if not titulo or not asignee or not due_date:
        return jsonify({"error": "Faltan campos obligatorios"}), 400

    # Validar fecha
    try:
        datetime.strptime(due_date, "%Y-%m-%d")
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
        "created_by": session.get("user"),
        "created_at": datetime.now(),
        "completed": False,
    }

    mongo.db.users.update_one({"_id": user["_id"]}, {"$push": {"tareas": nueva_tarea}})

    # Enviar notificación solo al usuario asignado
    send_push_to_user(
        user_name=asignee,
        title="Nueva tarea asignada 📋",
        body=f"Se te ha asignado: {titulo}",
        url="/tareas",
    )

    return jsonify({"success": True})


# ===================================================
# Endpoint: Completar tarea
# ===================================================
@api.route("/api/completar_tarea", methods=["POST"])
@login_required
def completar_tarea():
    """
    Marca una tarea como completada y la elimina de la base de datos.
    """
    data = request.get_json()
    user_id = data.get("user_id")
    tarea = data.get("tarea")

    if not user_id or not tarea:
        return jsonify({"error": "Datos incompletos"}), 400

    try:
        # Registrar tarea completada antes de eliminar
        user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        if user:
            completed_task = {
                "titulo": tarea["titulo"],
                "due_date": tarea["due_date"],
                "completed_by": user["nombre"],
                "completed_at": datetime.now(),
                "completed_by_session": session.get("user"),
            }
            mongo.db.completed_tasks.insert_one(completed_task)

        # Eliminar de tareas activas
        mongo.db.users.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$pull": {
                    "tareas": {"titulo": tarea["titulo"], "due_date": tarea["due_date"]}
                }
            },
        )

        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Error completar_tarea: {traceback.format_exc()}")
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
        if not any(
            s["endpoint"] == subscription["endpoint"] for s in existing["subscriptions"]
        ):
            mongo.db.subscriptions.update_one(
                {"user": user}, {"$push": {"subscriptions": subscription}}
            )
    else:
        mongo.db.subscriptions.insert_one(
            {
                "user": user,
                "subscriptions": [subscription],
                "created_at": datetime.now(),
            }
        )

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
        logger.error(f"Error obtener_lista: {e}")
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
        existing = mongo.db.lista_compra.find_one(
            {"nombre": {"$regex": f"^{nombre}$", "$options": "i"}}
        )
        if existing:
            return jsonify({"error": "Este producto ya está en la lista"}), 409

        new_item = {
            "nombre": nombre,
            "cantidad": str(cantidad),
            "unidad": unidad,
            "comprado": False,
            "created_by": session.get("user"),
            "created_at": datetime.now(),
        }

        result = mongo.db.lista_compra.insert_one(new_item)

        # Notificar a todos los usuarios
        send_push_to_all(
            title="Lista de compra actualizada 🛒",
            body=f"{session.get('user')} añadió: {nombre}",
            url="/lista_compra",
        )

        return jsonify({"success": True, "id": str(result.inserted_id)})
    except Exception as e:
        logger.error(f"Error agregar_item: {e}")
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
            {
                "$set": {
                    "nombre": nombre,
                    "cantidad": str(cantidad),
                    "unidad": unidad,
                    "updated_by": session.get("user"),
                    "updated_at": datetime.now(),
                }
            },
        )

        if result.matched_count == 0:
            return jsonify({"error": "Item no encontrado"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error editar_item: {e}")
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
            url="/lista_compra",
        )

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error eliminar_item: {e}")
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
            url="/lista_compra",
        )

        return jsonify({"success": True, "deleted_count": result.deleted_count})
    except Exception as e:
        logger.error(f"Error eliminar_toda_lista: {e}")
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
            {
                "$set": {
                    "comprado": new_status,
                    "marked_by": session.get("user"),
                    "marked_at": datetime.now(),
                }
            },
        )

        return jsonify({"success": True, "comprado": new_status})
    except Exception as e:
        logger.error(f"Error marcar_comprado: {e}")
        return jsonify({"error": "Error al marcar el item"}), 500


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
            return jsonify(
                {
                    "avatar": "/static/images/default-avatar.png",
                    "nombre": user.get("nombre"),
                    "encasa": user.get("encasa", False),
                }
            )

        return jsonify(
            {
                "avatar": f"data:image/jpeg;base64,{imagen_data}",
                "nombre": user.get("nombre"),
                "encasa": user.get("encasa", False),
                "last_status_change": user.get("last_status_change"),
            }
        )
    except Exception as e:
        logger.error(f"Error get_user_profile: {e}")
        return jsonify({"error": "Error al obtener perfil"}), 500


# ===================================================
# Endpoint: Chat con IA familiar (CORREGIDO)
# ===================================================
# ===================================================
# Funciones auxiliares para acceso a datos
# ===================================================

def get_comprehensive_family_data():
    """Obtener todos los datos familiares de la base de datos"""
    try:
        # 1. Usuarios y su estado
        users = list(mongo.db.users.find())
        users_info = []
        total_tasks = 0
        
        for user in users:
            user_tasks = user.get("tareas", [])
            task_count = len(user_tasks)
            total_tasks += task_count
            
            # Clasificar tareas por prioridad y fecha
            urgent_tasks = [t for t in user_tasks if t.get("prioridad") == "alta"]
            overdue_tasks = []
            upcoming_tasks = []
            
            for task in user_tasks:
                try:
                    due_date = datetime.strptime(task.get("due_date", ""), "%Y-%m-%d")
                    if due_date < datetime.now():
                        overdue_tasks.append(task)
                    elif due_date <= datetime.now() + timedelta(days=3):
                        upcoming_tasks.append(task)
                except:
                    pass
            
            users_info.append({
                "nombre": user.get("nombre"),
                "encasa": user.get("encasa", False),
                "total_tareas": task_count,
                "tareas_urgentes": len(urgent_tasks),
                "tareas_vencidas": len(overdue_tasks),
                "tareas_proximas": len(upcoming_tasks),
                "tareas_detalle": user_tasks,
                "last_status_change": user.get("last_status_change")
            })
        
        # 2. Lista de compra
        shopping_items = list(mongo.db.lista_compra.find().sort("created_at", -1))
        shopping_stats = {
            "total_items": len(shopping_items),
            "items_comprados": len([item for item in shopping_items if item.get("comprado", False)]),
            "items_pendientes": len([item for item in shopping_items if not item.get("comprado", False)]),
            "items_detalle": shopping_items
        }
        
        # 3. Tareas completadas recientes
        recent_completed = list(mongo.db.completed_tasks.find().sort("completed_at", -1).limit(10))
        
        # 4. Estadísticas generales
        stats = {
            "usuarios_en_casa": len([u for u in users_info if u["encasa"]]),
            "usuarios_fuera": len([u for u in users_info if not u["encasa"]]),
            "total_usuarios": len(users_info),
            "total_tareas_activas": total_tasks,
            "tareas_completadas_hoy": mongo.db.completed_tasks.count_documents({
                "completed_at": {
                    "$gte": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                }
            })
        }
        
        return {
            "usuarios": users_info,
            "lista_compra": shopping_stats,
            "tareas_completadas_recientes": recent_completed,
            "estadisticas": stats,
            "fecha_consulta": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        
    except Exception as e:
        logger.error(f"Error get_comprehensive_family_data: {e}")
        return {"error": "No se pudieron obtener los datos familiares"}

def generate_ai_context(family_data, user_query):
    """Generar contexto inteligente para la IA basado en la consulta"""
    
    if not family_data or "error" in family_data:
        return "No hay datos familiares disponibles en este momento."
    
    # Análisis de la consulta para determinar qué información es relevante
    query_lower = user_query.lower()
    
    context_parts = []
    
    # Información básica siempre incluida
    stats = family_data["estadisticas"]
    context_parts.append(f"""=== ESTADO ACTUAL DE LA FAMILIA ({family_data['fecha_consulta']}) ===
• {stats['usuarios_en_casa']} usuarios en casa, {stats['usuarios_fuera']} fuera
• {stats['total_tareas_activas']} tareas activas en total
• {stats['tareas_completadas_hoy']} tareas completadas hoy
• {family_data['lista_compra']['items_pendientes']} productos pendientes en lista de compra""")
    
    # Información específica según la consulta
    if any(word in query_lower for word in ["tarea", "task", "trabajo", "hacer", "pendiente", "vencid", "urgent"]):
        context_parts.append("\n=== INFORMACIÓN DE TAREAS ===")
        for user in family_data["usuarios"]:
            if user["total_tareas"] > 0:
                context_parts.append(f"""
• {user['nombre']}: {user['total_tareas']} tareas totales
  - {user['tareas_urgentes']} urgentes
  - {user['tareas_vencidas']} vencidas  
  - {user['tareas_proximas']} próximas a vencer
  
  Tareas detalladas:""")
                
                for task in user["tareas_detalle"][:3]:  # Máximo 3 tareas por usuario
                    prioridad = task.get("prioridad", "normal")
                    fecha = task.get("due_date", "Sin fecha")
                    context_parts.append(f"    - {task.get('titulo', 'Sin título')} (Prioridad: {prioridad}, Fecha: {fecha})")
    
    if any(word in query_lower for word in ["compra", "shopping", "mercado", "supermercado", "producto", "necesit"]):
        context_parts.append(f"\n=== LISTA DE COMPRA ===")
        context_parts.append(f"• {family_data['lista_compra']['items_pendientes']} productos pendientes")
        context_parts.append(f"• {family_data['lista_compra']['items_comprados']} productos ya comprados")
        
        if family_data['lista_compra']['items_detalle']:
            context_parts.append("\nProductos pendientes:")
            for item in family_data['lista_compra']['items_detalle'][:8]:  # Máximo 8 items
                if not item.get("comprado", False):
                    cantidad = item.get("cantidad", "1")
                    unidad = item.get("unidad", "")
                    context_parts.append(f"  - {item.get('nombre', 'Sin nombre')} ({cantidad} {unidad})".strip())
    
    if any(word in query_lower for word in ["quien", "quién", "usuario", "persona", "casa", "fuera", "estado"]):
        context_parts.append(f"\n=== ESTADO DE USUARIOS ===")
        for user in family_data["usuarios"]:
            estado = "🏠 En casa" if user["encasa"] else "🚶 Fuera"
            context_parts.append(f"• {user['nombre']}: {estado} ({user['total_tareas']} tareas)")
    
    if any(word in query_lower for word in ["optimiz", "mejor", "eficien", "distribu", "balanc", "reparto"]):
        context_parts.append(f"\n=== ANÁLISIS PARA OPTIMIZACIÓN ===")
        # Usuario con más tareas
        max_tasks_user = max(family_data["usuarios"], key=lambda u: u["total_tareas"])
        min_tasks_user = min(family_data["usuarios"], key=lambda u: u["total_tareas"])
        
        context_parts.append(f"• Usuario con más tareas: {max_tasks_user['nombre']} ({max_tasks_user['total_tareas']} tareas)")
        context_parts.append(f"• Usuario con menos tareas: {min_tasks_user['nombre']} ({min_tasks_user['total_tareas']} tareas)")
        
        # Tareas vencidas y urgentes
        total_overdue = sum(u["tareas_vencidas"] for u in family_data["usuarios"])
        total_urgent = sum(u["tareas_urgentes"] for u in family_data["usuarios"])
        context_parts.append(f"• Total tareas vencidas: {total_overdue}")
        context_parts.append(f"• Total tareas urgentes: {total_urgent}")
    
    return "\n".join(context_parts)

# ===================================================
# Endpoint: Chat con IA familiar MEJORADO
# ===================================================
@api.route("/api/chatfd", methods=["POST"])
@login_required
def chat_familiar():
    """Chat con asistente familiar usando Groq con acceso completo a la base de datos"""
    try:
        if not request.is_json:
            return jsonify({"error": "Content-Type debe ser application/json"}), 400

        data = request.get_json()
        q = data.get("prompt", "").strip()
        if not q:
            return jsonify({"error": "Prompt vacío"}), 400

        API_KEY = os.getenv("GROQ_API_KEY")
        if not API_KEY:
            return jsonify({"error": "Servicio de IA no configurado"}), 500

        # 📊 Obtener datos completos de la familia
        family_data = get_comprehensive_family_data()
        
        # 🧠 Generar contexto inteligente basado en la consulta
        smart_context = generate_ai_context(family_data, q)
        
        # --- Inicializar historial si no existe ---
        if "chat_history" not in session:
            session["chat_history"] = [
                {
                    "role": "system",
                    "content": f"""Eres 'Casa AI', un asistente familiar inteligente que ayuda a optimizar la vida doméstica.

CAPACIDADES PRINCIPALES:
• Análisis y optimización de tareas familiares
• Redistribución inteligente de responsabilidades  
• Planificación de menús y gestión de compras
• Consejos personalizados para el hogar
• Análisis de patrones familiares y sugerencias de mejora

INSTRUCCIONES IMPORTANTES:
1. Usa SIEMPRE los datos actualizados de la base de datos para tus respuestas
2. Proporciona insights específicos basados en los datos reales
3. Sugiere optimizaciones concretas y actionables
4. Sé proactivo identificando problemas y oportunidades
5. Personaliza las respuestas según el contexto familiar actual
6. Usa emojis para hacer las respuestas más amigables
7. Si hay tareas vencidas o desequilibrios, menciónalos proactivamente

DATOS ACTUALES DE LA FAMILIA:
{smart_context}

Responde de manera práctica, específica y útil usando esta información."""
                }
            ]
        else:
            # Actualizar el contexto del sistema con datos frescos
            session["chat_history"][0]["content"] = f"""Eres 'Casa AI', un asistente familiar inteligente que ayuda a optimizar la vida doméstica.

CAPACIDADES PRINCIPALES:
• Análisis y optimización de tareas familiares
• Redistribución inteligente de responsabilidades  
• Planificación de menús y gestión de compras
• Consejos personalizados para el hogar
• Análisis de patrones familiares y sugerencias de mejora

INSTRUCCIONES IMPORTANTES:
1. Usa SIEMPRE los datos actualizados de la base de datos para tus respuestas
2. Proporciona insights específicos basados en los datos reales
3. Sugiere optimizaciones concretas y actionables
4. Sé proactivo identificando problemas y oportunidades
5. Personaliza las respuestas según el contexto familiar actual
6. Usa emojis para hacer las respuestas más amigables
7. Si hay tareas vencidas o desequilibrios, menciónalos proactivamente

DATOS ACTUALIZADOS DE LA FAMILIA:
{smart_context}

Responde de manera práctica, específica y útil usando esta información."""

        # Limitar historial
        MAX_HISTORY = 15
        if len(session["chat_history"]) > MAX_HISTORY:
            session["chat_history"] = (
                session["chat_history"][:1]
                + session["chat_history"][-(MAX_HISTORY - 1):]
            )

        # Añadir mensaje del usuario
        session["chat_history"].append({"role": "user", "content": q})

        # --- Petición a Groq ---
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": session["chat_history"],
            "max_tokens": 1000,
            "temperature": 0.7,
            "top_p": 0.9,
            "stream": False,
        }

        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=45,
        )

        if resp.status_code != 200:
            return jsonify({"error": f"Error del servicio de IA ({resp.status_code})"}), 500

        response_data = resp.json()
        answer = response_data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        if not answer:
            return jsonify({"error": "Respuesta vacía del servicio de IA"}), 500

        # Añadir respuesta al historial
        session["chat_history"].append({"role": "assistant", "content": answer})

        # 📈 Datos adicionales para el frontend
        additional_data = {
            "data_timestamp": family_data.get("fecha_consulta"),
            "active_tasks": family_data.get("estadisticas", {}).get("total_tareas_activas", 0),
            "users_home": family_data.get("estadisticas", {}).get("usuarios_en_casa", 0),
            "pending_shopping": family_data.get("lista_compra", {}).get("items_pendientes", 0)
        }

        return jsonify({
            "answer": answer, 
            "status": "success", 
            "model": "llama-3.1-8b-instant",
            "context_data": additional_data
        })

    except Exception as e:
        logger.error(f"Error en chat_familiar: {traceback.format_exc()}")
        return jsonify({"error": "Error interno del servidor"}), 500

# ===================================================
# Endpoint: Estado del servicio de IA MEJORADO
# ===================================================
@api.route("/api/ai_status", methods=["GET"])
@login_required
def ai_status():
    """Verificar estado del servicio de IA con información de datos"""
    try:
        API_KEY = os.getenv("GROQ_API_KEY")
        has_api_key = bool(API_KEY)
        
        # Obtener estadísticas rápidas de la base de datos
        db_stats = {}
        if has_api_key:
            try:
                db_stats = {
                    "total_users": mongo.db.users.count_documents({}),
                    "active_tasks": sum(len(user.get("tareas", [])) for user in mongo.db.users.find()),
                    "shopping_items": mongo.db.lista_compra.count_documents({}),
                    "last_data_update": datetime.now().strftime("%H:%M")
                }
            except Exception as e:
                logger.warning(f"No se pudieron obtener estadísticas de BD: {e}")
                db_stats = {"error": "Datos no disponibles"}

        status = {
            "available": has_api_key,
            "model": "llama-3.1-8b-instant" if has_api_key else None,
            "chat_history_length": len(session.get("chat_history", [])),
            "provider": "Groq" if has_api_key else None,
            "database_connected": "error" not in db_stats,
            "data_stats": db_stats,
            "capabilities": [
                "📊 Análisis de tareas familiares",
                "🔄 Optimización y redistribución",
                "🛒 Gestión de lista de compra", 
                "📈 Estadísticas familiares",
                "💡 Sugerencias personalizadas"
            ] if has_api_key else []
        }

        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting AI status: {e}")
        return jsonify({"error": "Error al verificar estado de IA"}), 500

# ===================================================
# Endpoint: Análisis familiar proactivo
# ===================================================
@api.route("/api/family_insights", methods=["GET"])
@login_required
def get_family_insights():
    """Obtener insights automáticos sobre la situación familiar"""
    try:
        family_data = get_comprehensive_family_data()
        
        if "error" in family_data:
            return jsonify({"error": "No se pudieron obtener datos"}), 500
        
        insights = []
        stats = family_data["estadisticas"]
        
        # Análisis de tareas
        users = family_data["usuarios"]
        if users:
            max_tasks_user = max(users, key=lambda u: u["total_tareas"])
            min_tasks_user = min(users, key=lambda u: u["total_tareas"])
            
            # Desequilibrio de tareas
            if max_tasks_user["total_tareas"] - min_tasks_user["total_tareas"] >= 3:
                insights.append({
                    "type": "warning",
                    "title": "⚖️ Desequilibrio de tareas detectado",
                    "message": f"{max_tasks_user['nombre']} tiene {max_tasks_user['total_tareas']} tareas mientras que {min_tasks_user['nombre']} tiene {min_tasks_user['total_tareas']}",
                    "suggestion": "Considera redistribuir algunas tareas para balancer la carga de trabajo"
                })
            
            # Tareas vencidas
            total_overdue = sum(u["tareas_vencidas"] for u in users)
            if total_overdue > 0:
                insights.append({
                    "type": "urgent",
                    "title": "🚨 Tareas vencidas",
                    "message": f"Hay {total_overdue} tareas vencidas que requieren atención inmediata",
                    "suggestion": "Prioriza completar las tareas vencidas o reprogramar las fechas"
                })
        
        # Lista de compra
        shopping = family_data["lista_compra"]
        if shopping["items_pendientes"] > 10:
            insights.append({
                "type": "info",
                "title": "🛒 Lista de compra larga",
                "message": f"Tienes {shopping['items_pendientes']} productos pendientes en la lista",
                "suggestion": "Considera organizar una ida al supermercado pronto"
            })
        
        # Productividad diaria
        if stats["tareas_completadas_hoy"] == 0 and stats["total_tareas_activas"] > 0:
            insights.append({
                "type": "motivation",
                "title": "💪 ¡A por el día!",
                "message": "Aún no se han completado tareas hoy",
                "suggestion": "¿Qué tal empezar con una tarea pequeña para coger impulso?"
            })
        
        return jsonify({
            "insights": insights,
            "stats_summary": {
                "total_users": stats["total_usuarios"],
                "users_home": stats["usuarios_en_casa"],
                "active_tasks": stats["total_tareas_activas"],
                "completed_today": stats["tareas_completadas_hoy"],
                "shopping_pending": shopping["items_pendientes"]
            },
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        
    except Exception as e:
        logger.error(f"Error get_family_insights: {e}")
        return jsonify({"error": "Error al generar insights"}), 500


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
            "total_tasks": sum(
                len(user.get("tareas", [])) for user in mongo.db.users.find()
            ),
            "shopping_items": mongo.db.lista_compra.count_documents({}),
            "completed_tasks_today": mongo.db.completed_tasks.count_documents(
                {
                    "completed_at": {
                        "$gte": datetime.now().replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                    }
                }
            ),
        }
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error get_stats: {e}")
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
        if session.get("user") != "Joso":  # Ajusta según tu sistema de permisos
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
                    {"user": user_name}, {"$set": {"subscriptions": valid_subs}}
                )

        return jsonify({"success": True, "removed_count": removed_count})
    except Exception as e:
        logger.error(f"Error cleanup_subscriptions: {e}")
        return jsonify({"error": "Error en limpieza"}), 500


# ===================================================
# Funciones de notificaciones mejoradas
# ===================================================
def send_push_to_user(
    user_name, title, body, url="/", icon="/static/icons/house-icon.png"
):
    """Enviar notificación push a un usuario específico - MEJORADO"""
    try:
        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            logger.error("VAPID_PRIVATE_KEY no configurada")
            return False

        subscription_doc = mongo.db.subscriptions.find_one({"user": user_name})
        if not subscription_doc:
            logger.warning(f"No hay suscripciones para {user_name}")
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
                    data=json.dumps(
                        {"title": title, "body": body, "icon": icon, "url": url}
                    ),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=claims,
                )
                success_count += 1
                logger.info(f"✅ Notificación enviada a {user_name}")

            except WebPushException as ex:
                logger.error(f"❌ Error al enviar push a {user_name}: {repr(ex)}")
                # Limpiar suscripciones inválidas
                if ex.response and ex.response.status_code in [410, 404]:
                    mongo.db.subscriptions.update_one(
                        {"user": user_name}, {"$pull": {"subscriptions": sub}}
                    )
            except Exception as e:
                logger.error(f"❌ Error inesperado enviando push: {e}")

        return success_count > 0

    except Exception as e:
        logger.error(f"❌ Error en send_push_to_user: {e}")
        return False


def send_push_to_all(title, body, url="/", icon="/static/icons/house-icon.png"):
    """Enviar notificación push a todos los usuarios suscritos - MEJORADO"""
    try:
        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            logger.error("VAPID_PRIVATE_KEY no configurada")
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
                        data=json.dumps(
                            {"title": title, "body": body, "icon": icon, "url": url}
                        ),
                        vapid_private_key=vapid_private_key,
                        vapid_claims=claims,
                    )
                    total_sent += 1
                    logger.info(f"✅ Notificación enviada a {user_name}")

                except WebPushException as ex:
                    logger.error(f"❌ Error al enviar push a {user_name}: {repr(ex)}")
                    # Limpiar suscripciones inválidas
                    if ex.response and ex.response.status_code in [410, 404]:
                        mongo.db.subscriptions.update_one(
                            {"user": user_name}, {"$pull": {"subscriptions": sub}}
                        )
                except Exception as e:
                    logger.error(f"❌ Error inesperado al enviar push: {e}")

        logger.info(f"📊 Total notificaciones enviadas: {total_sent}")

    except Exception as e:
        logger.error(f"❌ Error en send_push_to_all: {e}")

@api.route("/api/clear_all_tasks", methods=["POST"])
def clear_all_tasks():
    """Eliminar todas las tareas"""
    try:
        mongo.db.users.update_many({}, {"$set": {"tareas": []}})
        return jsonify({"success": True, "message": "Todas las tareas han sido eliminadas"})
    except Exception as e:
        logger.error(f"Error al eliminar todas las tareas: {e}")
        return jsonify({"error": "Error al eliminar las tareas"}), 500