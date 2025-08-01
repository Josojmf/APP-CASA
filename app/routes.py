from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, current_app
from app import mongo
from bson import ObjectId
from datetime import datetime, timedelta
from functools import wraps
import os
import traceback
import requests
from ddgs import DDGS
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

# ==========================================
# Decorador para autenticación
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated_function

# ==========================================
# Funciones para el servicio de IA mejorado
# ==========================================
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
        if family_data["usuarios"]:
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

# ==========================================
# Utilidades
# ==========================================
def get_food_image(food_name):
    """
    Obtener imagen de comida usando DuckDuckGo
    """
    try:
        with DDGS() as ddgs:
            results = list(ddgs.images(
                keywords=f"{food_name} food recipe",
                region="es-es",
                safesearch="moderate",
                size="medium",
                max_results=1
            ))
            if results:
                return results[0].get("image", "/static/img/default_food.jpg")
    except Exception as e:
        logger.error(f"Error get_food_image: {e}")
    
    return "/static/img/default_food.jpg"

def get_fecha_real_desde_dia_semana(dia_nombre):
    """
    Convertir nombre de día a fecha real de esta semana
    """
    dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
    try:
        hoy = datetime.now()
        dia_actual = hoy.weekday()
        indice_dia = dias.index(dia_nombre)
        diferencia = (indice_dia - dia_actual + 7) % 7
        fecha_objetivo = hoy + timedelta(days=diferencia)
        return fecha_objetivo.strftime("%Y-%m-%d")
    except ValueError:
        return datetime.now().strftime("%Y-%m-%d")

