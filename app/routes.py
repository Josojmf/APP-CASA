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
import traceback  
from datetime import datetime
import re
#Retry 
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import hashlib
from collections import OrderedDict
import threading






# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

main = Blueprint('main', __name__)

# Configuración de retry para requests
retry_strategy = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[408, 429, 500, 502, 503, 504]
)
adapter = HTTPAdapter(max_retries=retry_strategy)

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
# Cache de categorías de Mercadona
# ==========================================
class SearchCache:
    """
    Sistema de caché inteligente para búsquedas de productos
    con TTL, LRU y limpieza automática
    """
    def __init__(self, max_size=500, ttl_minutes=30):
        self.max_size = max_size
        self.ttl_seconds = ttl_minutes * 60
        self.cache = OrderedDict()
        self.access_times = {}
        self.lock = threading.RLock()
        
    def _generate_key(self, query, filters=None):
        """Generar clave única para la búsqueda"""
        key_data = f"{query.lower().strip()}"
        if filters:
            key_data += f"_filters_{str(sorted(filters.items()))}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _is_expired(self, timestamp):
        """Verificar si una entrada ha expirado"""
        return (datetime.now() - timestamp).total_seconds() > self.ttl_seconds
    
    def _cleanup_expired(self):
        """Limpiar entradas expiradas"""
        current_time = datetime.now()
        expired_keys = []
        
        for key, (_, timestamp) in self.cache.items():
            if self._is_expired(timestamp):
                expired_keys.append(key)
        
        for key in expired_keys:
            self.cache.pop(key, None)
            self.access_times.pop(key, None)
    
    def get(self, query, filters=None):
        """Obtener resultado de caché si existe y no ha expirado"""
        with self.lock:
            key = self._generate_key(query, filters)
            
            if key in self.cache:
                data, timestamp = self.cache[key]
                
                if not self._is_expired(timestamp):
                    # Mover al final (LRU)
                    self.cache.move_to_end(key)
                    self.access_times[key] = datetime.now()
                    
                    logger.info(f"🎯 Cache HIT para búsqueda: '{query}'")
                    return data
                else:
                    # Eliminar entrada expirada
                    del self.cache[key]
                    self.access_times.pop(key, None)
                    logger.info(f"⏰ Cache EXPIRED para búsqueda: '{query}'")
            
            logger.info(f"💥 Cache MISS para búsqueda: '{query}'")
            return None
    
    def set(self, query, data, filters=None):
        """Guardar resultado en caché"""
        with self.lock:
            key = self._generate_key(query, filters)
            current_time = datetime.now()
            
            # Limpiar expirados antes de añadir
            self._cleanup_expired()
            
            # Si la caché está llena, eliminar el menos usado (LRU)
            if len(self.cache) >= self.max_size:
                oldest_key = next(iter(self.cache))
                del self.cache[oldest_key]
                self.access_times.pop(oldest_key, None)
                logger.info(f"🗑️ Cache LRU eliminación: {oldest_key}")
            
            self.cache[key] = (data, current_time)
            self.access_times[key] = current_time
            
            logger.info(f"💾 Cache SAVE para búsqueda: '{query}' ({len(data.get('products', []))} productos)")
    
    def clear(self):
        """Limpiar toda la caché"""
        with self.lock:
            self.cache.clear()
            self.access_times.clear()
            logger.info("🧹 Cache completamente limpiada")
    
    def get_stats(self):
        """Obtener estadísticas de la caché"""
        with self.lock:
            self._cleanup_expired()
            
            total_entries = len(self.cache)
            memory_usage = sum(len(str(data)) for data, _ in self.cache.values())
            
            return {
                "total_entries": total_entries,
                "max_size": self.max_size,
                "memory_usage_bytes": memory_usage,
                "ttl_minutes": self.ttl_seconds // 60,
                "oldest_entry": min(self.access_times.values()) if self.access_times else None,
                "newest_entry": max(self.access_times.values()) if self.access_times else None
            }

# Instancia global de caché
search_cache = SearchCache(max_size=500, ttl_minutes=30)


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
    
# ==========================================
#Caché de 2 mins
# ==========================================
_mercadona_categories_cache = {
    "data": None,
    "timestamp": None
}
_CATEGORIES_CACHE_TTL = 120  # segundos

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

def get_menu_data():
    """Obtener datos completos de los menús de la semana"""
    try:
        menus = list(mongo.db.menus.find())
        menu_data = {}
        
        dias = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']
        
        for dia in dias:
            menu_data[dia] = {'comida': None, 'cena': None}
        
        for menu in menus:
            dia = menu.get('dia')
            momento = menu.get('momento')
            titulo = menu.get('titulo')
            
            if dia in menu_data and momento in ['comida', 'cena'] and titulo:
                menu_data[dia][momento] = {
                    'titulo': titulo,
                    'asignado': menu.get('asignaciones', {}).get(momento)
                }
        
        return menu_data
    except Exception as e:
        logger.error(f"Error get_menu_data: {e}")
        return {}

