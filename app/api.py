import base64
import io
import json
import logging
import os
import traceback
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlparse

import requests
from bson import ObjectId
from flask import Blueprint, current_app, flash, jsonify, request, session
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


@api.route("/api/lista_compra/last_purchase", methods=["GET"])
@login_required
def get_last_purchase():
    """Obtener los productos de la última compra marcada como completada"""
    try:
        # Buscar en el historial la última compra completada
        last_purchase = mongo.db.completed_shopping.find_one(
            {}, sort=[("completed_at", -1)]
        )

        if not last_purchase:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "No se encontraron compras anteriores",
                        "items": [],
                    }
                ),
                404,
            )

        return jsonify(
            {
                "success": True,
                "items": last_purchase.get("items", []),
                "completed_at": last_purchase.get("completed_at"),
            }
        )

    except Exception as e:
        logger.error(f"Error get_last_purchase: {e}")
        return (
            jsonify({"success": False, "error": "Error al obtener última compra"}),
            500,
        )


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

            users_info.append(
                {
                    "nombre": user.get("nombre"),
                    "encasa": user.get("encasa", False),
                    "total_tareas": task_count,
                    "tareas_urgentes": len(urgent_tasks),
                    "tareas_vencidas": len(overdue_tasks),
                    "tareas_proximas": len(upcoming_tasks),
                    "tareas_detalle": user_tasks,
                    "last_status_change": user.get("last_status_change"),
                }
            )

        # 2. Lista de compra
        shopping_items = list(mongo.db.lista_compra.find().sort("created_at", -1))
        shopping_stats = {
            "total_items": len(shopping_items),
            "items_comprados": len(
                [item for item in shopping_items if item.get("comprado", False)]
            ),
            "items_pendientes": len(
                [item for item in shopping_items if not item.get("comprado", False)]
            ),
            "items_detalle": shopping_items,
        }

        # 3. Tareas completadas recientes
        recent_completed = list(
            mongo.db.completed_tasks.find().sort("completed_at", -1).limit(10)
        )

        # 4. Estadísticas generales
        stats = {
            "usuarios_en_casa": len([u for u in users_info if u["encasa"]]),
            "usuarios_fuera": len([u for u in users_info if not u["encasa"]]),
            "total_usuarios": len(users_info),
            "total_tareas_activas": total_tasks,
            "tareas_completadas_hoy": mongo.db.completed_tasks.count_documents(
                {
                    "completed_at": {
                        "$gte": datetime.now().replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                    }
                }
            ),
        }

        return {
            "usuarios": users_info,
            "lista_compra": shopping_stats,
            "tareas_completadas_recientes": recent_completed,
            "estadisticas": stats,
            "fecha_consulta": datetime.now().strftime("%Y-%m-%d %H:%M"),
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
    context_parts.append(
        f"""=== ESTADO ACTUAL DE LA FAMILIA ({family_data['fecha_consulta']}) ===
• {stats['usuarios_en_casa']} usuarios en casa, {stats['usuarios_fuera']} fuera
• {stats['total_tareas_activas']} tareas activas en total
• {stats['tareas_completadas_hoy']} tareas completadas hoy
• {family_data['lista_compra']['items_pendientes']} productos pendientes en lista de compra"""
    )

    # Información específica según la consulta
    if any(
        word in query_lower
        for word in [
            "tarea",
            "task",
            "trabajo",
            "hacer",
            "pendiente",
            "vencid",
            "urgent",
        ]
    ):
        context_parts.append("\n=== INFORMACIÓN DE TAREAS ===")
        for user in family_data["usuarios"]:
            if user["total_tareas"] > 0:
                context_parts.append(
                    f"""
• {user['nombre']}: {user['total_tareas']} tareas totales
  - {user['tareas_urgentes']} urgentes
  - {user['tareas_vencidas']} vencidas  
  - {user['tareas_proximas']} próximas a vencer
  
  Tareas detalladas:"""
                )

                for task in user["tareas_detalle"][:3]:  # Máximo 3 tareas por usuario
                    prioridad = task.get("prioridad", "normal")
                    fecha = task.get("due_date", "Sin fecha")
                    context_parts.append(
                        f"    - {task.get('titulo', 'Sin título')} (Prioridad: {prioridad}, Fecha: {fecha})"
                    )

    if any(
        word in query_lower
        for word in [
            "compra",
            "shopping",
            "mercado",
            "supermercado",
            "producto",
            "necesit",
        ]
    ):
        context_parts.append(f"\n=== LISTA DE COMPRA ===")
        context_parts.append(
            f"• {family_data['lista_compra']['items_pendientes']} productos pendientes"
        )
        context_parts.append(
            f"• {family_data['lista_compra']['items_comprados']} productos ya comprados"
        )

        if family_data["lista_compra"]["items_detalle"]:
            context_parts.append("\nProductos pendientes:")
            for item in family_data["lista_compra"]["items_detalle"][
                :8
            ]:  # Máximo 8 items
                if not item.get("comprado", False):
                    cantidad = item.get("cantidad", "1")
                    unidad = item.get("unidad", "")
                    context_parts.append(
                        f"  - {item.get('nombre', 'Sin nombre')} ({cantidad} {unidad})".strip()
                    )

    if any(
        word in query_lower
        for word in ["quien", "quién", "usuario", "persona", "casa", "fuera", "estado"]
    ):
        context_parts.append(f"\n=== ESTADO DE USUARIOS ===")
        for user in family_data["usuarios"]:
            estado = "🏠 En casa" if user["encasa"] else "🚶 Fuera"
            context_parts.append(
                f"• {user['nombre']}: {estado} ({user['total_tareas']} tareas)"
            )

    if any(
        word in query_lower
        for word in ["optimiz", "mejor", "eficien", "distribu", "balanc", "reparto"]
    ):
        context_parts.append(f"\n=== ANÁLISIS PARA OPTIMIZACIÓN ===")
        # Usuario con más tareas
        max_tasks_user = max(family_data["usuarios"], key=lambda u: u["total_tareas"])
        min_tasks_user = min(family_data["usuarios"], key=lambda u: u["total_tareas"])

        context_parts.append(
            f"• Usuario con más tareas: {max_tasks_user['nombre']} ({max_tasks_user['total_tareas']} tareas)"
        )
        context_parts.append(
            f"• Usuario con menos tareas: {min_tasks_user['nombre']} ({min_tasks_user['total_tareas']} tareas)"
        )

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

Responde de manera práctica, específica y útil usando esta información.""",
                }
            ]
        else:
            # Actualizar el contexto del sistema con datos frescos
            session["chat_history"][0][
                "content"
            ] = f"""Eres 'Casa AI', un asistente familiar inteligente que ayuda a optimizar la vida doméstica.

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
                + session["chat_history"][-(MAX_HISTORY - 1) :]
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
            return (
                jsonify({"error": f"Error del servicio de IA ({resp.status_code})"}),
                500,
            )

        response_data = resp.json()
        answer = (
            response_data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if not answer:
            return jsonify({"error": "Respuesta vacía del servicio de IA"}), 500

        # Añadir respuesta al historial
        session["chat_history"].append({"role": "assistant", "content": answer})

        # 📈 Datos adicionales para el frontend
        additional_data = {
            "data_timestamp": family_data.get("fecha_consulta"),
            "active_tasks": family_data.get("estadisticas", {}).get(
                "total_tareas_activas", 0
            ),
            "users_home": family_data.get("estadisticas", {}).get(
                "usuarios_en_casa", 0
            ),
            "pending_shopping": family_data.get("lista_compra", {}).get(
                "items_pendientes", 0
            ),
        }

        return jsonify(
            {
                "answer": answer,
                "status": "success",
                "model": "llama-3.1-8b-instant",
                "context_data": additional_data,
            }
        )

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
                    "active_tasks": sum(
                        len(user.get("tareas", [])) for user in mongo.db.users.find()
                    ),
                    "shopping_items": mongo.db.lista_compra.count_documents({}),
                    "last_data_update": datetime.now().strftime("%H:%M"),
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
            "capabilities": (
                [
                    "📊 Análisis de tareas familiares",
                    "🔄 Optimización y redistribución",
                    "🛒 Gestión de lista de compra",
                    "📈 Estadísticas familiares",
                    "💡 Sugerencias personalizadas",
                ]
                if has_api_key
                else []
            ),
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
                insights.append(
                    {
                        "type": "warning",
                        "title": "⚖️ Desequilibrio de tareas detectado",
                        "message": f"{max_tasks_user['nombre']} tiene {max_tasks_user['total_tareas']} tareas mientras que {min_tasks_user['nombre']} tiene {min_tasks_user['total_tareas']}",
                        "suggestion": "Considera redistribuir algunas tareas para balancer la carga de trabajo",
                    }
                )

            # Tareas vencidas
            total_overdue = sum(u["tareas_vencidas"] for u in users)
            if total_overdue > 0:
                insights.append(
                    {
                        "type": "urgent",
                        "title": "🚨 Tareas vencidas",
                        "message": f"Hay {total_overdue} tareas vencidas que requieren atención inmediata",
                        "suggestion": "Prioriza completar las tareas vencidas o reprogramar las fechas",
                    }
                )

        # Lista de compra
        shopping = family_data["lista_compra"]
        if shopping["items_pendientes"] > 10:
            insights.append(
                {
                    "type": "info",
                    "title": "🛒 Lista de compra larga",
                    "message": f"Tienes {shopping['items_pendientes']} productos pendientes en la lista",
                    "suggestion": "Considera organizar una ida al supermercado pronto",
                }
            )

        # Productividad diaria
        if stats["tareas_completadas_hoy"] == 0 and stats["total_tareas_activas"] > 0:
            insights.append(
                {
                    "type": "motivation",
                    "title": "💪 ¡A por el día!",
                    "message": "Aún no se han completado tareas hoy",
                    "suggestion": "¿Qué tal empezar con una tarea pequeña para coger impulso?",
                }
            )

        return jsonify(
            {
                "insights": insights,
                "stats_summary": {
                    "total_users": stats["total_usuarios"],
                    "users_home": stats["usuarios_en_casa"],
                    "active_tasks": stats["total_tareas_activas"],
                    "completed_today": stats["tareas_completadas_hoy"],
                    "shopping_pending": shopping["items_pendientes"],
                },
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        )

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
        return jsonify(
            {"success": True, "message": "Todas las tareas han sido eliminadas"}
        )
    except Exception as e:
        logger.error(f"Error al eliminar todas las tareas: {e}")
        return jsonify({"error": "Error al eliminar las tareas"}), 500


# ==========================================
# CORRECCIONES CRÍTICAS PARA api.py
# ==========================================

# 1. ELIMINAR estas líneas duplicadas del final de api.py (están mal colocadas):

"""
# APIS DE MERCADONA
"""

# 2. CORREGIR el decorador de la función en api.py:
# Cambiar de @main.route a @api.route

api.route("/api/mercadona/add_to_list", methods=["POST"])


@login_required
def add_mercadona_to_list():
    """Añadir producto de Mercadona a nuestra lista de compra (endpoint alternativo)"""
    try:
        data = request.get_json()
        product_id = data.get("product_id")
        product_name = data.get("product_name")
        quantity = int(data.get("quantity", 1))
        packaging = data.get("packaging", "")

        if not product_id or not product_name:
            return jsonify({"error": "Datos de producto requeridos"}), 400

        # Formatear nombre con detalles
        formatted_name = product_name.title()
        if packaging:
            formatted_name += f" ({packaging})"

        # Verificar si ya existe en nuestra lista
        existing = mongo.db.lista_compra.find_one(
            {"nombre": {"$regex": f"^{formatted_name}$", "$options": "i"}}
        )

        if existing:
            # Si existe, actualizar cantidad
            new_quantity = int(existing.get("cantidad", "1")) + quantity
            mongo.db.lista_compra.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "cantidad": str(new_quantity),
                        "updated_by": session.get("user"),
                        "updated_at": datetime.now(),
                    }
                },
            )
            action = "updated"
        else:
            # Si no existe, crear nuevo
            new_item = {
                "nombre": formatted_name,
                "cantidad": str(quantity),
                "unidad": "",
                "comprado": False,
                "created_by": session.get("user"),
                "created_at": datetime.now(),
                "mercadona_id": product_id,
                "source": "mercadona",
            }

            mongo.db.lista_compra.insert_one(new_item)
            action = "added"

        # Notificar
        action_text = "actualizó cantidad de" if action == "updated" else "añadió"
        send_push_to_all(
            title="🛒 Producto desde Mercadona",
            body=f"{session.get('user')} {action_text}: {formatted_name}",
            url="/lista_compra",
        )

        return jsonify(
            {
                "success": True,
                "action": action,
                "product_name": formatted_name,
                "quantity": quantity,
            }
        )

    except Exception as e:
        logger.error(f"Error add_mercadona_to_list: {e}")
        return jsonify({"error": "Error al añadir producto a la lista"}), 500