# ==========================================
# Rutas principales
# ==========================================
@main.route("/health")
def health_check():
    """Health check endpoint para Docker y tests"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }), 200

@main.route("/sw.js")
def service_worker():
    """Servir el service worker"""
    return current_app.send_static_file("sw.js")

@main.route('/')
@login_required
def index():
    """Página principal con usuarios"""
    try:
        users = list(mongo.db.users.find())
        vapid_public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
        
        # Estadísticas básicas
        stats = {
            "total_users": len(users),
            "users_at_home": len([u for u in users if u.get('encasa', False)]),
            "total_tasks": sum(len(u.get("tareas", [])) for u in users)
        }
        
        return render_template("index.html", 
                             users=users, 
                             vapid_public_key=vapid_public_key,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error index: {e}")
        return "Error interno del servidor", 500

@main.route('/users_cards')
@login_required
def users_cards():
    """Fragmento HTMX para las tarjetas de usuarios"""
    try:
        users = list(mongo.db.users.find())
        return render_template('components/cards_fragment.html', users=users)
    except Exception as e:
        logger.error(f"Error users_cards: {e}")
        return "Error al cargar usuarios", 500

@main.route('/user_card/<user_id>')
@login_required
def user_card(user_id):
    """Tarjeta individual de usuario"""
    try:
        user = mongo.db.users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return "Usuario no encontrado", 404
        return render_template("components/user_card.html", user=user)
    except Exception as e:
        logger.error(f"Error user_card: {e}")
        return "Error al cargar usuario", 500

@main.route('/tareas')
@login_required
def tareas():
    """Página de gestión de tareas"""
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])
            # Ordenar tareas por fecha de vencimiento
            tareas = user.get('tareas', [])
            if tareas:
                tareas.sort(key=lambda x: x.get('due_date', '9999-12-31'))
        
        vapid_public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
        
        # Estadísticas de tareas
        total_tasks = sum(len(user.get("tareas", [])) for user in users)
        overdue_tasks = 0
        today = datetime.now().strftime('%Y-%m-%d')
        
        for user in users:
            for tarea in user.get('tareas', []):
                if tarea.get('due_date', '9999-12-31') < today:
                    overdue_tasks += 1
        
        stats = {
            "total_tasks": total_tasks,
            "overdue_tasks": overdue_tasks
        }
        
        return render_template("tareas.html", 
                             users=users, 
                             vapid_public_key=vapid_public_key,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error tareas: {e}")
        return "Error al cargar tareas", 500

@main.route('/api/add_event', methods=['POST'])
@login_required
def add_event():
    try:
        data = request.get_json()
        title = data.get("title")
        start_date = data.get("start_date")
        end_date = data.get("end_date", start_date)
        description = data.get("description", "")
        color = data.get("color", "#9c27b0")  # Color púrpura por defecto

        if not title or not start_date:
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        new_event = {
            "title": title,
            "start": start_date,
            "end": end_date,
            "description": description,
            "reported_by": session.get("user"),
            "color": color,
            "created_at": datetime.now(),
            "type": "event"
        }

        result = mongo.db.events.insert_one(new_event)
        
        return jsonify({
            "success": True, 
            "event_id": str(result.inserted_id)
        })
    except Exception as e:
        logger.error(f"Error add_event: {e}")
        return jsonify({"error": "Error al añadir evento"}), 500

@main.route('/api/add_task', methods=['POST'])
@login_required
def add_task():
    try:
        data = request.get_json()
        titulo = data.get("titulo")
        asignee = data.get("asignee")
        due_date = data.get("due_date")
        prioridad = data.get("prioridad")
        pasos = data.get("pasos")

        if not titulo or not asignee or not due_date:
            return jsonify({"error": "Faltan campos obligatorios para la tarea"}), 400

        # Buscar usuario por nombre
        user = mongo.db.users.find_one({"nombre": asignee})
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404

        # Crear nueva tarea
        nueva_tarea = {
            "titulo": titulo,
            "due_date": due_date,
            "pasos": pasos,
            "prioridad": prioridad,
            "created_by": session.get('user'),
            "created_at": datetime.now(),
            "asignado": asignee
        }

        # Añadir tarea al usuario
        mongo.db.users.update_one(
            {"_id": user['_id']},
            {"$push": {"tareas": nueva_tarea}}
        )

        return jsonify({
            "success": True, 
            "task_id": str(nueva_tarea.get('_id', '')),
            "message": "Tarea añadida exitosamente"
        })
    except Exception as e:
        logger.error(f"Error add_task: {e}")
        return jsonify({"error": "Error al añadir tarea"}), 500

@main.route("/calendario")
@login_required
def calendario():
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])

        # Obtener eventos
        eventos = []
        events = list(mongo.db.events.find())
        for event in events:
            eventos.append({
                "title": f"⭐ {event['title']}",
                "start": event["start"],
                "end": event.get("end"),
                "allDay": True,
                "backgroundColor": event["color"],
                "borderColor": event["color"],
                "extendedProps": {
                    "type": "event",
                    "reported_by": event["reported_by"],
                    "description": event.get("description", "")
                }
            })

        # Obtener tareas
        for user in users:
            nombre = user.get("nombre", "Sin nombre")
            tareas = user.get("tareas", [])
            for tarea in tareas:
                # Determinar color según prioridad
                color_map = {
                    'urgente': '#ef4444',
                    'alta': '#f97316', 
                    'normal': '#f59e0b',
                    'baja': '#22c55e'
                }
                color = color_map.get(tarea.get('prioridad', 'normal'), '#10b981')
                
                eventos.append({
                    "title": f"{tarea.get('titulo', '')} - {nombre}",
                    "start": tarea.get("due_date"),
                    "allDay": True,
                    "backgroundColor": color,
                    "borderColor": color,
                    "extendedProps": {
                        "asignee": nombre,
                        "prioridad": tarea.get('prioridad', 'normal'),
                        "pasos": tarea.get('pasos', ''),
                        "user_id": str(user['_id'])
                    }
                })

        vapid_public_key = current_app.config.get("VAPID_PUBLIC_KEY", "")
        return render_template("calendario.html", 
                             users=users, 
                             events=eventos, 
                             vapid_public_key=vapid_public_key)
    except Exception as e:
        logger.error(f"Error calendario: {e}")
        return "Error al cargar calendario", 500

@main.route('/menus')
@login_required
def mostrar_menus():
    """Página de gestión de menús"""
    try:
        dias = ['Lunes','Martes','Miércoles','Jueves','Viernes','Sábado','Domingo']
        menus_data = {dia: {'comida': None, 'cena': None} for dia in dias}

        # Cargar menús existentes
        for menu in mongo.db.menus.find({}):
            dia = menu.get('dia')
            momento = menu.get('momento')
            titulo = menu.get('titulo')
            imagen = menu.get('img')
            asignaciones = menu.get('asignaciones', {})
            asignado = asignaciones.get(momento) if asignaciones else None

            if dia in menus_data and momento in ['comida', 'cena']:
                menus_data[dia][momento] = {
                    "titulo": titulo,
                    "img": imagen,
                    "asignado": asignado
                }

        users = list(mongo.db.users.find({}, {"nombre": 1}))
        
        # Estadísticas de menús
        total_menus = sum(1 for dia_data in menus_data.values() 
                         for momento_data in dia_data.values() 
                         if momento_data and momento_data.get('titulo'))
        assigned_menus = sum(1 for dia_data in menus_data.values() 
                           for momento_data in dia_data.values() 
                           if momento_data and momento_data.get('asignado'))
        
        stats = {
            "total_menus": total_menus,
            "assigned_menus": assigned_menus,
            "completion_percentage": int((assigned_menus / max(total_menus, 1)) * 100)
        }
        
        return render_template("menus.html", 
                             menus=menus_data, 
                             users=users,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error mostrar_menus: {e}")
        return "Error al cargar menús", 500

@main.route('/api/add_menu', methods=['POST'])
@login_required
def add_menu():
    """Añadir nuevo menú"""
    try:
        data = request.get_json()
        dia = data.get("dia")
        momento = data.get("momento")
        titulo = data.get("titulo")

        if not dia or not momento or not titulo:
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        # Verificar si ya existe
        existing = mongo.db.menus.find_one({
            "dia": dia,
            "momento": momento
        })

        # Obtener imagen
        if existing and existing.get("img"):
            image_url = existing["img"]
        else:
            try:
                image_url = get_food_image(titulo)
            except Exception as e:
                logger.error(f"Error get_food_image: {e}")
                image_url = "/static/img/default_food.jpg"

        # Actualizar o crear menú
        mongo.db.menus.update_one(
            {"dia": dia, "momento": momento},
            {"$set": {
                "titulo": titulo,
                "img": image_url,
                "updated_by": session.get('user'),
                "updated_at": datetime.now()
            }},
            upsert=True
        )
        
        return jsonify({"status": "ok", "image": image_url})
    except Exception as e:
        logger.error(f"Error add_menu: {e}")
        return jsonify({"error": "Error al añadir menú"}), 500

@main.route('/api/reset_menus', methods=['DELETE'])
@login_required
def reset_menus():
    """Reiniciar todos los menús"""
    try:
        result = mongo.db.menus.delete_many({})
        
        # También eliminar tareas de cocina relacionadas
        mongo.db.users.update_many(
            {},
            {"$pull": {"tareas": {"titulo": {"$regex": "^Preparar (Comida|Cena):"}}}}
        )
        
        return jsonify({
            "status": "reseteado", 
            "deleted_count": result.deleted_count
        })
    except Exception as e:
        logger.error(f"Error reset_menus: {e}")
        return jsonify({"error": "Error al reiniciar menús"}), 500

@main.route("/api/asignar_comida", methods=["POST"])
@login_required
def asignar_comida():
    """Asignar persona responsable de preparar comida"""
    try:
        data = request.json
        dia = data.get("dia")
        tipo = data.get("tipo")  # 'comida' o 'cena'
        miembro = data.get("miembro")

        if not dia or not tipo or not miembro:
            return jsonify({"error": "Faltan campos obligatorios"}), 400

        # Verificar que el usuario existe
        user = mongo.db.users.find_one({"nombre": miembro})
        if not user:
            return jsonify({"error": "Usuario no encontrado"}), 404

        # Actualizar asignación en menús
        mongo.db.menus.update_one(
            {"dia": dia},
            {"$set": {
                f"asignaciones.{tipo}": miembro,
                "assigned_by": session.get('user'),
                "assigned_at": datetime.now()
            }},
            upsert=True
        )

        # Obtener información del menú para crear tarea
        menu = mongo.db.menus.find_one({"dia": dia})
        titulo_menu = "Comida sin especificar"
        
        if menu and menu.get("titulo"):
            titulo_menu = menu["titulo"]

        momento = tipo.capitalize()
        tarea_titulo = f"Preparar {momento}: {titulo_menu}"
        fecha_real = get_fecha_real_desde_dia_semana(dia)

        # Eliminar tareas previas de este menú
        mongo.db.users.update_many(
            {},
            {"$pull": {
                "tareas": {
                    "titulo": tarea_titulo,
                    "due_date": fecha_real
                }
            }}
        )

        # Crear nueva tarea
        nueva_tarea = {
            "titulo": tarea_titulo,
            "due_date": fecha_real,
            "pasos": f"Encargado de preparar la {tipo} del día {dia}",
            "prioridad": "normal",
            "created_by": session.get('user'),
            "created_at": datetime.now(),
            "tipo": "cocina",
            "asignado": miembro  # Agregar campo asignado
        }

        # Asignar tarea al usuario
        mongo.db.users.update_one(
            {"nombre": miembro},
            {"$push": {"tareas": nueva_tarea}}
        )

        return jsonify({
            "status": "ok", 
            "tarea_creada": True,
            "tarea_titulo": tarea_titulo
        })
    except Exception as e:
        logger.error(f"Error asignar_comida: {e}")
        return jsonify({"error": "Error al asignar comida"}), 500

@main.route("/lista_compra")
@login_required
def lista_compra():
    """Página de lista de la compra"""
    try:
        items = list(mongo.db.lista_compra.find().sort("created_at", -1))
        
        # Estadísticas
        total_items = len(items)
        completed_items = len([item for item in items if item.get('comprado', False)])
        
        stats = {
            "total_items": total_items,
            "completed_items": completed_items,
            "pending_items": total_items - completed_items,
            "completion_percentage": int((completed_items / max(total_items, 1)) * 100)
        }
        
        return render_template("lista_compra.html", 
                             items=items,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error lista_compra: {e}")
        return "Error al cargar lista de compra", 500

@main.route('/configuracion')
@login_required
def configuracion():
    """Página de configuración"""
    try:
        users = list(mongo.db.users.find())
        for user in users:
            user['_id'] = str(user['_id'])
        
        # Estadísticas del sistema
        total_subscriptions = mongo.db.subscriptions.count_documents({})
        total_completed_tasks = mongo.db.completed_tasks.count_documents({})
        
        system_stats = {
            "total_users": len(users),
            "total_subscriptions": total_subscriptions,
            "total_completed_tasks": total_completed_tasks,
            "current_theme": session.get('theme', 'light')
        }
        
        return render_template("configuracion.html", 
                             users=users,
                             system_stats=system_stats)
    except Exception as e:
        logger.error(f"Error configuracion: {e}")
        return "Error al cargar configuración", 500

@main.route('/api/add_user', methods=['POST'])
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
        if mongo.db.users.find_one({"nombre": {"$regex": f"^{nombre}$", "$options": "i"}}):
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
            "created_by": session.get('user'),
            "created_at": datetime.now()
        }

        result = mongo.db.users.insert_one(new_user)
        
        return jsonify({
            "success": True, 
            "user_id": str(result.inserted_id)
        }), 201
    except Exception as e:
        logger.error(f"Error add_user: {e}")
        return jsonify({"error": "Error al crear usuario"}), 500

@main.route('/api/delete_user/<user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """Eliminar usuario"""
    try:
        current_user = session.get('user')
        logger.info(f"Intento de eliminación por: {current_user}, ID objetivo: {user_id}")
        obj_id = ObjectId(user_id)
        # Obtener información del usuario antes de eliminar
        user = mongo.db.users.find_one({"_id": obj_id})
        if not user:
            logger.warning(f"Usuario no encontrado: {user_id}")
            return jsonify({
                "error": "Usuario no encontrado",
                "message": f"No existe usuario con ID {user_id}"
            }), 404

        # Registrar acción
        logger.info(f"Eliminando usuario: {user['nombre']} (ID: {user_id})")

        # Eliminar usuario
        mongo.db.users.delete_one({"_id": obj_id})
        
        # Limpiar datos relacionados
        nombre_usuario = user.get("nombre")
        result_subs = mongo.db.subscriptions.delete_many({"user": nombre_usuario})
        result_tasks = mongo.db.completed_tasks.delete_many({"completed_by": nombre_usuario})
        
        logger.info(f"Datos relacionados eliminados: "
                    f"{result_subs.deleted_count} suscripciones, "
                    f"{result_tasks.deleted_count} tareas completadas")
        
        return jsonify({
            "success": True,
            "message": f"Usuario {nombre_usuario} eliminado correctamente",
            "deleted_subs": result_subs.deleted_count,
            "deleted_tasks": result_tasks.deleted_count
        }), 200
        
    except Exception as e:
        logger.error(f"Error crítico en delete_user: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({
            "error": "Error interno del servidor",
            "message": str(e)
        }), 500

@main.route('/api/toggle_theme', methods=['POST'])
@login_required
def toggle_theme():
    """Cambiar tema de la aplicación"""
    try:
        data = request.json
        new_theme = data.get("theme")
        
        if new_theme not in ['light', 'dark']:
            return jsonify({"error": "Tema inválido"}), 400
            
        session["theme"] = new_theme
        return jsonify({"success": True, "theme": new_theme})
    except Exception as e:
        logger.error(f"Error toggle_theme: {e}")
        return jsonify({"error": "Error al cambiar tema"}), 500

@main.route("/chat")
@login_required
def chat():
    """Página de chat familiar"""
    try:
        # Recuperar últimos mensajes
        messages = list(mongo.db.messages.find().sort("timestamp", -1).limit(50))
        messages.reverse()
        
        for m in messages:
            m["_id"] = str(m["_id"])
            if m.get("timestamp"):
                m["timestamp"] = m["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

        # Estadísticas del chat
        total_messages = mongo.db.messages.count_documents({})
        today_messages = mongo.db.messages.count_documents({
            "timestamp": {"$gte": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        
        stats = {
            "total_messages": total_messages,
            "today_messages": today_messages
        }

        return render_template("chat.html", 
                             messages=messages,
                             stats=stats)
    except Exception as e:
        logger.error(f"Error chat: {e}")
        return "Error al cargar chat", 500

@main.route("/chat/messages", methods=["GET"])
@login_required
def get_chat_messages():
    """API para obtener mensajes del chat"""
    try:
        # Asegurar índice para mejorar rendimiento
        mongo.db.messages.create_index("timestamp")

        # Obtener últimos mensajes
        limit = int(request.args.get('limit', 50))
        messages = list(mongo.db.messages.find().sort("timestamp", -1).limit(limit))
        messages.reverse()

        # Formatear para JSON
        for m in messages:
            m["_id"] = str(m["_id"])
            if m.get("timestamp"):
                m["timestamp"] = m["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

        return jsonify(messages), 200
    except Exception as e:
        logger.error(f"Error get_chat_messages: {e}")
        return jsonify({"error": "Error al obtener mensajes"}), 500

@main.route("/asistente-familiar")
@login_required
def asistente_familiar_page():
    """Página del asistente familiar con IA mejorada"""
    try:
        # Estadísticas del asistente
        chat_count = len(session.get("chat_history", []))
        
        # Obtener datos familiares para mostrar en el dashboard
        family_data = get_comprehensive_family_data()
        
        stats = {
            "messages_in_session": chat_count,
            "ai_available": bool(os.getenv("GROQ_API_KEY")),
            "family_data": family_data if "error" not in family_data else None
        }
        
        return render_template("ai.html", stats=stats)
    except Exception as e:
        logger.error(f"Error asistente_familiar_page: {e}")
        return "Error al cargar asistente", 500

@main.route("/api/test-push/<username>")
@login_required
def test_push(username):
    """Endpoint para probar notificaciones push - MEJORADO"""
    try:
        # Solo admin puede probar push
        if session.get('user') != 'Joso':
            return jsonify({"error": "Permisos insuficientes"}), 403

        from pywebpush import webpush, WebPushException
        from urllib.parse import urlparse
        import json

        subscripcion = mongo.db.subscriptions.find_one({"user": username})
        if not subscripcion:
            return jsonify({"error": f"No se encontró suscripción para {username}"}), 404

        vapid_private_key = current_app.config.get("VAPID_PRIVATE_KEY")
        if not vapid_private_key:
            return jsonify({"error": "VAPID no configurado"}), 500

        def get_vapid_claims(endpoint_url):
            try:
                parsed = urlparse(endpoint_url)
                return {
                    "aud": f"{parsed.scheme}://{parsed.netloc}",
                    "sub": "mailto:joso.jmf@gmail.com"
                }
            except Exception:
                return None

        success_count = 0
        for sub in subscripcion.get("subscriptions", []):
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
                        "title": f"🔔 Test push a {username}",
                        "body": f"Hola {username}, esta es una notificación de prueba desde House App.",
                        "icon": "/static/icons/house-icon.png",
                        "url": "/"
                    }),
                    vapid_private_key=vapid_private_key,
                    vapid_claims=claims
                )
                success_count += 1
                logger.info(f"📲 Test push enviada a {username}")
            except WebPushException as ex:
                logger.error(f"❌ Error al enviar push: {repr(ex)}")
                return jsonify({
                    "error": "Fallo al enviar push", 
                    "details": str(ex)
                }), 500

        return jsonify({
            "status": "ok", 
            "message": f"Push enviada a {username}",
            "sent_count": success_count
        })
    except Exception as e:
        logger.error(f"Error test_push: {e}")
        return jsonify({"error": "Error interno"}), 500

# ==========================================
# Rutas del servicio de IA mejorado
# ==========================================

@main.route("/api/chatfd", methods=["POST"])
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
            "pending_shopping": family_data.get("lista_compra", {}).get("items_pendientes", 0),
            "completed_today": family_data.get("estadisticas", {}).get("tareas_completadas_hoy", 0)
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

@main.route("/api/ai_status", methods=["GET"])
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
                    "shopping_pending": mongo.db.lista_compra.count_documents({"comprado": {"$ne": True}}),
                    "users_home": mongo.db.users.count_documents({"encasa": True}),
                    "completed_today": mongo.db.completed_tasks.count_documents({
                        "completed_at": {
                            "$gte": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                        }
                    }),
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

@main.route("/api/family_insights", methods=["GET"])
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
                    "suggestion": "Considera redistribuir algunas tareas para balancear la carga de trabajo"
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

@main.route("/api/clear_chat", methods=["POST"])
@login_required
def clear_chat_history():
    """Limpiar el historial de chat del usuario"""
    try:
        session.pop("chat_history", None)
        logger.info(f"Chat history cleared for user: {session.get('user')}")
        return jsonify({"success": True, "message": "Historial de chat limpiado"})
    except Exception as e:
        logger.error(f"Error clearing chat history: {e}")
        return jsonify({"error": "Error al limpiar historial"}), 500

@main.route("/api/debug/family_data")
@login_required  
def debug_family_data():
    """Endpoint de debug para ver los datos familiares"""
    try:
        # Solo permitir a usuarios autorizados
        if session.get('user') not in ['Joso', 'Admin']:
            return jsonify({"error": "No autorizado"}), 403
            
        family_data = get_comprehensive_family_data()
        return jsonify(family_data)
    except Exception as e:
        logger.error(f"Error debug_family_data: {e}")
        return jsonify({"error": str(e)}), 500

# ==========================================
# Manejo de errores mejorado
# ==========================================
@main.errorhandler(404)
def not_found_error(error):
    logger.error(f"404 Error: {request.url}")
    return render_template('errors/404.html'), 404

@main.errorhandler(500)
def internal_error(error):
    logger.error(f"500 Error: {error}")
    return render_template('errors/500.html'), 500

@main.errorhandler(403)
def forbidden_error(error):
    logger.error(f"403 Error: {error}")
    return render_template('errors/403.html'), 403