def analyze_shopping_needs(menu_data, existing_shopping_items):
    """Analizar necesidades de compra basado en menús y lista existente"""
    try:
        # Base de datos de ingredientes comunes por plato/tipo de comida
        ingredient_database = {
            # Carnes y proteínas
            'pollo': ['pollo', 'aceite', 'sal', 'pimienta', 'ajo', 'cebolla'],
            'pescado': ['pescado', 'limón', 'aceite', 'sal', 'ajo'],
            'ternera': ['ternera', 'aceite', 'sal', 'pimienta', 'cebolla'],
            'cerdo': ['cerdo', 'aceite', 'sal', 'pimienta'],
            'hamburguesa': ['carne picada', 'pan hamburguesa', 'lechuga', 'tomate', 'cebolla', 'queso'],
            
            # Pasta y arroces
            'pasta': ['pasta', 'tomate frito', 'aceite', 'ajo', 'queso parmesano'],
            'espaguetis': ['espaguetis', 'tomate frito', 'aceite', 'ajo', 'albahaca'],
            'macarrones': ['macarrones', 'tomate frito', 'queso', 'aceite'],
            'arroz': ['arroz', 'aceite', 'sal', 'cebolla', 'ajo'],
            'paella': ['arroz', 'azafrán', 'pollo', 'gambas', 'judías verdes', 'tomate', 'aceite'],
            'risotto': ['arroz arborio', 'cebolla', 'vino blanco', 'queso parmesano', 'mantequilla'],
            
            # Verduras y ensaladas
            'ensalada': ['lechuga', 'tomate', 'cebolla', 'aceite', 'vinagre'],
            'gazpacho': ['tomate', 'pepino', 'pimiento', 'cebolla', 'ajo', 'aceite', 'vinagre', 'pan'],
            'verduras': ['brócoli', 'zanahoria', 'calabacín', 'aceite', 'sal'],
            
            # Legumbres
            'lentejas': ['lentejas', 'cebolla', 'zanahoria', 'ajo', 'laurel', 'aceite'],
            'garbanzos': ['garbanzos', 'cebolla', 'tomate', 'pimiento', 'aceite', 'pimentón'],
            'judías': ['judías blancas', 'cebolla', 'tomate', 'aceite', 'ajo'],
            
            # Huevos
            'tortilla': ['huevos', 'patatas', 'cebolla', 'aceite', 'sal'],
            'huevos': ['huevos', 'aceite', 'sal'],
            
            # Sopas
            'sopa': ['caldo', 'verduras', 'fideos', 'aceite'],
            'crema': ['calabaza', 'cebolla', 'nata', 'aceite', 'sal'],
            
            # Básicos siempre necesarios
            'básicos': ['pan', 'leche', 'huevos', 'aceite', 'sal', 'azúcar', 'harina']
        }
        
        # Obtener lista de productos ya existentes (en lowercase para comparación)
        existing_items = set()
        for item in existing_shopping_items:
            if not item.get('comprado', False):  # Solo productos no comprados
                existing_items.add(item.get('nombre', '').lower().strip())
        
        # Analizar menús y generar lista de ingredientes necesarios
        needed_ingredients = set()
        menu_analysis = []
        
        for dia, comidas in menu_data.items():
            for momento, menu_info in comidas.items():
                if menu_info and menu_info.get('titulo'):
                    titulo = menu_info['titulo'].lower()
                    menu_analysis.append(f"{dia} {momento}: {menu_info['titulo']}")
                    
                    # Buscar ingredientes basándose en palabras clave
                    for key, ingredients in ingredient_database.items():
                        if key in titulo:
                            needed_ingredients.update(ingredients)
                            break
                    else:
                        # Si no encuentra coincidencias específicas, añadir ingredientes básicos
                        needed_ingredients.update(['aceite', 'sal', 'ajo', 'cebolla'])
        
        # Añadir básicos siempre necesarios
        needed_ingredients.update(ingredient_database['básicos'])
        
        # Filtrar ingredientes que ya están en la lista
        missing_ingredients = []
        for ingredient in needed_ingredients:
            if ingredient not in existing_items:
                missing_ingredients.append(ingredient.title())
        
        return {
            'menu_analysis': menu_analysis,
            'needed_ingredients': sorted(list(needed_ingredients)),
            'missing_ingredients': sorted(missing_ingredients),
            'existing_items': sorted(list(existing_items))
        }
        
    except Exception as e:
        logger.error(f"Error analyze_shopping_needs: {e}")
        return {
            'menu_analysis': [],
            'needed_ingredients': [],
            'missing_ingredients': [],
            'existing_items': []
        }

def generate_enhanced_ai_context(family_data, user_query):
    """Generar contexto mejorado incluyendo análisis de menús y compras"""
    
    if not family_data or "error" in family_data:
        return "No hay datos familiares disponibles en este momento."
    
    # Obtener contexto básico
    basic_context = generate_ai_context(family_data, user_query)
    
    # Análisis de consulta para contenido de menús/compras
    query_lower = user_query.lower()
    
    # Si la consulta incluye palabras relacionadas con compras/menús
    if any(word in query_lower for word in ["compra", "menu", "menú", "ingrediente", "cocin", "receta", "product", "necesit", "falta", "añadir", "agregar"]):
        try:
            # Obtener datos de menús
            menu_data = get_menu_data()
            existing_shopping = family_data.get('lista_compra', {}).get('items_detalle', [])
            
            # Analizar necesidades de compra
            shopping_analysis = analyze_shopping_needs(menu_data, existing_shopping)
            
            enhanced_context = basic_context + f"""

=== ANÁLISIS DE MENÚS Y COMPRAS ===

MENÚS DE LA SEMANA:
{chr(10).join(['• ' + menu for menu in shopping_analysis['menu_analysis']])}

ANÁLISIS DE INGREDIENTES:
• Ingredientes necesarios para los menús: {len(shopping_analysis['needed_ingredients'])} productos
• Productos que faltan en la lista: {len(shopping_analysis['missing_ingredients'])} productos
• Productos ya en lista pendiente: {len(shopping_analysis['existing_items'])} productos

PRODUCTOS QUE FALTAN Y DEBERÍAN AÑADIRSE:
{chr(10).join(['• ' + item for item in shopping_analysis['missing_ingredients'][:15]])}

INSTRUCCIONES ESPECIALES PARA COMPRAS:
1. Si el usuario pregunta sobre qué comprar o qué falta, usa esta información
2. Si el usuario confirma añadir productos, utiliza la función add_shopping_items_bulk
3. Sugiere productos específicos basados en los menús planificados
4. Prioriza ingredientes que faltan para menús próximos
"""
            return enhanced_context
            
        except Exception as e:
            logger.error(f"Error generating enhanced context: {e}")
    
    return basic_context

# ==========================================
# FUNCIÓN PARA AÑADIR PRODUCTOS EN LOTE
# ==========================================