@api.route("/api/lista_compra/history", methods=["POST"])
@login_required
def save_shopping_history():
    """Guardar la lista de compra actual en el historial"""
    try:
        data = request.get_json()
        items = data.get("items", [])

        if not items:
            return jsonify({"error": "Lista vacía"}), 400

        history_entry = {
            "items": items,
            "completed_by": session.get("user"),
            "completed_at": datetime.now(),
        }

        result = mongo.db.completed_shopping.insert_one(history_entry)

        return jsonify({"success": True, "inserted_id": str(result.inserted_id)})

    except Exception as e:
        logger.error(f"Error save_shopping_history: {e}")
        return jsonify({"error": "Error al guardar historial"}), 500


@api.route("/api/lista_compra/mark_completed", methods=["POST"])
def mark_completed():
    try:
        # Primero obtener todos los items actuales
        current_items = list(db.lista_compra.find({}, {"_id": 0}))

        if not current_items:
            return jsonify({"error": "La lista está vacía"}), 400

        # Guardar en el historial
        history_record = {"items": current_items, "date": datetime.now().isoformat()}
        db.purchase_history.insert_one(history_record)

        # Luego vaciar la lista actual
        db.lista_compra.delete_many({})

        return (
            jsonify({"message": "Lista marcada como comprada y guardada en historial"}),
            200,
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api.route("/api/change_user_image/<user_id>", methods=["PUT"])
@login_required
def change_user_image(user_id):
    """Cambiar imagen de perfil de un usuario"""
    try:
        data = request.json
        imagen = data.get("imagen", "").strip()

        if not imagen:
            return jsonify({"error": "La imagen es obligatoria"}), 400

        # Verificar que el usuario existe
        obj_id = ObjectId(user_id)
        user = mongo.db.users.find_one({"_id": obj_id})
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404

        # Actualizar imagen
        mongo.db.users.update_one({"_id": obj_id}, {"$set": {"imagen": imagen}})

        return jsonify({"success": True, "message": "Imagen actualizada correctamente"})

    except Exception as e:
        logger.error(f"Error change_user_image: {e}")
        return jsonify({"error": "Error al actualizar imagen"}), 500


# Añadir estas rutas a tu aplicación Flask


@api.route("/api/tasks_stats")
@login_required
def get_tasks_stats():
    """Obtener estadísticas de tareas para el gráfico"""
    try:
        period = int(request.args.get("period", 30))
        end_date = datetime.now()
        start_date = end_date - timedelta(days=period)

        # Agrupar tareas completadas por día
        pipeline = [
            {"$match": {"completed_at": {"$gte": start_date, "$lte": end_date}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$completed_at"}
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        results = list(mongo.db.completed_tasks.aggregate(pipeline))

        # Crear datos para el gráfico
        labels = []
        values = []

        # Rellenar todos los días del periodo
        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            labels.append(current_date.strftime("%d %b"))

            # Buscar si hay datos para este día
            day_data = next((item for item in results if item["_id"] == date_str), None)
            values.append(day_data["count"] if day_data else 0)

            current_date += timedelta(days=1)

        return jsonify({"labels": labels, "values": values})

    except Exception as e:
        logger.error(f"Error get_tasks_stats: {e}")
        return jsonify({"error": "Error al obtener estadísticas"}), 500


@api.route("/api/tasks_stats")
@login_required
def tasks_stats():
    """API para obtener estadísticas de tareas para el gráfico"""
    try:
        period = int(request.args.get("period", 30))
        now = datetime.now()
        start_date = now - timedelta(days=period)

        # Agrupar tareas por día
        pipeline = [
            {"$match": {"completion_date": {"$gte": start_date}}},
            {
                "$group": {
                    "_id": {
                        "$dateToString": {
                            "format": "%Y-%m-%d",
                            "date": "$completion_date",
                        }
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]

        results = list(mongo.db.completed_tasks.aggregate(pipeline))

        # Formatear datos para Chart.js
        labels = []
        values = []

        # Rellenar todos los días del periodo
        for i in range(period + 1):
            current_date = start_date + timedelta(days=i)
            date_str = current_date.strftime("%Y-%m-%d")
            labels.append(current_date.strftime("%d %b"))

            # Buscar si hay datos para este día
            found = next((item for item in results if item["_id"] == date_str), None)
            values.append(found["count"] if found else 0)

        return jsonify({"labels": labels, "values": values})
    except Exception as e:
        logger.error(f"Error en tasks_stats: {e}")
        return jsonify({"error": str(e)}), 500


import csv
from io import StringIO

from flask import make_response


def get_stats_data(period_days):
    """Obtiene estadísticas de la base de datos para el periodo especificado"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    pipeline = [
        {"$match": {"timestamp": {"$gte": start_date, "$lte": end_date}}},
        {
            "$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}},
                "completed_tasks": {
                    "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                },
                "active_users": {"$sum": {"$cond": ["$is_active", 1, 0]}},
            }
        },
        {"$sort": {"_id": 1}},
    ]

    results = list(mongo.db.user_activities.aggregate(pipeline))

    # Formatear los resultados
    stats = []
    for result in results:
        stats.append(
            {
                "date": result["_id"],
                "completed_tasks": result["completed_tasks"],
                "active_users": result["active_users"],
            }
        )

    return stats


import zipfile
from io import BytesIO


@api.route("/api/export_full_stats")
def export_full_stats():
    try:
        # 1. Exportar tareas completadas
        tasks = list(
            mongo.db.completed_tasks.find(
                {}, {"_id": 0, "task_name": 1, "completion_date": 1, "completed_by": 1}
            )
        )

        # 2. Exportar lista de compras completadas (con manejo de items mejorado)
        shopping = list(
            mongo.db.completed_shopping.find(
                {}, {"_id": 0, "items": 1, "completed_date": 1, "completed_by": 1}
            )
        )

        # 3. Exportar usuarios
        users = list(
            mongo.db.users.find(
                {}, {"_id": 0, "nombre": 1, "email": 1, "last_login": 1}
            )
        )

        # Crear CSV múltiple en un ZIP
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w") as zip_file:
            # --- Archivo de tareas ---
            tasks_csv = StringIO()
            writer = csv.writer(tasks_csv)
            writer.writerow(["Nombre", "Completado Por"])
            for task in tasks:
                # solo titulo y usuario
                task_name = task.get("titulo", "Desconocido")
                completed_by = task.get("usuario", "Desconocido")
                writer.writerow([task_name, completed_by])
            zip_file.writestr("tareas_completadas.csv", tasks_csv.getvalue())

            # --- Archivo de compras ---
            shopping_csv = StringIO()
            writer = csv.writer(shopping_csv)
            writer.writerow(["Productos", "Cantidad", "Fecha", "Comprado Por"])
            for shop in shopping:
                # Manejo seguro de items (ahora soporta diccionarios)
                items_list = []
                for item in shop.get("items", []):
                    if isinstance(item, dict):
                        # Si el item es un diccionario, extraemos nombre y cantidad
                        name = item.get("nombre", item.get("name", "Desconocido"))
                        qty = item.get("cantidad", item.get("quantity", 1))
                        items_list.append(f"{name} ({qty})")
                    else:
                        items_list.append(str(item))

                # Manejo de fechas
                shop_date = shop.get("completed_date", "")
                if isinstance(shop_date, datetime):
                    shop_date = shop_date.strftime("%Y-%m-%d")

                writer.writerow(
                    [
                        ", ".join(items_list),  # Items formateados
                        len(shop.get("items", [])),  # Cantidad total de items
                        shop_date,
                        shop.get("completed_by", ""),
                    ]
                )
            zip_file.writestr("compras_completadas.csv", shopping_csv.getvalue())

            # --- Archivo de usuarios ---
            users_csv = StringIO()
            writer = csv.writer(users_csv)
            writer.writerow(["Nombre", "Email", "Último Login"])
            for user in users:
                last_login = user.get("last_login", "")
                if isinstance(last_login, datetime):
                    last_login = last_login.strftime("%Y-%m-%d %H:%M")
                writer.writerow(
                    [user.get("nombre", ""), user.get("email", ""), last_login]
                )
            zip_file.writestr("usuarios.csv", users_csv.getvalue())

        zip_buffer.seek(0)
        response = make_response(zip_buffer.getvalue())
        response.headers["Content-Disposition"] = "attachment; filename=full_export.zip"
        response.headers["Content-type"] = "application/zip"

        return response

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify(
                {
                    "error": "Error en la exportación",
                    "details": str(e),
                    "type": type(e).__name__,
                }
            ),
            500,
        )


@api.route("/api/add_user", methods=["POST"])
@login_required
def add_user():
    """Añadir nuevo usuario"""
    try:
        data = request.json
        nombre = data.get("nombre", "").strip()
        imagen = data.get("imagen", "")

        if not nombre:
            return jsonify({"error": "El nombre es obligatorio"}), 400

        # Verificar si ya existe
        if mongo.db.users.find_one(
            {"nombre": {"$regex": f"^{nombre}$", "$options": "i"}}
        ):
            return jsonify({"error": "Ya existe un usuario con ese nombre"}), 409

        # Procesar imagen
        if imagen and imagen.startswith("data:image/"):
            imagen = imagen.split(",")[1]

        if imagen:
            imagen = imagen.strip()

        # Crear usuario
        new_user = {
            "nombre": nombre,
            "encasa": True,
            "imagen": imagen,
            "tareas": [],
            "calendario": [],
            "created_by": session.get("user"),
            "created_at": datetime.now(),
        }

        result = mongo.db.users.insert_one(new_user)

        return jsonify({"success": True, "user_id": str(result.inserted_id)}), 201
    except Exception as e:
        logger.error(f"Error add_user: {e}")
        return jsonify({"error": "Error al crear usuario"}), 500


@api.route("/api/delete_user/<user_id>", methods=["DELETE"])
@login_required
def delete_user(user_id):
    """Eliminar usuario"""
    try:
        current_user = session.get("user")
        logger.info(
            f"Intento de eliminación por: {current_user}, ID objetivo: {user_id}"
        )
        obj_id = ObjectId(user_id)
        # Obtener información del usuario antes de eliminar
        user = mongo.db.users.find_one({"_id": obj_id})
        if not user:
            logger.warning(f"Usuario no encontrado: {user_id}")
            return (
                jsonify(
                    {
                        "error": "Usuario no encontrado",
                        "message": f"No existe usuario con ID {user_id}",
                    }
                ),
                404,
            )

        # Registrar acción
        logger.info(f"Eliminando usuario: {user['nombre']} (ID: {user_id})")

        # Eliminar usuario
        mongo.db.users.delete_one({"_id": obj_id})

        # Limpiar datos relacionados
        nombre_usuario = user.get("nombre")
        result_subs = mongo.db.subscriptions.delete_many({"user": nombre_usuario})
        result_tasks = mongo.db.completed_tasks.delete_many(
            {"completed_by": nombre_usuario}
        )

        logger.info(
            f"Datos relacionados eliminados: "
            f"{result_subs.deleted_count} suscripciones, "
            f"{result_tasks.deleted_count} tareas completadas"
        )

        return (
            jsonify(
                {
                    "success": True,
                    "message": f"Usuario {nombre_usuario} eliminado correctamente",
                    "deleted_subs": result_subs.deleted_count,
                    "deleted_tasks": result_tasks.deleted_count,
                }
            ),
            200,
        )

    except Exception as e:
        logger.error(f"Error crítico en delete_user: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": "Error interno del servidor", "message": str(e)}), 500


@api.route("/api/toggle_theme", methods=["POST"])
@login_required
def toggle_theme():
    """Cambiar tema de la aplicación"""
    try:
        data = request.json
        new_theme = data.get("theme")

        if new_theme not in ["light", "dark"]:
            return jsonify({"error": "Tema inválido"}), 400

        session["theme"] = new_theme
        return jsonify({"success": True, "theme": new_theme})
    except Exception as e:
        logger.error(f"Error toggle_theme: {e}")
        return jsonify({"error": "Error al cambiar tema"}), 500


@api.route("/api/storage_details")
def get_storage_details():
    try:
        # Verificar la conexión de manera correcta
        if mongo.db is None:
            return jsonify({"error": "Conexión a la base de datos no establecida"}), 500

        # Probar la conexión
        mongo.db.command("ping")

        # Obtener estadísticas de la base de datos
        db_stats = mongo.db.command("dbstats")

        # Obtener estadísticas de las colecciones
        collections_stats = {}
        for collection_name in mongo.db.list_collection_names():
            try:
                collections_stats[collection_name] = mongo.db.command(
                    "collstats", collection_name
                )
            except Exception as coll_error:
                collections_stats[collection_name] = {"error": str(coll_error)}

        # Formatear la respuesta
        response = {
            "database": {
                "name": mongo.db.name,
                "size_bytes": db_stats.get("dataSize", 0),
                "storage_size_bytes": db_stats.get("storageSize", 0),
                "collections_count": db_stats.get("collections", 0),
                "objects_count": db_stats.get("objects", 0),
                "indexes_count": db_stats.get("indexes", 0),
                "index_size_bytes": db_stats.get("indexSize", 0),
            },
            "collections": {
                collection: (
                    {
                        "size_bytes": stats.get("size", 0),
                        "storage_size_bytes": stats.get("storageSize", 0),
                        "count": stats.get("count", 0),
                        "avg_obj_size_bytes": stats.get("avgObjSize", 0),
                        "indexes_count": len(stats.get("indexSizes", {})),
                        "index_size_bytes": sum(stats.get("indexSizes", {}).values()),
                    }
                    if not isinstance(stats, dict) or "error" not in stats
                    else {"error": stats["error"]}
                )
                for collection, stats in collections_stats.items()
            },
        }

        return jsonify(response)

    except Exception as e:
        import traceback

        traceback.print_exc()
        return (
            jsonify(
                {
                    "error": "Error al obtener detalles de almacenamiento",
                    "message": str(e),
                    "type": type(e).__name__,
                }
            ),
            500,
        )


# Endpoints para planes
# REEMPLAZAR el endpoint reorder_planes en api.py con esta versión corregida:
from pymongo import DeleteOne, InsertOne, ReplaceOne, UpdateOne


@api.route("/api/planes/reorder", methods=["PUT"])
@login_required
def reorder_planes():
    """Reordenar planes según el nuevo orden proporcionado"""
    try:
        data = request.get_json()

        # Soportar ambos formatos: 'order' (nuevo) y 'operations' (legacy)
        if "order" in data:
            # Formato nuevo: [{ id: "...", order: 1 }, { id: "...", order: 2 }]
            order_data = data.get("order", [])

            if not order_data:
                return jsonify({"error": "No order data provided"}), 400

            # Crear operaciones de bulk update
            operations = []
            for item in order_data:
                plan_id = item.get("id")
                new_order = item.get("order")

                if not plan_id or new_order is None:
                    continue

                operations.append(
                    UpdateOne(
                        {"_id": ObjectId(plan_id)},
                        {
                            "$set": {
                                "order": new_order,
                                "updated_at": datetime.now(),
                                "updated_by": session.get("user"),
                            }
                        },
                    )
                )

        elif "operations" in data:
            # Formato legacy para compatibilidad
            operations = data.get("operations", [])

            if not operations:
                return jsonify({"error": "No operations provided"}), 400

        else:
            return jsonify({"error": "No valid data format provided"}), 400

        if not operations:
            return jsonify({"error": "No valid operations to perform"}), 400

        # Ejecutar operaciones en bulk
        result = mongo.db.planes.bulk_write(operations)

        logger.info(
            f"📊 Planes reordenados: {result.modified_count} modificados por {session.get('user')}"
        )

        return jsonify(
            {
                "success": True,
                "modified": result.modified_count,
                "matched": result.matched_count,
            }
        )

    except Exception as e:
        logger.error(f"❌ Error reordering planes: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return (
            jsonify(
                {
                    "error": "Error interno al reordenar planes",
                    "details": str(e) if current_app.debug else None,
                }
            ),
            500,
        )


# TAMBIÉN AÑADIR estos endpoints si no existen:


@api.route("/api/planes", methods=["GET"])
@login_required
def get_planes():
    """Obtener todos los planes ordenados"""
    try:
        # Obtener planes ordenados por 'order' (descendente) y luego por fecha de creación
        planes = list(
            mongo.db.planes.find().sort(
                [
                    ("order", -1),  # Orden personalizado (mayor = más arriba)
                    ("created_at", -1),  # Más recientes primero como fallback
                ]
            )
        )

        # Convertir ObjectId a string para JSON
        for plan in planes:
            plan["_id"] = str(plan["_id"])
            # Formatear fechas si existen
            if "created_at" in plan:
                plan["fecha_creacion"] = plan["created_at"].strftime("%Y-%m-%d")

        return jsonify(planes)

    except Exception as e:
        logger.error(f"❌ Error getting planes: {str(e)}")
        return jsonify({"error": "Error al obtener planes"}), 500


@api.route("/api/planes", methods=["POST"])
@login_required
def create_plan():
    """Crear nuevo plan"""
    try:
        data = request.get_json()

        titulo = data.get("titulo", "").strip()
        descripcion = data.get("descripcion", "").strip()
        prioridad = data.get("prioridad", 2)

        if not titulo:
            return jsonify({"error": "El título es obligatorio"}), 400

        # Obtener el orden más alto actual para colocar el nuevo plan al inicio
        max_order_plan = mongo.db.planes.find_one(sort=[("order", -1)])
        new_order = (max_order_plan.get("order", 0) + 1) if max_order_plan else 1

        new_plan = {
            "titulo": titulo,
            "descripcion": descripcion,
            "prioridad": int(prioridad),
            "order": new_order,
            "created_at": datetime.now(),
            "created_by": session.get("user"),
            "updated_at": datetime.now(),
        }

        result = mongo.db.planes.insert_one(new_plan)

        logger.info(f"📝 Nuevo plan creado: '{titulo}' por {session.get('user')}")

        return (
            jsonify(
                {
                    "success": True,
                    "id": str(result.inserted_id),
                    "message": "Plan creado correctamente",
                }
            ),
            201,
        )

    except Exception as e:
        logger.error(f"❌ Error creating plan: {str(e)}")
        return (
            jsonify(
                {
                    "error": "Error al crear plan",
                    "details": str(e) if current_app.debug else None,
                }
            ),
            500,
        )


@api.route("/api/planes/<plan_id>", methods=["DELETE"])
@login_required
def delete_plan(plan_id):
    """Eliminar plan por ID"""
    try:
        if not plan_id:
            return jsonify({"error": "ID de plan requerido"}), 400

        # Verificar que el plan existe
        plan = mongo.db.planes.find_one({"_id": ObjectId(plan_id)})
        if not plan:
            return jsonify({"error": "Plan no encontrado"}), 404

        # Eliminar el plan
        result = mongo.db.planes.delete_one({"_id": ObjectId(plan_id)})

        if result.deleted_count > 0:
            logger.info(
                f"🗑️ Plan eliminado: '{plan.get('titulo', 'Sin título')}' por {session.get('user')}"
            )
            return jsonify({"success": True, "message": "Plan eliminado correctamente"})
        else:
            return jsonify({"error": "No se pudo eliminar el plan"}), 500

    except Exception as e:
        logger.error(f"❌ Error deleting plan {plan_id}: {str(e)}")
        return (
            jsonify(
                {
                    "error": "Error al eliminar plan",
                    "details": str(e) if current_app.debug else None,
                }
            ),
            500,
        )


@api.route("/api/planes/<plan_id>/top", methods=["PUT"])
@login_required
def move_plan_to_top(plan_id):
    """Mover plan al inicio de la lista"""
    try:
        if not plan_id:
            return jsonify({"error": "ID de plan requerido"}), 400

        # Verificar que el plan existe
        plan = mongo.db.planes.find_one({"_id": ObjectId(plan_id)})
        if not plan:
            return jsonify({"error": "Plan no encontrado"}), 404

        # Obtener el orden más alto actual
        max_order_plan = mongo.db.planes.find_one(sort=[("order", -1)])
        new_order = (max_order_plan.get("order", 0) + 1) if max_order_plan else 1

        # Actualizar el plan para ponerlo al inicio
        result = mongo.db.planes.update_one(
            {"_id": ObjectId(plan_id)},
            {
                "$set": {
                    "order": new_order,
                    "updated_at": datetime.now(),
                    "updated_by": session.get("user"),
                }
            },
        )

        if result.modified_count > 0:
            logger.info(
                f"⬆️ Plan movido al top: '{plan.get('titulo', 'Sin título')}' por {session.get('user')}"
            )
            return jsonify(
                {"success": True, "message": "Plan movido al inicio correctamente"}
            )
        else:
            return jsonify({"error": "No se pudo mover el plan"}), 500

    except Exception as e:
        logger.error(f"❌ Error moving plan {plan_id} to top: {str(e)}")
        return (
            jsonify(
                {
                    "error": "Error al mover plan",
                    "details": str(e) if current_app.debug else None,
                }
            ),
            500,
        )