@main.route('/api/add_shopping_items_bulk', methods=['POST'])
@login_required
def add_shopping_items_bulk():
    """Añadir múltiples productos a la lista de compra de una vez"""
    try:
        data = request.get_json()
        products = data.get('products', [])
        
        if not products or not isinstance(products, list):
            return jsonify({"error": "Lista de productos vacía o inválida"}), 400
        
        added_count = 0
        skipped_count = 0
        added_products = []
        
        for product_name in products:
            product_name = product_name.strip().title()
            
            if not product_name:
                continue
                
            # Verificar si ya existe
            existing = mongo.db.lista_compra.find_one(
                {"nombre": {"$regex": f"^{product_name}$", "$options": "i"}}
            )
            
            if existing:
                skipped_count += 1
                continue
            
            # Añadir producto
            new_item = {
                "nombre": product_name,
                "cantidad": "1",
                "unidad": "",
                "comprado": False,
                "created_by": session.get("user"),
                "created_at": datetime.now(),
                "added_by_ai": True  # Marcar como añadido por IA
            }
            
            mongo.db.lista_compra.insert_one(new_item)
            added_count += 1
            added_products.append(product_name)
        
        # Notificar si se añadieron productos
        if added_count > 0:
            products_text = ", ".join(added_products[:5])
            if len(added_products) > 5:
                products_text += f" y {len(added_products) - 5} más"
                
            # Notificación push (si existe la función)
            try:
                # Importar función de notificaciones si existe
                from app.api import send_push_to_all
                send_push_to_all(
                    title="🛒 Productos añadidos por Casa AI",
                    body=f"Se añadieron {added_count} productos: {products_text}",
                    url="/lista_compra"
                )
            except ImportError:
                pass  # Si no existe la función de push, continuar sin error
        
        return jsonify({
            "success": True,
            "added_count": added_count,
            "skipped_count": skipped_count,
            "added_products": added_products,
            "message": f"Se añadieron {added_count} productos a la lista de compra"
        })
        
    except Exception as e:
        logger.error(f"Error add_shopping_items_bulk: {e}")
        return jsonify({"error": "Error al añadir productos"}), 500

# ==========================================
# RUTA PARA ANÁLISIS DE COMPRAS
# ==========================================

@main.route('/api/shopping_analysis', methods=['GET'])
@login_required
def get_shopping_analysis():
    """Obtener análisis detallado de necesidades de compra"""
    try:
        # Obtener datos
        menu_data = get_menu_data()
        shopping_items = list(mongo.db.lista_compra.find().sort("created_at", -1))
        
        # Realizar análisis
        analysis = analyze_shopping_needs(menu_data, shopping_items)
        
        return jsonify({
            "success": True,
            "analysis": analysis,
            "summary": {
                "total_menus": sum(1 for dia in menu_data.values() 
                                 for comida in dia.values() if comida),
                "missing_ingredients": len(analysis['missing_ingredients']),
                "existing_items": len(analysis['existing_items'])
            },
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        
    except Exception as e:
        logger.error(f"Error get_shopping_analysis: {e}")
        return jsonify({"error": "Error al generar análisis"}), 500

# ==========================================
# MODIFICAR LA FUNCIÓN CHAT_FAMILIAR EXISTENTE
# ==========================================

# REEMPLAZAR la función chat_familiar() existente con esta versión mejorada:

@main.route("/api/chatfd", methods=["POST"])
@login_required
def chat_familiar():
    """Chat con asistente familiar con capacidades de gestión de compras"""
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
        
        # 🧠 Generar contexto inteligente mejorado (incluye menús y compras)
        smart_context = generate_enhanced_ai_context(family_data, q)
        
        # Detectar si es una confirmación para añadir productos
        is_confirmation = any(word in q.lower() for word in ["sí", "si", "vale", "ok", "añade", "agrega", "confirmo", "acepto"])
        
        # Inicializar historial si no existe
        if "chat_history" not in session:
            session["chat_history"] = [
                {
                    "role": "system",
                    "content": f"""Eres 'Casa AI', un asistente familiar inteligente especializado en optimización doméstica.

CAPACIDADES PRINCIPALES:
• Análisis y optimización de tareas familiares
• Gestión inteligente de lista de compra basada en menús
• Análisis de ingredientes necesarios vs disponibles
• Recomendaciones automáticas de productos a comprar
• Planificación de menús y consejos de cocina
• Sugerencias personalizadas para el hogar

INSTRUCCIONES IMPORTANTES:
1. Usa SIEMPRE los datos actualizados para tus respuestas
2. Cuando analices necesidades de compra, sé específico con productos faltantes
3. Si recomiendas añadir productos, pregunta SIEMPRE al usuario si quiere que los añadas automáticamente
4. Si el usuario confirma añadir productos, responde con un JSON especial (ver formato abajo)
5. Prioriza ingredientes para menús de los próximos días
6. Sugiere cantidades aproximadas cuando sea relevante

FORMATO ESPECIAL PARA AÑADIR PRODUCTOS:
Si el usuario confirma que quiere añadir productos, responde con:
```json
{{"action": "add_products", "products": ["Producto 1", "Producto 2", "etc"], "message": "Tu mensaje explicativo"}}
```

DATOS ACTUALES:
{smart_context}

Responde de manera práctica y específica."""
                }
            ]
        else:
            # Actualizar contexto con datos frescos
            session["chat_history"][0]["content"] = f"""Eres 'Casa AI', un asistente familiar inteligente especializado en optimización doméstica.

CAPACIDADES PRINCIPALES:
• Análisis y optimización de tareas familiares
• Gestión inteligente de lista de compra basada en menús
• Análisis de ingredientes necesarios vs disponibles
• Recomendaciones automáticas de productos a comprar
• Planificación de menús y consejos de cocina
• Sugerencias personalizadas para el hogar

INSTRUCCIONES IMPORTANTES:
1. Usa SIEMPRE los datos actualizados para tus respuestas
2. Cuando analices necesidades de compra, sé específico con productos faltantes
3. Si recomiendas añadir productos, pregunta SIEMPRE al usuario si quiere que los añadas automáticamente
4. Si el usuario confirma añadir productos, responde con un JSON especial (ver formato abajo)
5. Prioriza ingredientes para menús de los próximos días
6. Sugiere cantidades aproximadas cuando sea relevante

FORMATO ESPECIAL PARA AÑADIR PRODUCTOS:
Si el usuario confirma que quiere añadir productos, responde con:
```json
{{"action": "add_products", "products": ["Producto 1", "Producto 2", "etc"], "message": "Tu mensaje explicativo"}}
```

DATOS ACTUALIZADOS:
{smart_context}

Responde de manera práctica y específica."""

        # Limitar historial
        MAX_HISTORY = 15
        if len(session["chat_history"]) > MAX_HISTORY:
            session["chat_history"] = (
                session["chat_history"][:1] + 
                session["chat_history"][-(MAX_HISTORY - 1):]
            )

        # Añadir mensaje del usuario
        session["chat_history"].append({"role": "user", "content": q})

        # Petición a Groq
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": session["chat_history"],
            "max_tokens": 1200,
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

        # Procesar respuesta especial para añadir productos
        action_performed = None
        if "```json" in answer and '"action": "add_products"' in answer:
            try:
                # Extraer JSON de la respuesta
                json_start = answer.find("```json") + 7
                json_end = answer.find("```", json_start)
                json_str = answer[json_start:json_end].strip()
                
                action_data = json.loads(json_str)
                
                if action_data.get("action") == "add_products":
                    products = action_data.get("products", [])
                    
                    if products:
                        # Añadir productos automáticamente
                        bulk_response = requests.post(
                            request.url_root + 'api/add_shopping_items_bulk',
                            json={"products": products},
                            headers={"Content-Type": "application/json"},
                            cookies=request.cookies
                        )
                        
                        if bulk_response.status_code == 200:
                            bulk_data = bulk_response.json()
                            action_performed = {
                                "type": "products_added",
                                "added_count": bulk_data.get("added_count", 0),
                                "added_products": bulk_data.get("added_products", []),
                                "skipped_count": bulk_data.get("skipped_count", 0)
                            }
                            
                            # Limpiar el JSON de la respuesta para mostrar solo el mensaje
                            answer = action_data.get("message", "Productos añadidos correctamente")
                            
            except Exception as e:
                logger.error(f"Error procesando acción de productos: {e}")

        # Añadir respuesta al historial
        session["chat_history"].append({"role": "assistant", "content": answer})

        # Datos adicionales para el frontend
        additional_data = {
            "data_timestamp": family_data.get("fecha_consulta"),
            "active_tasks": family_data.get("estadisticas", {}).get("total_tareas_activas", 0),
            "users_home": family_data.get("estadisticas", {}).get("usuarios_en_casa", 0),
            "pending_shopping": family_data.get("lista_compra", {}).get("items_pendientes", 0),
            "completed_today": family_data.get("estadisticas", {}).get("tareas_completadas_hoy", 0)
        }

        response = {
            "answer": answer,
            "status": "success",
            "model": "llama-3.1-8b-instant",
            "context_data": additional_data
        }
        
        # Añadir información de acción si se realizó
        if action_performed:
            response["action_performed"] = action_performed

        return jsonify(response)

    except Exception as e:
        logger.error(f"Error en chat_familiar: {traceback.format_exc()}")
        return jsonify({"error": "Error interno del servidor"}), 500

# ==========================================
# Rutas de mercadona
# ==========================================
MERCADONA_BASE_URL = "https://tienda.mercadona.es/api"
MERCADONA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.8,en-US;q=0.5,en;q=0.3',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0'
}

# ==========================================
# RUTAS DE MERCADONA
# ==========================================

@main.route('/mercadona')
@login_required
def mercadona_store():
    """Página principal de la tienda Mercadona"""
    try:
        # Obtener estadísticas de nuestra lista actual
        current_list_count = mongo.db.lista_compra.count_documents({"comprado": False})
        
        stats = {
            "current_list_items": current_list_count,
            "warehouse": "mad1",  # Madrid por defecto
            "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # Usar datetime.now() correctamente
        }
        
        return render_template("mercadona/store.html", stats=stats, now=datetime.now())
    except Exception as e:
        logger.error(f"Error mercadona_store: {e}")
        return "Error al cargar tienda", 500

@main.route('/mercadona/categories')
@login_required
def mercadona_categories():
    global _mercadona_categories_cache

    # Si hay cache y no ha caducado
    if (_mercadona_categories_cache["data"] and 
        _mercadona_categories_cache["timestamp"] and
        (datetime.now() - _mercadona_categories_cache["timestamp"]).total_seconds() < _CATEGORIES_CACHE_TTL):
        return jsonify(_mercadona_categories_cache["data"])

    try:
        url = f"{MERCADONA_BASE_URL}/categories/?lang=es&wh=mad1"
        response = requests.get(url, headers=MERCADONA_HEADERS, timeout=10)

        if response.status_code != 200:
            # Si falla la API y tenemos cache, devolverlo
            if _mercadona_categories_cache["data"]:
                return jsonify(_mercadona_categories_cache["data"])
            return jsonify({'success': False, 'error': 'Error al conectar con Mercadona'}), 500

        try:
            categories_data = response.json()
        except ValueError:
            if _mercadona_categories_cache["data"]:
                return jsonify(_mercadona_categories_cache["data"])
            return jsonify({'success': False, 'error': 'Respuesta inválida de Mercadona'}), 500

        results = categories_data.get('results')
        if not isinstance(results, list):
            if _mercadona_categories_cache["data"]:
                return jsonify(_mercadona_categories_cache["data"])
            return jsonify({'success': False, 'error': 'Formato de datos inválido'}), 500

        categories = []
        for category in results:
            subcategories_count = len(category.get('categories', []))
            categories.append({
                'id': category.get('id'),
                'name': category.get('name', 'Sin nombre'),
                'subcategories_count': subcategories_count
            })

        data = {'success': True, 'categories': categories}

        # Guardar en cache
        _mercadona_categories_cache["data"] = data
        _mercadona_categories_cache["timestamp"] = datetime.now()

        return jsonify(data)

    except Exception as e:
        logger.error(f"Error mercadona_categories: {e}")
        # Si falla y tenemos cache, devolverlo
        if _mercadona_categories_cache["data"]:
            return jsonify(_mercadona_categories_cache["data"])
        return jsonify({'success': False, 'error': 'Error interno del servidor'}), 500

@main.route('/mercadona/category/<int:category_id>')
@login_required
def mercadona_category_products(category_id):
    #category id +100 e.g initial 12 -Z 112
    category_id += 100
    """Obtener productos de una categoría específica"""
    try:
        # Verificar primero si tenemos datos en caché
        cache_key = f"category_{category_id}"
        cached_data = _mercadona_categories_cache.get(cache_key)
        
        if cached_data and (datetime.now() - cached_data["timestamp"]).total_seconds() < _CATEGORIES_CACHE_TTL:
            return jsonify(cached_data["data"])

        # Configuración de la solicitud con timeout
        session = requests.Session()
        session.headers.update(MERCADONA_HEADERS)
        adapter = requests.adapters.HTTPAdapter(max_retries=3)
        session.mount("https://", adapter)

        # 1. Obtener categorías principales con manejo de timeout
        try:
            main_categories_url = f"{MERCADONA_BASE_URL}/categories/?lang=es&wh=mad1"
            response = session.get(main_categories_url, timeout=10)
            response.raise_for_status()  # Lanza error para códigos 4XX/5XX
            categories_data = response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f"Error obteniendo categorías principales: {str(e)}")
            if cached_data:
                return jsonify(cached_data["data"])
            return jsonify({
                'success': False,
                'error': 'No se pudo obtener la lista de categorías principales',
                'details': str(e)
            }), 500

        # 2. Buscar la categoría solicitada
        category_name = "Categoría"
        all_products = []
        found = False

        for main_category in categories_data.get('results', []):
            if main_category.get('id') == category_id:
                category_name = main_category.get('name', 'Categoría')
                products = get_products_from_subcategory(session, category_id)
                all_products.extend(products)
                found = True
                break

            for subcategory in main_category.get('categories', []):
                if subcategory.get('id') == category_id:
                    category_name = subcategory.get('name', 'Subcategoría')
                    products = get_products_from_subcategory(session, category_id)
                    all_products.extend(products)
                    found = True
                    break

        if not found:
            return jsonify({
                'success': False,
                'error': 'Categoría no encontrada'
            }), 404

        # Preparar respuesta y guardar en caché
        response_data = {
            'success': True,
            'category_name': category_name,
            'products': all_products,
            'total_products': len(all_products)
        }

        _mercadona_categories_cache[cache_key] = {
            "data": response_data,
            "timestamp": datetime.now()
        }

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error en mercadona_category_products: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': 'Error interno del servidor',
            'details': str(e)
        }), 500    


def get_products_from_subcategory(session, subcategory_id):
    """Función auxiliar para obtener productos de una subcategoría con manejo de errores"""
    try:
        subcat_url = f"{MERCADONA_BASE_URL}/categories/{subcategory_id}/?lang=es&wh=mad1"
        response = session.get(subcat_url, timeout=10)
        response.raise_for_status()
        subcat_data = response.json()

        products = []
        
        # Procesar productos directos
        for product in subcat_data.get('products', []):
            try:
                formatted = format_mercadona_product(product)
                products.append(formatted)
            except Exception as e:
                logger.error(f"Error formateando producto: {str(e)}")
                continue
        
        # Procesar sub-subcategorías
        for sub_subcategory in subcat_data.get('categories', []):
            for product in sub_subcategory.get('products', []):
                try:
                    formatted = format_mercadona_product(product)
                    products.append(formatted)
                except Exception as e:
                    logger.error(f"Error formateando producto: {str(e)}")
                    continue
        
        return products
        
    except Exception as e:
        logger.error(f"Error obteniendo subcategoría {subcategory_id}: {str(e)}")
        return []

@main.route('/mercadona/product/<product_id>')
@login_required
def mercadona_product_detail(product_id):
    """Obtener detalles completos de un producto"""
    try:
        url = f"{MERCADONA_BASE_URL}/products/{product_id}/?lang=es&wh=mad1"
        
        response = requests.get(url, headers=MERCADONA_HEADERS, timeout=10)
        
        if response.status_code == 200:
            product_data = response.json()
            
            formatted_product = {
                'id': product_data.get('id'),
                'name': product_data.get('display_name'),
                'brand': product_data.get('brand'),
                'price': product_data.get('price_instructions', {}).get('unit_price', '0'),
                'reference_price': product_data.get('price_instructions', {}).get('reference_price', '0'),
                'size': product_data.get('price_instructions', {}).get('unit_size', 1),
                'size_format': product_data.get('price_instructions', {}).get('size_format', ''),
                'packaging': product_data.get('packaging'),
                'thumbnail': product_data.get('thumbnail'),
                'photos': product_data.get('photos', []),
                'origin': product_data.get('origin'),
                'description': product_data.get('details', {}).get('description', ''),
                'ingredients': product_data.get('nutrition_information', {}).get('ingredients', ''),
                'storage': product_data.get('details', {}).get('storage_instructions', ''),
                'ean': product_data.get('ean'),
                'slug': product_data.get('slug')
            }
            
            return jsonify({
                'success': True,
                'product': formatted_product
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Producto no encontrado'
            }), 404
            
    except Exception as e:
        logger.error(f"Error mercadona_product_detail: {e}")
        return jsonify({
            'success': False,
            'error': 'Error al obtener detalles del producto'
        }), 500



# ==========================================
# FUNCIÓN AUXILIAR
# ==========================================

def format_mercadona_product(product_data):
    """Formatear datos de producto para uso consistente con manejo robusto de None"""
    if not product_data:
        raise ValueError("Datos de producto vacíos")
    
    price_info = product_data.get('price_instructions', {})
    
    # Función auxiliar para manejar strings que pueden ser None
    def safe_string(value, default=''):
        """Convierte None a string vacío y aplica strip si es necesario"""
        if value is None:
            return default
        return str(value).strip() if hasattr(value, 'strip') else str(value)
    
    # Función auxiliar para manejar números que pueden ser None
    def safe_number(value, default=0):
        """Convierte None a número por defecto"""
        if value is None:
            return default
        try:
            return float(value) if '.' in str(value) else int(value)
        except (ValueError, TypeError):
            return default
    
    # Validar campos esenciales
    product_id = product_data.get('id')
    if not product_id:
        raise ValueError("Producto sin ID")
    
    return {
        'id': str(product_id),
        'name': safe_string(product_data.get('display_name'), 'Sin nombre'),
        'slug': safe_string(product_data.get('slug')),
        'thumbnail': safe_string(product_data.get('thumbnail'), '/static/img/default_food.jpg'),
        'packaging': safe_string(product_data.get('packaging')),
        'price': safe_string(price_info.get('unit_price'), '0'),
        'reference_price': safe_string(price_info.get('reference_price'), '0'),
        'size': safe_number(price_info.get('unit_size'), 1),
        'size_format': safe_string(price_info.get('size_format')),
        'bulk_price': safe_string(price_info.get('bulk_price'), '0'),
        'previous_price': safe_string(price_info.get('previous_unit_price')),
        'is_discounted': bool(price_info.get('price_decreased', False)),
        'status': safe_string(product_data.get('status'), 'available'),
        'limit': safe_number(product_data.get('limit'), 999),
        'share_url': safe_string(product_data.get('share_url')),
        'categories': [safe_string(cat.get('name')) for cat in product_data.get('categories', []) if cat and cat.get('name')]
        
    }
    

# REEMPLAZAR la función mercadona_search en routes.py con esta versión mejorada:

@main.route('/mercadona/search')
@login_required
def mercadona_search():
    """Buscar productos en Mercadona con sistema de caché avanzado"""
    try:
        query = request.args.get('q', '').strip()
        
        if not query:
            return jsonify(success=False, error="Query vacía")
        
        if len(query) < 2:
            return jsonify(success=False, error="Query muy corta, mínimo 2 caracteres")
        
        # Filtros adicionales (para futuras mejoras)
        filters = {
            'min_price': request.args.get('min_price'),
            'max_price': request.args.get('max_price'),
            'category': request.args.get('category'),
            'discount_only': request.args.get('discount_only') == 'true'
        }
        # Eliminar filtros vacíos y falsos
        filters = {k: v for k, v in filters.items() if v is not None and v is not False}
        
        # Logging más conciso
        logger.info(f"🔍 Búsqueda: '{query}'" + (f" +filtros" if filters else ""))
        
        # Intentar obtener de caché primero
        cached_result = search_cache.get(query, filters if filters else None)
        if cached_result:
            cached_result['from_cache'] = True
            cached_result['cache_timestamp'] = datetime.now().isoformat()
            logger.info(f"🎯 Cache HIT: '{query}' ({cached_result.get('total_found', 0)} productos)")
            return jsonify(cached_result)
        
        # Si no está en caché, realizar búsqueda
        logger.info(f"💥 Cache MISS: '{query}' - iniciando búsqueda")
        
        # Configurar sesión con retry y timeout
        session_search = requests.Session()
        session_search.headers.update(MERCADONA_HEADERS)
        adapter = requests.adapters.HTTPAdapter(max_retries=2)
        session_search.mount("https://", adapter)
        
        # Obtener todas las categorías
        categories_data = get_mercadona_categories_cached(session_search)
        if not categories_data:
            error_msg = "No se pudo obtener categorías de Mercadona"
            logger.error(f"❌ {error_msg}")
            return jsonify(success=False, error=error_msg), 500
        
        all_products = []
        processed_subcategories = set()
        search_start_time = datetime.now()
        
        # Normalizar query para búsqueda más flexible
        query_normalized = query.lower().strip()
        query_words = query_normalized.split()
        
        # Contadores para estadísticas
        total_subcategories = sum(len(cat.get('categories', [])) for cat in categories_data.get('results', []))
        processed_count = 0
        error_count = 0
        success_count = 0
        
        # Buscar en cada categoría y subcategoría
        for category in categories_data.get('results', []):
            category_name = category.get('name', '')
            
            for subcat in category.get('categories', []):
                subcat_id = subcat.get('id')
                subcat_name = subcat.get('name', '')
                processed_count += 1
                
                # Evitar procesar la misma subcategoría múltiples veces
                if subcat_id in processed_subcategories:
                    continue
                processed_subcategories.add(subcat_id)
                
                if subcat_id:
                    try:
                        # Usar caché también para subcategorías individuales
                        subcat_products = get_subcategory_products_cached(session_search, subcat_id, subcat_name)
                        
                        if subcat_products:  # Solo procesar si hay productos
                            success_count += 1
                            products_found_in_subcat = 0
                            
                            for product in subcat_products:
                                if is_product_match(product, query_normalized, query_words):
                                    try:
                                        formatted_product = format_mercadona_product(product)
                                        formatted_product['category'] = category_name
                                        formatted_product['subcategory'] = subcat_name
                                        
                                        # Aplicar filtros si existen
                                        if apply_filters(formatted_product, filters):
                                            all_products.append(formatted_product)
                                            products_found_in_subcat += 1
                                            
                                    except Exception as e:
                                        # Solo log en debug para errores de formateo
                                        if current_app.debug:
                                            logger.debug(f"Error formateando producto {product.get('id', 'unknown')}: {str(e)}")
                                        continue
                        
                    except Exception as e:
                        error_count += 1
                        # Solo log de errores críticos, no warnings
                        if "403" not in str(e) and "NoneType" not in str(e):
                            logger.warning(f"Error procesando subcategoría {subcat_id}: {str(e)}")
                        continue
        
        # Eliminar duplicados basándose en el ID del producto
        unique_products = []
        seen_ids = set()
        for product in all_products:
            if product['id'] not in seen_ids:
                seen_ids.add(product['id'])
                unique_products.append(product)
        
        # Ordenar resultados por relevancia
        sorted_products = sort_products_by_relevance(unique_products, query_normalized, query_words)
        
        search_duration = (datetime.now() - search_start_time).total_seconds()
        
        # Preparar resultado
        result = {
            'success': True,
            'products': sorted_products,
            'query': query,
            'total_found': len(sorted_products),
            'search_terms': query_words,
            'search_duration_seconds': round(search_duration, 3),
            'from_cache': False,
            'applied_filters': filters,
            'searched_subcategories': len(processed_subcategories),
            'stats': {
                'total_subcategories_processed': processed_count,
                'successful_subcategories': success_count,
                'error_subcategories': error_count
            }
        }
        
        # Guardar en caché siempre
        search_cache.set(query, result, filters if filters else None)
        
        # Log final conciso
        logger.info(f"✅ '{query}': {len(sorted_products)} productos, {search_duration:.2f}s, {success_count}/{processed_count} subcategorías OK")
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"❌ Error crítico en búsqueda '{query}': {str(e)}")
        return jsonify({
            'success': False,
            'error': 'Error interno en la búsqueda',
            'details': str(e) if current_app.debug else "Error interno del servidor"
        }), 500

def get_mercadona_categories_cached(session):
    """Obtener categorías con caché (usando la caché global existente)"""
    global _mercadona_categories_cache
    
    # Si hay cache y no ha caducado
    if (_mercadona_categories_cache.get("data") and 
        _mercadona_categories_cache.get("timestamp") and
        (datetime.now() - _mercadona_categories_cache["timestamp"]).total_seconds() < _CATEGORIES_CACHE_TTL):
        
        cached_data = _mercadona_categories_cache["data"]
        if isinstance(cached_data, dict) and cached_data.get('categories_data'):
            return cached_data['categories_data']
    
    try:
        logger.info("🔄 Obteniendo categorías...")
        url = f"{MERCADONA_BASE_URL}/categories/?lang=es&wh=mad1"
        response = session.get(url, timeout=15)
        
        if response.status_code == 200:
            categories_data = response.json()
            
            # Validar que los datos son correctos
            if not categories_data.get('results'):
                logger.error("❌ Respuesta de categorías sin 'results'")
                return None
            
            # Guardar en cache con estructura mejorada
            _mercadona_categories_cache["data"] = {
                'success': True,
                'categories': [
                    {
                        'id': cat.get('id'),
                        'name': cat.get('name', 'Sin nombre'),
                        'subcategories_count': len(cat.get('categories', []))
                    }
                    for cat in categories_data.get('results', [])
                ],
                'categories_data': categories_data
            }
            _mercadona_categories_cache["timestamp"] = datetime.now()
            
            logger.info(f"✅ {len(categories_data.get('results', []))} categorías obtenidas")
            return categories_data
        else:
            logger.error(f"❌ Error HTTP {response.status_code} obteniendo categorías")
        
    except Exception as e:
        logger.error(f"❌ Error crítico obteniendo categorías: {str(e)}")
    
    return None

def get_subcategory_products_cached(session, subcat_id, subcat_name):
    """Obtener productos de subcategoría con caché y manejo robusto de errores"""
    cache_key = f"subcat_{subcat_id}"
    
    # Verificar si está en la caché global
    cached_data = _mercadona_categories_cache.get(cache_key)
    if cached_data and (datetime.now() - cached_data["timestamp"]).total_seconds() < _CATEGORIES_CACHE_TTL:
        return cached_data["data"]
    
    try:
        subcat_url = f"{MERCADONA_BASE_URL}/categories/{subcat_id}/?lang=es&wh=mad1"
        response = session.get(subcat_url, timeout=8)
        
        if response.status_code == 200:
            subcat_data = response.json()
            products = []
            
            # Procesar productos directos
            for product in subcat_data.get('products', []):
                if product and isinstance(product, dict):  # Validar que el producto no es None
                    products.append(product)
            
            # Procesar sub-subcategorías
            for sub_subcat in subcat_data.get('categories', []):
                if sub_subcat and isinstance(sub_subcat, dict):
                    for product in sub_subcat.get('products', []):
                        if product and isinstance(product, dict):
                            products.append(product)
            
            # Guardar en caché solo si hay productos válidos
            if products:
                _mercadona_categories_cache[cache_key] = {
                    "data": products,
                    "timestamp": datetime.now()
                }
            
            return products
            
        elif response.status_code == 403:
            # 403 es común en Mercadona, no es un error crítico
            return []
        else:
            # Solo log en debug para otros errores HTTP
            if current_app.debug:
                logger.debug(f"HTTP {response.status_code} para subcategoría {subcat_id}")
            return []
        
    except Exception as e:
        # Solo log errores no relacionados con None o 403
        if current_app.debug and "NoneType" not in str(e):
            logger.debug(f"Error subcategoría {subcat_id}: {str(e)}")
        return []
    
    try:
        subcat_url = f"{MERCADONA_BASE_URL}/categories/{subcat_id}/?lang=es&wh=mad1"
        response = session.get(subcat_url, timeout=8)
        
        if response.status_code == 200:
            subcat_data = response.json()
            products = []
            
            # Procesar productos directos
            for product in subcat_data.get('products', []):
                products.append(product)
            
            # Procesar sub-subcategorías
            for sub_subcat in subcat_data.get('categories', []):
                for product in sub_subcat.get('products', []):
                    products.append(product)
            
            # Guardar en caché
            _mercadona_categories_cache[cache_key] = {
                "data": products,
                "timestamp": datetime.now()
            }
            
            return products
        
    except Exception as e:
        logger.error(f"Error obteniendo subcategoría {subcat_id}: {str(e)}")
    
    return []


def apply_filters(product, filters):
    """Aplicar filtros al producto con validación robusta"""
    if not filters or not product:
        return True
    
    try:
        # Filtro de precio mínimo
        if filters.get('min_price'):
            try:
                price = float(product.get('price', '0'))
                if price < float(filters['min_price']):
                    return False
            except (ValueError, TypeError):
                pass
        
        # Filtro de precio máximo
        if filters.get('max_price'):
            try:
                price = float(product.get('price', '999'))
                if price > float(filters['max_price']):
                    return False
            except (ValueError, TypeError):
                pass
        
        # Filtro de categoría
        if filters.get('category'):
            category = product.get('category', '')
            if category and filters['category'].lower() not in category.lower():
                return False
        
        # Filtro solo productos con descuento
        if filters.get('discount_only'):
            if not product.get('is_discounted', False):
                return False
        
        return True
        
    except Exception as e:
        if current_app.debug:
            logger.debug(f"Error aplicando filtros: {str(e)}")
        return True

@main.route('/api/mercadona/cache/stats', methods=['GET'])
@login_required
def get_cache_stats():
    """Obtener estadísticas de la caché de búsquedas"""
    try:
        stats = search_cache.get_stats()
        
        # Añadir estadísticas de la caché de categorías
        categories_cache_size = len(_mercadona_categories_cache)
        
        return jsonify({
            "success": True,
            "search_cache": stats,
            "categories_cache_entries": categories_cache_size,
            "categories_cache_ttl_seconds": _CATEGORIES_CACHE_TTL,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error obteniendo estadísticas de caché: {str(e)}")
        return jsonify({"error": "Error obteniendo estadísticas"}), 500


@main.route('/api/mercadona/cache/clear', methods=['POST'])
@login_required
def clear_search_cache():
    """Limpiar caché de búsquedas (solo admin)"""
    try:
        # Solo admin puede limpiar caché
        if session.get('user') != 'Joso':
            return jsonify({"error": "Permisos insuficientes"}), 403
        
        # Limpiar caché de búsquedas
        search_cache.clear()
        
        # Limpiar caché de categorías
        global _mercadona_categories_cache
        _mercadona_categories_cache.clear()
        
        logger.info(f"🧹 Caché limpiada por: {session.get('user')}")
        
        return jsonify({
            "success": True,
            "message": "Caché limpiada correctamente",
            "cleared_by": session.get('user'),
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"Error limpiando caché: {str(e)}")
        return jsonify({"error": "Error limpiando caché"}), 500


def is_product_match(product, query_normalized, query_words):
    """
    Determinar si un producto coincide con la búsqueda usando coincidencias parciales flexibles
    """
    # Validar que el producto no es None y tiene los campos necesarios
    if not product or not isinstance(product, dict):
        return False
    
    # Obtener campos con validación de None
    product_name = product.get('display_name') or ""
    product_brand = product.get('brand') or ""
    product_packaging = product.get('packaging') or ""
    
    # Convertir a lowercase de forma segura
    product_name = product_name.lower() if product_name else ""
    product_brand = product_brand.lower() if product_brand else ""
    product_packaging = product_packaging.lower() if product_packaging else ""
    
    # Texto completo del producto para búsqueda
    full_product_text = f"{product_name} {product_brand} {product_packaging}".strip()
    
    # Si no hay texto para buscar, no es una coincidencia
    if not full_product_text:
        return False
    
    # Método 1: Coincidencia directa de la query completa
    if query_normalized in full_product_text:
        return True
    
    # Método 2: Todas las palabras de la query deben estar presentes
    if len(query_words) > 1:
        words_found = 0
        for word in query_words:
            if len(word) >= 2 and word in full_product_text:
                words_found += 1
        
        # Si encontramos al menos el 70% de las palabras, es una coincidencia
        if words_found >= len(query_words) * 0.7:
            return True
    
    # Método 3: Coincidencia parcial inteligente para palabras individuales
    for word in query_words:
        if len(word) >= 3:
            product_words = full_product_text.split()
            for prod_word in product_words:
                if prod_word.startswith(word) or word in prod_word:
                    return True
    
    return False


def sort_products_by_relevance(products, query_normalized, query_words):
    """
    Ordenar productos por relevancia de la búsqueda con validación robusta
    """
    def calculate_relevance_score(product):
        # Validar producto
        if not product or not isinstance(product, dict):
            return 0
        
        product_name = (product.get('name') or "").lower()
        product_brand = (product.get('brand') or "").lower()
        
        score = 0
        
        # Coincidencia exacta en el nombre = máxima puntuación
        if query_normalized == product_name:
            score += 100
        
        # Coincidencia al inicio del nombre
        elif product_name.startswith(query_normalized):
            score += 80
        
        # Query completa contenida en el nombre
        elif query_normalized in product_name:
            score += 60
        
        # Puntuación por palabras individuales
        for word in query_words:
            if len(word) >= 2:
                if word in product_name:
                    score += 10
                if word in product_brand:
                    score += 5
        
        # Bonus por productos más populares
        try:
            price = float(product.get('price', '999'))
            if price < 5:
                score += 5
            elif price < 10:
                score += 2
        except (ValueError, TypeError):
            pass
        
        # Bonus por productos con descuento
        if product.get('is_discounted', False):
            score += 3
        
        return score
    
    # Ordenar por puntuación descendente
    return sorted(products, key=calculate_relevance_score, reverse=True)


@main.route('/api/add_shopping_item', methods=['POST'])
@login_required
def add_shopping_item():
    """Añadir un producto individual a la lista de compra desde Mercadona"""
    try:
        data = request.get_json()
        
        # Validar datos requeridos
        product_id = data.get('product_id')
        name = data.get('name', '').strip()
        quantity = int(data.get('quantity', 1))
        
        if not product_id or not name:
            return jsonify({"error": "Faltan campos obligatorios"}), 400
        
        if quantity < 1 or quantity > 99:
            return jsonify({"error": "Cantidad inválida (1-99)"}), 400
        
        # Datos opcionales con manejo seguro
        packaging = data.get('packaging', '').strip()
        price = str(data.get('price', '0')).strip()  # Convertir a string primero
        size = str(data.get('size', '')).strip() if data.get('size') is not None else ''
        size_format = data.get('size_format', '').strip()
        source = data.get('source', 'mercadona')
        
        # Verificar si el producto ya existe en la lista
        existing_item = mongo.db.lista_compra.find_one({
            "$or": [
                {"product_id": product_id},
                {"nombre": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
            ],
            "comprado": False
        })
        
        if existing_item:
            # Actualizar cantidad del producto existente
            new_quantity = int(existing_item.get("cantidad", 1)) + quantity
            new_quantity = min(new_quantity, 99)  # Límite máximo
            
            mongo.db.lista_compra.update_one(
                {"_id": existing_item["_id"]},
                {
                    "$set": {
                        "cantidad": str(new_quantity),
                        "updated_by": session.get("user"),
                        "updated_at": datetime.now(),
                        "price": price,  # Actualizar precio si viene
                        "source": source
                    }
                }
            )
            
            return jsonify({
                "success": True,
                "action": "updated",
                "new_quantity": new_quantity,
                "message": f"Cantidad actualizada: {new_quantity}"
            })
        else:
            # Crear nuevo producto en la lista
            new_item = {
                "product_id": product_id,
                "nombre": name,
                "cantidad": str(quantity),
                "unidad": size_format if size_format else "",
                "packaging": packaging,
                "price": price,
                "size": size,
                "comprado": False,
                "source": source,
                "created_by": session.get("user"),
                "created_at": datetime.now(),
                "added_from_mercadona": True
            }
            
            result = mongo.db.lista_compra.insert_one(new_item)
            
            # Notificar a todos los usuarios
            try:
                from app.api import send_push_to_all
                send_push_to_all(
                    title="🛒 Producto desde Mercadona",
                    body=f"{session.get('user')} añadió: {name}",
                    url="/lista_compra"
                )
            except ImportError:
                pass  # Si no existe la función de push, continuar sin error
            
            return jsonify({
                "success": True,
                "action": "added",
                "item_id": str(result.inserted_id),
                "message": f"{name} añadido a la lista"
            })
    
    except ValueError as e:
        logger.error(f"Error de validación en add_shopping_item: {e}")
        return jsonify({"error": "Datos inválidos"}), 400
    except Exception as e:
        logger.error(f"Error add_shopping_item: {e}")
        return jsonify({"error": "Error al añadir producto"}), 500     
    
  


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

